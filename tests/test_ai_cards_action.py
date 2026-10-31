"""AI card suggestions and creation with a fake collection. Synthetic data; no Anki import."""
import ast
import base64
import json
import pathlib
import sys
import types
import typing
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
PNG = b'\x89PNG\r\n\x1a\n' + b'\x01' * 16
PNG_URI = 'data:image/png;base64,' + base64.b64encode(PNG).decode()
BODY = json.dumps({'pages': [{'id': 'a', 'title': 'Heart', 'blocks': [
    {'type': 'text', 'html': f'Aortic valve <img src="{PNG_URI}">'},
    {'type': 'text', 'html': '<img src="https://x.org/leak.png">'}]}]})


def load(names, env):
    tree = ast.parse((ROOT / 'ai_assistant.py').read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names), names
    ns = {'__package__': 'synapse_cards_action_pkg', 'Any': typing.Any, 'Dict': typing.Dict, 'List': typing.List,
          'Optional': typing.Optional, 'json': json, '_': lambda text: text, **env}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'ai_assistant.py', 'exec'), ns)
    return ns


class FakeDeck:
    def __init__(self, id, name):
        self.id, self.name = id, name


class FakeCol:
    def __init__(self):
        self.written, self.added = {}, []
        col = self
        self.decks = types.SimpleNamespace(
            all_names_and_ids=lambda **kw: [FakeDeck(1, 'Default'), FakeDeck(7, 'Anatomy')],
            get_current_id=lambda: 7,
            get=lambda i: {'name': 'Anatomy'} if i == 7 else None)
        self.media = types.SimpleNamespace(write_data=lambda name, data: col.written.setdefault(name, data) and name)
        self.models = object()

    def new_note(self, notetype):
        return types.SimpleNamespace(fields=['', ''])

    def add_notes(self, requests):
        self.added.extend(requests)


class Base(unittest.TestCase):
    def setUp(self):
        package = types.ModuleType('synapse_cards_action_pkg')
        package.__path__ = [str(ROOT)]
        fake_types = types.ModuleType('synapse_cards_action_pkg.pdf_card_types')
        fake_types.resolve = lambda col, kind: f'notetype-{kind}'
        anki, collection = types.ModuleType('anki'), types.ModuleType('anki.collection')
        collection.AddNoteRequest = lambda note, deck_id: types.SimpleNamespace(note=note, deck_id=deck_id)
        patcher = patch.dict(sys.modules, {
            'synapse_cards_action_pkg': package, 'synapse_cards_action_pkg.pdf_card_types': fake_types,
            'anki': anki, 'anki.collection': collection})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.js, self.col = [], FakeCol()


