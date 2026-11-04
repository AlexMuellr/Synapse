"""Notebook highlight shortcut: what the page receives and what the settings dialog stores."""
import ast
import importlib
import pathlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
KEY, DEFAULT = 'notebook_highlight', 'Ctrl+Shift+H'
SIDEBAR_KEYS = ('mindmap_enabled', 'notebook_enabled')


def fake_shortcuts():
    """The portable-text rules without Qt: one chord, must contain Ctrl/Alt/Meta or be an F-key."""
    mod = types.ModuleType('sidebar_shortcuts')
    mod.normalise_sequence = lambda raw: raw.strip() if isinstance(raw, str) and '+' in raw or (isinstance(raw, str) and raw.startswith('F')) else ''
    def safe(raw):
        parts = raw.split('+')
        return any(p in ('Ctrl', 'Alt', 'Meta') for p in parts[:-1]) or parts[-1].startswith('F')
    mod.is_safe_sequence = lambda raw: bool(mod.normalise_sequence(raw)) and safe(raw)
    mod.display_sequence = lambda raw: ('⌘' + raw) if raw else ''
    mod.normalise_shortcut_map = lambda raw: {k: v for k, v in raw.items() if k in SIDEBAR_KEYS} if isinstance(raw, dict) else {}
    return mod


class SidebarGlue(unittest.TestCase):
    """notebook_sidebar loaded with a stubbed Anki (same approach as test_sync_integration)."""
    def setUp(self):
        stubs = ['aqt', 'aqt.webview', 'aqt.utils', 'aqt.operations', 'anki', 'anki.collection', 'aqt.gui_hooks']
        qt = types.ModuleType('aqt.qt')
        meta = type('M', (type,), {'__getattr__': lambda cls, n: MagicMock()})
        qt.__getattr__ = lambda name: meta(name, (), {'__init__': lambda self, *a, **k: None, '__getattr__': lambda self, n: MagicMock()}) if not name.startswith('__') else (_ for _ in ()).throw(AttributeError(name))
        patcher = patch.dict(sys.modules, {**{n: MagicMock() for n in stubs}, 'aqt.qt': qt})
        patcher.start()
        self.addCleanup(patcher.stop)
        name = 'shortcut_pkg_' + str(id(self))
        package = types.ModuleType(name)
        package.__path__ = [str(ROOT)]
        sys.modules[name] = package
        self.addCleanup(lambda: [sys.modules.pop(k) for k in [k for k in sys.modules if k.startswith(name)]])
        self.settings = {}
        package.addon_settings = self.settings
        sys.modules[name + '.sidebar_shortcuts'] = package.sidebar_shortcuts = fake_shortcuts()
        constants = types.SimpleNamespace(NOTEBOOK_HIGHLIGHT_DEFAULT=DEFAULT, NOTEBOOK_HIGHLIGHT_KEY=KEY)
        sys.modules[name + '.constants'] = package.constants = constants
        self.module = importlib.import_module(name + '.notebook_sidebar')

    def test_page_receives_default_custom_and_disabled_shortcuts(self):
        got = self.module._notebook_shortcuts()
        self.assertEqual(got, {'highlight': DEFAULT, 'highlightLabel': '⌘' + DEFAULT}, 'unset: the default applies')
        self.settings['notebook_highlight_shortcut'] = 'Alt+M'
        self.assertEqual(self.module._notebook_shortcuts()['highlight'], 'Alt+M')
        self.settings['notebook_highlight_shortcut'] = ''
        self.assertEqual(self.module._notebook_shortcuts(), {'highlight': '', 'highlightLabel': ''}, 'cleared: switched off')
        for bad in ('H', 'Shift+H', 5, None, ['x']):
            self.settings['notebook_highlight_shortcut'] = bad
            self.assertEqual(self.module._notebook_shortcuts()['highlight'], '', repr(bad))

    def test_live_refresh_only_reaches_a_loaded_notebook_page(self):
        module = self.module
        module._dock = None
        module.refresh_notebook_shortcuts()                      # no dock: nothing happens
        panel = object.__new__(module.NotebookPanel)
        panel.web, panel.current_tool = MagicMock(), 'notebook'
        module._dock = MagicMock()
        module._dock.widget.return_value = panel
        self.settings['notebook_highlight_shortcut'] = 'Alt+M'
        module.refresh_notebook_shortcuts()
        js = panel.web.eval.call_args[0][0]
        self.assertIn('synapseSetNotebookShortcuts', js)
        self.assertIn('"highlight": "Alt+M"', js)
        panel.web.eval.reset_mock()
        panel.current_tool = 'todo'
        module.refresh_notebook_shortcuts()
        panel.web.eval.assert_not_called()


