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

    def test_legacy_and_invalid_data(self):
        legacy = json.dumps([{'type': 'text', 'html': 'old'}])
        self.assertEqual(nt.pages_to_context(legacy, ['legacy']), '<notebook_page title="My Notes">\nold\n</notebook_page>')
        self.assertEqual(nt.parse_pages('not json'), [])
        self.assertEqual(nt.blocks_to_text([None, 5, {'type': 'bullet', 'html': 'ok', 'indent': 'x'}]), '- ok')


if __name__ == '__main__':
    unittest.main()