class SuggestionTests(Base):
    def run_action(self, data, reply, configured=True, body=BODY):
        class SyncThread:
            def __init__(self, target, daemon=None): self.target = target
            def start(self): self.target()
        prompts = []
        def complete(settings, messages):
            prompts.append(messages[0]['content'])
            if isinstance(reply, Exception):
                raise reply
            return reply
        ns = load({'_action_notes_cards', '_card_error', '_clean_note_ids'}, {
            'MAX_NOTE_PAGES': 30, '_card_images': {}, '_session_generation': 3,
            '_load_settings': lambda: {'provider': 'openai', 'apiKey': 'k' if configured else '', 'model': 'm'},
            '_is_configured': lambda provider, key: bool(key), '_load_notebook_body': lambda: body,
            '_lang_suffix': lambda: 'in English', '_complete_text': complete,
            '_classify_error': lambda exc, provider: 'classified', 'mw': types.SimpleNamespace(col=self.col),
            '_js_on_main': lambda code, generation=None: self.js.append((code, generation)),
            'threading': types.SimpleNamespace(Thread=SyncThread)})
        ns['_action_notes_cards'](data)
        return ns, prompts

    def test_suggestions_carry_cards_decks_and_only_used_images(self):
        reply = '```json\n[{"front":"Which valve?","back":"Aortic","image":"img1"}]\n```'
        ns, prompts = self.run_action({'noteIds': ['a']}, reply)
        self.assertIn('[img1]', prompts[0])
        self.assertNotIn('base64', prompts[0])
        self.assertNotIn('leak.png', prompts[0])
        code, generation = self.js[-1]
        self.assertEqual(generation, 3)
        self.assertTrue(code.startswith('receiveCardSuggestions('))
        payload = json.loads(code[len('receiveCardSuggestions('):-2])
        self.assertEqual(payload['cards'], [{'front': 'Which valve?', 'back': 'Aortic', 'image': 'img1'}])
        self.assertEqual(payload['images'], {'img1': PNG_URI})
        self.assertEqual(payload['defaultDeck'], 7)
        self.assertEqual([d['name'] for d in payload['decks']], ['Default', 'Anatomy'])
        self.assertEqual(ns['_card_images'], {'img1': PNG_URI})

    def test_error_paths_never_reach_the_ai_or_crash(self):
        _, prompts = self.run_action({'noteIds': []}, '[]')
        self.assertEqual(prompts, [])
        self.assertIn('Select notes first', self.js[-1][0])
        _, prompts = self.run_action({'noteIds': ['gone']}, '[]')
        self.assertEqual(prompts, [])
        self.assertIn('not found', self.js[-1][0])
        _, prompts = self.run_action({'noteIds': ['a']}, '[]', configured=False)
        self.assertEqual(prompts, [])
        self.assertIn('No API key', self.js[-1][0])
        self.run_action({'noteIds': ['a']}, 'sorry, no cards today')
        self.assertIn('usable cards', self.js[-1][0])
        self.run_action({'noteIds': ['a']}, RuntimeError('boom'))
        self.assertIn('classified', self.js[-1][0])
        self.assertTrue(all(code.startswith('receiveCardsError(') for code, _ in self.js))


class CreateTests(Base):
    def run_create(self, data, images=None):
        cache = {'img1': PNG_URI} if images is None else images
        ns = load({'_action_create_cards'}, {
            '_card_images': cache, 'mw': types.SimpleNamespace(col=self.col),
            '_run_js': self.js.append})
        ns['_action_create_cards'](data)

    def test_cards_with_image_are_added_to_the_chosen_deck(self):
        self.run_create({'deckId': 7, 'cards': [
            {'front': 'Q <1>', 'back': 'A', 'image': 'img1', 'placement': 'back'},
            {'front': 'Q2', 'back': 'A2', 'image': None}]})
        self.assertEqual(self.js, ['receiveCardsCreated(2, "Anatomy");'])
        self.assertEqual([r.deck_id for r in self.col.added], [7, 7])
        name = next(iter(self.col.written))
        self.assertEqual(self.col.written[name], PNG)
        first, second = (r.note.fields for r in self.col.added)
        self.assertEqual(first, ['Q &lt;1&gt;', f'A<br><img src="{name}">'])
        self.assertEqual(second, ['Q2', 'A2'])

    def test_bad_requests_create_nothing(self):
        for data in ({'deckId': 99, 'cards': [{'front': 'Q', 'back': 'A'}]},
                     {'deckId': 7, 'cards': []},
                     {'deckId': 'x', 'cards': [{'front': 'Q', 'back': 'A'}]},
                     {'deckId': 7, 'cards': [{'front': 'Q', 'back': 'A', 'image': 'img1', 'placement': 'back', 'src': 'x'}], 'images': {'img1': 'data:image/png;base64,AAAA'}}):
            self.js.clear()
            self.run_create(data, images={} if 'images' in data else None)
            self.assertTrue(self.js[0].startswith('receiveCardsError('), data)
        self.assertEqual(self.col.added, [])
        self.assertEqual(self.col.written, {})


if __name__ == '__main__':
    unittest.main()