class SettingsDialogLogic(unittest.TestCase):
    """The two dialog methods that decide what is stored, run without Qt."""
    @staticmethod
    def methods():
        tree = ast.parse((ROOT / 'web_settings_dialog.py').read_text())
        cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == 'WebSettingsDialog')
        return [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ('get_new_settings', '_shortcuts_for_page')]

    def build(self, sent, highlight_value=''):
        name = 'dialog_pkg_' + str(id(sent))
        package = types.ModuleType(name)
        package.__path__ = [str(ROOT)]
        options = types.ModuleType(name + '.deck_overview_options')
        options.normalize = lambda s: {}
        patcher = patch.dict(sys.modules, {name: package, name + '.deck_overview_options': options})
        patcher.start()
        self.addCleanup(patcher.stop)
        env = {'__package__': name, 'sidebar_shortcuts': fake_shortcuts(), 'Dict': dict, '_COUNTER_COLOR_DEFAULTS': {},
               '_safe_counter_color': lambda v, f: f,
               'constants': types.SimpleNamespace(NOTEBOOK_HIGHLIGHT_KEY=KEY, NOTEBOOK_HIGHLIGHT_DEFAULT=DEFAULT)}
        exec(compile(ast.Module(body=self.methods(), type_ignores=[]), 'web_settings_dialog.py', 'exec'), env)
        dialog = types.SimpleNamespace(_result={'sidebar_shortcuts': sent}, _custom_theme_colors={}, _shortcut_values={'notebook_enabled': 'Ctrl+N'},
                                       _highlight_value=highlight_value)
        return env, dialog

    def test_highlight_shortcut_is_stored_separately_from_sidebar_shortcuts(self):
        env, dialog = self.build({'notebook_enabled': 'Ctrl+N', KEY: 'Ctrl+Alt+H', 'mindmap_enabled': 'Ctrl+M'})
        result = env['get_new_settings'](dialog)
        self.assertEqual(result['notebook_highlight_shortcut'], 'Ctrl+Alt+H')
        self.assertEqual(result['sidebar_shortcuts'], {'notebook_enabled': 'Ctrl+N', 'mindmap_enabled': 'Ctrl+M'},
                         'the sidebar map never contains the notebook entry')

    def test_cleared_or_unsafe_highlight_shortcut_is_stored_as_empty(self):
        for sent in ({}, {KEY: ''}, {KEY: 'H'}, {KEY: 'Shift+H'}, 'junk'):
            env, dialog = self.build(sent)
            self.assertEqual(env['get_new_settings'](dialog)['notebook_highlight_shortcut'], '', sent)

    def test_page_gets_the_highlight_entry_only_when_one_is_set(self):
        env, dialog = self.build({}, highlight_value=DEFAULT)
        self.assertEqual(env['_shortcuts_for_page'](dialog), {'notebook_enabled': 'Ctrl+N', KEY: DEFAULT})
        env, dialog = self.build({}, highlight_value='')
        self.assertEqual(env['_shortcuts_for_page'](dialog), {'notebook_enabled': 'Ctrl+N'})


if __name__ == '__main__':
    unittest.main()
