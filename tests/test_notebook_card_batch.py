"""Card Creator batch for notebook pages (shares the PDF batch code). Fake Anki; synthetic data."""
import ast
import html
import json
import os
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / 'notebook_sidebar.py').read_text())


def module_function(name):
    return next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)


def method(name):
    panel = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'NotebookPanel')
    return next(n for n in panel.body if isinstance(n, ast.FunctionDef) and n.name == name)


class FakeCol:
    def __init__(self):
        self.added, self.decks, self.undo_entries = [], [], []
        col = self
        self.decks_api = types.SimpleNamespace(add_normal_deck_with_name=lambda name: (col.decks.append(name), types.SimpleNamespace(id=len(col.decks)))[1])

    @property
    def decks_ns(self):
        return self.decks_api

    def new_note(self, notetype):
        return types.SimpleNamespace(fields=['', ''], notetype=notetype)

    def add_notes(self, requests):
        self.added.extend(requests)
        return 'changes'

    def add_custom_undo_entry(self, label):
        self.undo_entries.append(label)
        return 1

    def merge_undo_entries(self, entry):
        return 'merged'

    def undo(self):
        self.undone = True


class Harness(unittest.TestCase):
    def setUp(self):
        self.col, self.notifications = FakeCol(), []
        self.col.decks = types.SimpleNamespace(names=[], add_normal_deck_with_name=None)
        names = self.col.decks.names
        self.col.decks.add_normal_deck_with_name = lambda name: (names.append(name), types.SimpleNamespace(id=len(names)))[1]

        class NotebookPanel:
            @staticmethod
            def _pdf_text_to_html(text):
                return html.escape(text.replace('\x00', '').replace('\r\n', '\n')).replace('\n', '<br>')

        class Op:
            def __init__(self, parent, op):
                self.op = op
            def success(self, fn): self.ok = fn; return self
            def failure(self, fn): self.bad = fn; return self
            def run_in_background(self, initiator=None):
                try:
                    self.ok(self.op(harness.col))
                except Exception as exc:
                    self.bad(exc)

        harness = self
        pdf_types = types.ModuleType('synapse_batch_pkg.pdf_card_types')
        real = importlib_load('pdf_card_types')
        pdf_types.validate, pdf_types.card_count = real.validate, real.card_count
        pdf_types.resolve = lambda col, kind: f'notetype-{kind}'
        package = types.ModuleType('synapse_batch_pkg'); package.__path__ = [str(ROOT)]
        patcher = patch.dict(sys.modules, {'synapse_batch_pkg': package, 'synapse_batch_pkg.pdf_card_types': pdf_types})
        patcher.start(); self.addCleanup(patcher.stop)
        env = {'__package__': 'synapse_batch_pkg', 'html': html, 'json': json, 'os': os, '_': lambda t: t,
               'MAX_PDF_BATCH_CARDS': 500, 'MAX_PDF_CARD_CHARS': 50_000, 'MAX_PDF_BATCH_CHARS': 1_000_000,
               '_valid_pdf_id': lambda v: '', '_pdf_source_html': lambda *a: '<pdf-source>', 'CollectionOp': Op,
               'mw': types.SimpleNamespace(col=self.col), 'NotebookPanel': NotebookPanel, 'AddNoteRequest': lambda note, deck_id: types.SimpleNamespace(note=note, deck_id=deck_id)}
        body = ast.Module(body=[module_function('_notebook_source_html'), method('_create_pdf_card_batch')], type_ignores=[])
        exec(compile(body, 'notebook_sidebar.py', 'exec'), env)
        self.env = env
        panel = types.SimpleNamespace(_current_pdf_path='/x/book.pdf', _decode_pdf_action_payload=lambda b: json.loads(b),
                                      _notify_pdf_action=lambda cb, ok, msg: self.notifications.append((cb, ok, msg)))
        panel._pdf_text_to_html = NotebookPanel._pdf_text_to_html
        self.panel = panel

    def create(self, payload, notebook=True):
        env_fn = self.env['_create_pdf_card_batch']
        with patch.dict(self.env, {}):
            env_fn(self.panel, json.dumps(payload), notebook=notebook)
        return self.notifications[-1]


