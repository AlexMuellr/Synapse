"""Allow-list sanitizer for Card Creator fields. Synthetic data only."""
import base64
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
package = types.ModuleType('synapse_cardhtml_pkg')
package.__path__ = [str(ROOT)]
sys.modules['synapse_cardhtml_pkg'] = package
def load(name):
    spec = importlib.util.spec_from_file_location(f'synapse_cardhtml_pkg.{name}', ROOT / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[f'synapse_cardhtml_pkg.{name}'] = module
    spec.loader.exec_module(module)
    return module
load('notebook_text'); load('notebook_cards')
ch = load('card_html')
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 20


class SanitizeTests(unittest.TestCase):
    def clean(self, source, images=None):
        return ch.sanitize(source, images)[0]

    def test_keeps_what_every_anki_client_renders(self):
        source = '<h1>A</h1><h3>B</h3><b>x</b><i>y</i><u>z</u><s>s</s><sub>1</sub><sup>2</sup><br><ul><li>a<ol start="4" type="a"><li>b</li></ol></li></ul>'
        self.assertEqual(self.clean(source), source)

    def test_aliases_and_unknown_tags(self):
        self.assertEqual(self.clean('<strong>a</strong><em>b</em><h5>c</h5><font color="red">d</font><span style="x">e</span>'),
                         '<b>a</b><i>b</i><h3>c</h3>de')

    def test_dangerous_content_is_removed(self):
        for source, expected in (
            ('<script>alert(1)</script>x', 'x'),
            ('<style>*{}</style>x', 'x'),
            ('<iframe src="http://e"><b>in</b></iframe>x', 'x'),
            ('<b onclick="e()" style="color:red" class="c">x</b>', '<b>x</b>'),
            ('<a href="javascript:e()">x</a>', 'x'),
            ('<img src="http://e/x.png" onerror="e()">x', 'x'),
            ('<img src="data:image/png;base64,AAAA">x', 'x'),
            ('<svg><script>e()</script></svg>x', 'x'),
            ('<ol start="999999999" type="z"><li>a</li></ol>', '<ol><li>a</li></ol>'),
            ('<li>loose</li>', '<div>loose</div>'),
        ):
            self.assertEqual(self.clean(source), expected, source)

    def test_text_is_escaped_and_formulas_survive(self):
        self.assertEqual(self.clean('a &lt; b &amp; \\(x &lt; y\\) <3'), 'a &lt; b &amp; \\(x &lt; y\\) &lt;3')
        self.assertEqual(self.clean('\\[\\frac{a}{b}\\]'), '\\[\\frac{a}{b}\\]')

    def test_unclosed_and_stray_tags_are_balanced(self):
        self.assertEqual(self.clean('<b>x<i>y'), '<b>x<i>y</i></b>')
        self.assertEqual(self.clean('x</b></div>y'), 'xy')

    def test_empty_blocks_around_the_content_are_dropped(self):
        self.assertEqual(self.clean('<div><br></div><div>x</div><div><br></div><br>'), '<div>x</div>')
        self.assertEqual(self.clean('​ x  '), 'x')

    def test_nesting_depth_is_capped(self):
        deep = '<ul><li>' * 40 + 'x' + '</li></ul>' * 40
        self.assertLessEqual(self.clean(deep).count('<ul>'), ch.MAX_DEPTH)

    def test_pictures_become_media(self):
        html, media = ch.sanitize('a<img data-img="3">b<img data-img="3">', {'3': (PNG, 'png')})
        [(name, data)] = media
        self.assertEqual(html, f'a<img src="{name}">b<img src="{name}">')
        self.assertEqual(data, PNG)
        with self.assertRaises(ValueError):
            ch.sanitize('<img data-img="4">', {'3': (PNG, 'png')})
        self.assertEqual(ch.sanitize('<img data-img="4">')[0], '<img data-img="4">', 'placeholders stay when no pictures are given')
        self.assertEqual(ch.sanitize('<img data-img="x">', {})[0], '')

    def test_helpers(self):
        self.assertEqual(ch.image_ids('<img data-img="1"> <img data-img="22">'), ['1', '22'])
        self.assertTrue(ch.has_content('<img data-img="1">'))
        self.assertTrue(ch.has_content('<div>a</div>'))
        self.assertFalse(ch.has_content('<div><br></div> &nbsp; '.replace('&nbsp;', '')))
        self.assertEqual(ch.plain_text('<h1>Hi</h1><ul><li>a</li><li>b</li></ul>').split(), ['Hi', 'a', 'b'])


if __name__ == '__main__':
    unittest.main()
