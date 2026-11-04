import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('notebook_text', ROOT / 'notebook_text.py')
nt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nt)


class NotebookTextTests(unittest.TestCase):
    def test_lists_nesting_numbering_and_todo(self):
        text = nt.blocks_to_text([
            {'type': 'numbered', 'html': 'One'},
            {'type': 'numbered', 'html': 'Sub', 'indent': 1},
            {'type': 'numbered', 'html': 'Sub 2', 'indent': 1},
            {'type': 'numbered', 'html': 'Two'},
            {'type': 'bullet', 'html': 'Dot', 'indent': 1},
            {'type': 'todo', 'html': 'Task', 'checked': True},
            {'type': 'text', 'html': 'break'},
            {'type': 'numbered', 'html': 'Again'},
        ])
        self.assertEqual(text.split('\n'), [
            '1. One', '  a. Sub', '  b. Sub 2', '2. Two', '  - Dot', '- [x] Task', 'break', '1. Again'])

    def test_numbered_lists_can_start_anywhere(self):
        text = nt.blocks_to_text([
            {'type': 'numbered', 'html': 'a', 'start': 3},
            {'type': 'numbered', 'html': 'b'},
            {'type': 'numbered', 'html': 'c', 'indent': 1, 'start': 2},
            {'type': 'numbered', 'html': 'd', 'indent': 1},
            {'type': 'numbered', 'html': 'e', 'start': True},
            {'type': 'text', 'html': 'x'},
            {'type': 'numbered', 'html': 'f', 'start': 'oops'},
        ])
        self.assertEqual(text.split('\n'), ['3. a', '4. b', '  b. c', '  c. d', '5. e', 'x', '1. f'])

    def test_inline_formulas_become_anki_delimiters(self):
        formula = '<span class="math-inline" data-latex="%s"></span>'
        text = nt.blocks_to_text([
            {'type': 'text', 'html': 'Energy ' + formula % 'E=mc^2' + ' and ' + formula % r'\frac{1}{2}' + ' done'},
            {'type': 'bullet', 'html': formula % 'x'},
            {'type': 'text', 'html': '<span class="math-inline" data-latex="y"><span class="katex">rendered <b>junk</b></span></span> after'},
            {'type': 'text', 'html': '<span class="other">kept</span> ' + formula % 'z' + '<br>next'},
        ])
        self.assertEqual(text.split('\n'), ['Energy \\(E=mc^2\\) and \\(\\frac{1}{2}\\) done', '- \\(x\\)', '\\(y\\) after', 'kept \\(z\\)', 'next'])

    def test_formatting_is_dropped_but_text_and_links_kept(self):
        text = nt.blocks_to_text([
            {'type': 'text', 'html': 'a <span style="color: rgb(1, 2, 3);">red</span> <b>bold</b>&nbsp;<a href="https://x.org">link</a><br>next'},
            {'type': 'text', 'html': '<a href="javascript:alert(1)">bad</a><img src="data:image/png;base64,AA">'},
        ])
        self.assertEqual(text, 'a red bold link (https://x.org)\nnext\nbad[image]')

    def test_latex_headings_quote_code_toggle_table(self):
        text = nt.blocks_to_text([
            {'type': 'h2', 'html': 'Title'},
            {'type': 'latex', 'html': '', 'latex': 'E=mc^2'},
            {'type': 'quote', 'html': 'q'},
            {'type': 'code', 'html': 'x = 1'},
            {'type': 'toggle', 'html': 'More', 'body': 'hidden <i>text</i>'},
            {'type': 'table', 'html': '<tbody><tr><td>A</td><td>B</td></tr><tr><td>C</td><td></td></tr></tbody>'},
            {'type': 'divider', 'html': ''},
            {'type': 'future-widget', 'html': 'kept'},
        ])
        self.assertEqual(text.split('\n'), [
            '## Title', '\\[ E=mc^2 \\]', '> q', '```', 'x = 1', '```', '▸ More', '  hidden text',
            '| A | B |', '| C |', '---', 'kept'])

    def test_pages_outline_selection_and_limits(self):
        body = json.dumps({'pages': [
            {'id': 'a', 'title': 'Alpha', 'icon': '📄', 'blocks': [{'type': 'text', 'html': 'x ' * 100}]},
            {'id': 'b', 'title': 'Be "ta"', 'parentPageId': 'a', 'blocks': [{'type': 'text', 'html': 'beta'}]},
            {'id': 'c', 'title': 'Gamma', 'blocks': [{'type': 'text', 'html': 'gamma'}]},
        ]})
        self.assertEqual([p['id'] for p in nt.page_outline(body)], ['a', 'b', 'c'])
        self.assertEqual(nt.page_outline(body)[1]['parentPageId'], 'a')
        context = nt.pages_to_context(body, ['b', 'zzz'])
        self.assertEqual(context, '<notebook_page title="Be &quot;ta&quot;">\nbeta\n</notebook_page>')
        clipped = nt.pages_to_context(body, ['a'], per_page=20)
        self.assertIn('[…]', clipped)
        omitted = nt.pages_to_context(body, ['a', 'b', 'c'], total=120)
        self.assertIn('omitted', omitted)

    def test_notebook_outline_has_sections_and_pages(self):
        body = json.dumps({'folders': [{'id': 'f1', 'name': 'Anatomie', 'open': True}, 'junk'],
                           'pages': [{'id': 'a', 'title': 'A', 'folderId': 'f1', 'blocks': []}]})
        outline = nt.notebook_outline(body)
        self.assertEqual(outline['folders'], [{'id': 'f1', 'name': 'Anatomie'}])
        self.assertEqual(outline['pages'][0]['folderId'], 'f1')
        self.assertEqual(nt.notebook_outline('[]')['pages'][0]['id'], 'legacy')
        self.assertEqual(nt.notebook_outline('nope'), {'folders': [], 'pages': []})

    def test_legacy_and_invalid_data(self):
        legacy = json.dumps([{'type': 'text', 'html': 'old'}])
        self.assertEqual(nt.pages_to_context(legacy, ['legacy']), '<notebook_page title="My Notes">\nold\n</notebook_page>')
        self.assertEqual(nt.parse_pages('not json'), [])
        self.assertEqual(nt.blocks_to_text([None, 5, {'type': 'bullet', 'html': 'ok', 'indent': 'x'}]), '- ok')


if __name__ == '__main__':
    unittest.main()