def importlib_load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NotebookBatchTests(Harness):
    def test_notebook_batch_creates_cards_with_per_card_source(self):
        callback, ok, message = self.create({'deckName': 'Anatomy', 'source': 'Fallback', 'cards': [
            {'front': 'Q <1>', 'back': 'A', 'type': 'basic', 'includeSource': True, 'source': 'Heart'},
            {'front': 'The {{c1::heart}} pumps', 'back': '', 'type': 'cloze', 'includeSource': False},
            {'front': 'Rev', 'back': 'Back', 'type': 'reversed', 'includeSource': True, 'source': ''}]})
        self.assertEqual((callback, ok), ('onNotebookCardBatchCreated', True), message)
        self.assertEqual(self.col.decks.names, ['Anatomy'])
        fields = [r.note.fields for r in self.col.added]
        self.assertEqual(fields[0][0], 'Q &lt;1&gt;')
        self.assertIn('Source: Notebook — Heart', fields[0][1])
        self.assertNotIn('pdf-source', fields[0][1])
        self.assertEqual(fields[1][1], '')
        self.assertIn('Source: Notebook — Fallback', fields[2][1], 'falls back to the batch source')
        self.assertEqual([r.note.notetype for r in self.col.added], ['notetype-basic', 'notetype-cloze', 'notetype-reversed'])
        self.assertEqual(self.col.undo_entries, ['Create cards'])
        self.assertIn('Created 4 cards', message, 'a reversed card counts twice')

    def test_page_title_is_escaped_in_the_source_label(self):
        self.create({'deckName': 'D', 'cards': [{'front': 'Q', 'back': 'A', 'type': 'basic', 'includeSource': True, 'source': '<img src=x onerror=1>'}]})
        back = self.col.added[0].note.fields[1]
        self.assertIn('&lt;img src=x onerror=1&gt;', back)
        self.assertNotIn('<img', back)

    def test_validation_errors_use_the_notebook_wording_and_create_nothing(self):
        cases = [
            ({'deckName': '', 'cards': [{'front': 'Q', 'back': 'A'}]}, 'valid deck name'),
            ({'deckName': 'D', 'cards': []}, 'at least one card'),
            ({'deckName': 'D', 'cards': [{'front': '  ', 'back': 'A'}]}, 'front side'),
            ({'deckName': 'D', 'cards': [{'front': 'x' * 50_001, 'back': ''}]}, 'One of the cards is too long.'),
            ({'deckName': 'D', 'cards': [{'front': 'Q', 'back': '', 'type': 'reversed'}]}, 'both sides'),
            ({'deckName': 'D', 'cards': [{'front': 'no cloze', 'back': '', 'type': 'cloze'}]}, 'cloze'),
            ({'deckName': 'D', 'cards': [{'front': 'Q', 'back': 'A'}] * 501}, 'A Card Creator session can contain at most 500 cards.'),
            ({'deckName': 'D\nE', 'cards': [{'front': 'Q', 'back': 'A'}]}, 'valid deck name'),
        ]
        for payload, expected in cases:
            callback, ok, message = self.create(payload)
            self.assertEqual((callback, ok), ('onNotebookCardBatchCreated', False), payload)
            self.assertIn(expected, message)
        self.assertEqual(self.col.added, [])
        self.assertEqual(self.col.decks.names, [])

    def test_pdf_mode_is_unchanged(self):
        callback, ok, message = self.create({'deckName': 'PDFs', 'cards': [{'front': 'Q', 'back': 'A', 'type': 'basic', 'includeSource': True}]}, notebook=False)
        self.assertEqual((callback, ok), ('onPdfCardBatchCreated', True))
        self.assertEqual(self.col.added[0].note.fields[1], 'A<pdf-source>')
        self.assertEqual(self.col.undo_entries, ['Create PDF cards'])
        callback, ok, message = self.create({'deckName': 'PDFs', 'cards': [{'front': 'x' * 50_001, 'back': ''}]}, notebook=False)
        self.assertEqual(message, 'One of the PDF cards is too long.')

    def test_collection_failure_is_reported_in_notebook_words(self):
        self.col.add_notes = lambda requests: (_ for _ in ()).throw(RuntimeError('boom'))
        callback, ok, message = self.create({'deckName': 'D', 'cards': [{'front': 'Q', 'back': 'A'}]})
        self.assertEqual((callback, ok, message), ('onNotebookCardBatchCreated', False, 'Could not create the cards.'))


if __name__ == '__main__':
    unittest.main()
