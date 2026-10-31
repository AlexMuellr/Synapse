"""Card suggestions from notebook pages (prompt, parsing, images). Synthetic data only."""
import base64
import importlib.util
import json
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
package = types.ModuleType('synapse_cards_pkg')
package.__path__ = [str(ROOT)]
sys.modules['synapse_cards_pkg'] = package
def load(name):
    spec = importlib.util.spec_from_file_location(f'synapse_cards_pkg.{name}', ROOT / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[f'synapse_cards_pkg.{name}'] = module
    spec.loader.exec_module(module)
    return module
nt = load('notebook_text')
nc = load('notebook_cards')

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 20
PNG_URI = 'data:image/png;base64,' + base64.b64encode(PNG).decode()
JPG_URI = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xff\xe0' + b'1' * 10).decode()


class ImageMarkerTests(unittest.TestCase):
    def test_data_image_validation_uses_the_bytes(self):
        self.assertEqual(nt.decode_data_image(PNG_URI), (PNG, 'png'))
        self.assertEqual(nt.decode_data_image(JPG_URI)[1], 'jpg')
        fake = 'data:image/png;base64,' + base64.b64encode(b'<svg onload=alert(1)>').decode()
        for bad in (fake, 'data:image/svg+xml;base64,PHN2Zz4=', 'https://x.org/a.png', 'data:image/png;base64,!!!', '', None):
            self.assertIsNone(nt.decode_data_image(bad), bad)
        self.assertIsNone(nt.decode_data_image(PNG_URI, max_bytes=5))

    def test_markers_are_numbered_deduplicated_and_local(self):
        images = nt.ImageCollector()
        body = json.dumps({'pages': [{'id': 'a', 'title': 'A', 'blocks': [
            {'type': 'text', 'html': f'Heart <img src="{PNG_URI}"> valve'},
            {'type': 'text', 'html': f'<img src="{JPG_URI}"><img src="{PNG_URI}">'},
            {'type': 'text', 'html': '<img src="https://x.org/a.png">'},
        ]}]})
        context = nt.pages_to_context(body, ['a'], images=images)
        self.assertIn('Heart [img1] valve', context)
        self.assertIn('[img2] [img1]', context)
        self.assertIn('[image]', context)
        self.assertNotIn('base64', context)
        self.assertEqual(set(images.images), {'img1', 'img2'})
        self.assertEqual(images.referenced('only [img2] here'), {'img2': JPG_URI})
        self.assertEqual(nt.html_to_text(f'<img src="{PNG_URI}">'), '[image]')

    def test_image_limit(self):
        images = nt.ImageCollector(limit=1)
        self.assertEqual(images.marker(PNG_URI).strip(), '[img1]')
        self.assertEqual(images.marker(JPG_URI), '[image]')


class ParseTests(unittest.TestCase):
    def test_parses_fenced_and_chatty_replies(self):
        reply = 'Sure!\n```json\n[{"front":"Q1","back":"A1","image":"img1"},{"front":"Q2","back":"A2","image":null}]\n```\nDone'
        cards = nc.parse_cards(reply, ['img1'])
        self.assertEqual(cards, [{'front': 'Q1', 'back': 'A1', 'image': 'img1'}, {'front': 'Q2', 'back': 'A2', 'image': None}])
        wrapped = nc.parse_cards('{"cards":[{"front":"Q","back":"A"}]}')
        self.assertEqual(wrapped, [{'front': 'Q', 'back': 'A', 'image': None}])

    def test_drops_junk_and_invented_images(self):
        reply = json.dumps([{'front': '', 'back': 'x'}, {'front': 5, 'back': 'x'}, 'text', None,
                            {'front': ' Q ', 'back': ' A ', 'image': '[img9]'},
                            {'front': 'Q', 'back': 'A', 'image': ' [img1] '}])
        cards = nc.parse_cards(reply, ['img1'])
        self.assertEqual([c['image'] for c in cards], [None, 'img1'])
        self.assertEqual(cards[0]['front'], 'Q')
        self.assertEqual(nc.parse_cards('no json at all'), [])
        self.assertEqual(nc.parse_cards('[1, 2, 3]'), [])
        many = json.dumps([{'front': str(i), 'back': 'b'} for i in range(100)])
        self.assertEqual(len(nc.parse_cards(many)), nc.MAX_CARDS)

    def test_prompt_mentions_markers_only_when_images_exist(self):
        with_images = nc.build_prompt('CTX', 'in German', True)
        self.assertIn('[img1]', with_images)
        self.assertIn('in German', with_images)
        self.assertIn('<notebook_pages>\nCTX\n</notebook_pages>', with_images)
        self.assertIn('no images', nc.build_prompt('CTX', '', False))


class PrepareNotesTests(unittest.TestCase):
    def test_text_is_escaped_and_formulas_survive(self):
        [note] = nc.prepare_notes([{'front': 'What is <b>x</b>?\nSecond', 'back': r'\(a<b\) & more', 'image': None}], {})
        self.assertEqual(note['front'], 'What is &lt;b&gt;x&lt;/b&gt;?<br>Second')
        self.assertEqual(note['back'], r'\(a&lt;b\) &amp; more')
        self.assertIsNone(note['media'])

    def test_image_goes_to_the_chosen_side_from_the_local_cache(self):
        cards = [{'front': 'F', 'back': 'B', 'image': 'img1', 'placement': 'back'},
                 {'front': 'F', 'back': '', 'image': 'img1', 'placement': 'front'}]
        first, second = nc.prepare_notes(cards, {'img1': PNG_URI})
        name = first['media'][0]
        self.assertRegex(name, r'^synapse_notes_[0-9a-f]{16}\.png$')
        self.assertEqual(first['media'][1], PNG)
        self.assertEqual(first['back'], f'B<br><img src="{name}">')
        self.assertEqual(second['front'], f'F<br><img src="{name}">')
        self.assertEqual(second['back'], '')
        self.assertEqual(second['media'][0], name, 'same picture, same file')

    def test_rejects_malformed_input(self):
        good = {'front': 'F', 'back': 'B'}
        for bad in ([], 'x', [5], [{'front': '  ', 'back': 'B'}], [{'front': 'F'}],
                    [{**good, 'image': 'img2', 'placement': 'back'}],
                    [{**good, 'image': 'img1', 'placement': 'top'}],
                    [good] * (nc.MAX_CARDS + 1)):
            with self.assertRaises(ValueError, msg=repr(bad)):
                nc.prepare_notes(bad, {'img1': PNG_URI})
        with self.assertRaises(ValueError):
            nc.prepare_notes([{**good, 'image': 'img1'}], {'img1': 'data:image/png;base64,AAAA'})


if __name__ == '__main__':
    unittest.main()
