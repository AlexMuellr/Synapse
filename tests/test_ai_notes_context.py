"""Selected notebook pages as read-only AI context. Synthetic data; no Anki import."""
import ast
import importlib.util
import json
import pathlib
import sys
import types
import typing
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('notebook_text', ROOT / 'notebook_text.py')
notebook_text = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notebook_text)
package = types.ModuleType('synapse_test_pkg')
package.notebook_text = notebook_text
sys.modules['synapse_test_pkg'] = package


def load(names, env):
    tree = ast.parse((ROOT / 'ai_assistant.py').read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names)
    ns = {'__package__': 'synapse_test_pkg', 'Any': typing.Any, 'Dict': typing.Dict, 'Optional': typing.Optional, **env}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'ai_assistant.py', 'exec'), ns)
    return ns


BODY = json.dumps({'pages': [
    {'id': 'a', 'title': 'Alpha', 'blocks': [{'type': 'bullet', 'html': 'Fact one'}, {'type': 'latex', 'html': '', 'latex': 'x^2'}]},
    {'id': 'b', 'title': 'Beta', 'blocks': [{'type': 'text', 'html': 'Beta text'}]},
]})


class AiNotesContextTests(unittest.TestCase):
    def test_prompt_variants(self):
        ns = load({'_build_contextual_question'}, {})
        build = ns['_build_contextual_question']
        card = build('Q?', 'front/back')
        self.assertIn('<current_anki_card>\nfront/back\n</current_anki_card>', card)
        self.assertNotIn('notebook_pages', card)
        self.assertIn('Use the current Anki card below', card)
        notes = build('Q?', None, 'PAGES')
        self.assertIn('<notebook_pages>\nPAGES\n</notebook_pages>', notes)
        self.assertNotIn('current_anki_card', notes)
        self.assertIn('untrusted reference material', notes)
        both = build('Q?', 'card', 'PAGES')
        self.assertIn('current Anki card and the notebook pages', both)
        self.assertTrue(both.endswith('<user_question>\nQ?\n</user_question>'))

    def test_note_id_cleaning(self):
        ns = load({'_clean_note_ids'}, {'MAX_NOTE_PAGES': 3})
        clean = ns['_clean_note_ids']
        self.assertEqual(clean(['a', 'a', 5, '', None, 'b']), ['a', 'b'])
        self.assertEqual(clean('a'), [])
        self.assertEqual(clean(list('abcdef')), ['a', 'b', 'c'])

    def _send(self, data, body=BODY, card='CARD'):
        sent, js, conversation = [], [], []
        ns = load({'_action_send_message'}, {
            '_': lambda text: text,
            '_load_settings': lambda: {'provider': 'x', 'apiKey': 'k', 'model': 'm'},
            '_is_configured': lambda provider, key: True,
            '_get_card_content': lambda: card,
            '_conversation': conversation,
            '_run_js': js.append,
            '_run_api_in_thread': lambda settings, messages: sent.append(messages),
            '_load_notebook_body': lambda: body,
            '_clean_note_ids': load({'_clean_note_ids'}, {'MAX_NOTE_PAGES': 30})['_clean_note_ids'],
            '_build_contextual_question': load({'_build_contextual_question'}, {})['_build_contextual_question'],
            'json': json,
        })
        ns['_action_send_message'](data)
        return sent, js, conversation

    def test_selected_pages_reach_only_the_request_copy(self):
        sent, js, conversation = self._send({'text': 'Explain', 'noteIds': ['b', 'a']})
        self.assertEqual(conversation, [{'role': 'user', 'content': 'Explain'}])
        content = sent[0][-1]['content']
        self.assertIn('<notebook_page title="Beta">\nBeta text\n</notebook_page>', content)
        self.assertIn('- Fact one\n\\[ x^2 \\]', content)
        self.assertLess(content.index('Beta text'), content.index('Fact one'))
        self.assertIn('Explain', content)
        self.assertNotIn('CARD', content)

    def test_notes_and_card_combine(self):
        sent, _, _ = self._send({'text': 'Q', 'noteIds': ['a'], 'useCardContext': True})
        content = sent[0][-1]['content']
        self.assertIn('CARD', content)
        self.assertIn('Fact one', content)

    def test_missing_pages_stop_the_request_with_a_message(self):
        sent, js, conversation = self._send({'text': 'Q', 'noteIds': ['gone']})
        self.assertEqual(sent, [])
        self.assertEqual(conversation, [])
        self.assertIn('not found', js[0])

    def test_no_selection_keeps_plain_behaviour(self):
        sent, _, _ = self._send({'text': 'Q'})
        self.assertEqual(sent[0][-1]['content'], 'Q')


if __name__ == '__main__':
    unittest.main()
