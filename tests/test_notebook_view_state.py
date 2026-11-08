"""What the add-on keeps of a tool's scroll position. Stubbed Anki; synthetic data."""
import importlib
import json
import pathlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Qt(types.ModuleType):
    """aqt.qt whose names are real empty classes, so the panel class can be built."""
    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        meta = type('M', (type,), {'__getattr__': lambda cls, n: MagicMock()})
        stub = meta(name, (), {'__init__': lambda self, *a, **k: None, '__getattr__': lambda self, n: MagicMock()})
        setattr(self, name, stub)
        return stub


class Base(unittest.TestCase):
    def setUp(self):
        stubs = ['aqt', 'aqt.webview', 'aqt.utils', 'aqt.operations', 'anki', 'anki.collection', 'aqt.gui_hooks']
        patcher = patch.dict(sys.modules, {**{n: MagicMock() for n in stubs}, 'aqt.qt': Qt('aqt.qt')})
        patcher.start()
        self.addCleanup(patcher.stop)
        name = 'view_pkg_' + str(id(self))
        package = types.ModuleType(name)
        package.__path__ = [str(ROOT)]
        sys.modules[name] = package
        self.addCleanup(lambda: [sys.modules.pop(k) for k in [k for k in sys.modules if k.startswith(name)]])
        self.module = importlib.import_module(name + '.notebook_sidebar')
        self.panel = object.__new__(self.module.NotebookPanel)
        self.panel._view_state = {}


class ViewStateTests(Base):
    def send(self, payload):
        self.panel._remember_view_state(payload if isinstance(payload, str) else json.dumps(payload))

    def test_positions_are_kept_per_tool(self):
        self.send({'tool': 'notebook', 'state': {'page': 'p1', 'scroll': {'p1': 420.4, 'p2': 0}}})
        self.send({'tool': 'todo', 'state': {'scroll': 650}})
        self.assertEqual(self.panel._view_state['notebook'], {'page': 'p1', 'scroll': {'p1': 420.4, 'p2': 0.0}})
        self.assertEqual(self.panel._view_state['todo'], {'scroll': 650.0})
        self.send({'tool': 'todo', 'state': {'scroll': 10}})
        self.assertEqual(self.panel._view_state['todo'], {'scroll': 10.0}, 'the latest position wins')

    def test_bad_input_is_ignored_or_cleaned(self):
        for bad in ('not json', '[]', '{"tool": "nope", "state": {"scroll": 1}}', '{"tool": "todo"}', '{"tool": "todo", "state": []}', 'x' * 9000):
            self.send(bad)
        self.assertEqual(self.panel._view_state, {})
        self.send({'tool': 'todo', 'state': {'scroll': -5}})
        self.assertEqual(self.panel._view_state['todo']['scroll'], 0.0)
        self.send({'tool': 'todo', 'state': {'scroll': 10 ** 12}})
        self.assertEqual(self.panel._view_state['todo']['scroll'], 10_000_000.0)
        self.send({'tool': 'todo', 'state': {'scroll': True, 'page': 'x' * 500}})
        self.assertEqual(self.panel._view_state['todo'], {}, 'booleans and overlong ids are dropped')
        many = {str(i): i for i in range(500)}
        many.update({'bad': 'x', 'worse': None})
        self.send({'tool': 'notebook', 'state': {'scroll': many}})
        self.assertLessEqual(len(self.panel._view_state['notebook']['scroll']), 100)
        self.assertNotIn('bad', self.panel._view_state['notebook']['scroll'])


class TabKeyTests(Base):
    def test_neighbour_tool_wraps_and_respects_hidden_tabs(self):
        panel, module = self.panel, self.module
        panel._pending_tool_switch = None
        module.workspace_preferences = types.SimpleNamespace(enabled=lambda group: {'notebook': True, 'todo': True, 'pdf': True})
        order = ['notebook', 'todo', 'pdf', 'sync']
        panel.current_tool = 'notebook'
        self.assertEqual([panel._neighbour_tool(1)], ['todo'])
        panel.current_tool = 'sync'
        self.assertEqual(panel._neighbour_tool(1), 'notebook', 'wraps to the first tab')
        panel.current_tool = 'notebook'
        self.assertEqual(panel._neighbour_tool(-1), 'sync', 'and to the last')
        panel._pending_tool_switch = 'pdf'
        self.assertEqual(panel._neighbour_tool(1), 'sync', 'a quick second press moves on from the tab already chosen')
        panel._pending_tool_switch = None
        module.workspace_preferences = types.SimpleNamespace(enabled=lambda group: {'notebook': True, 'todo': False, 'pdf': True})
        panel.current_tool = 'notebook'
        self.assertEqual(panel._neighbour_tool(1), 'pdf', 'hidden tabs are skipped')
        module.workspace_preferences = types.SimpleNamespace(enabled=lambda group: {'notebook': False, 'todo': False, 'pdf': True})
        panel.current_tool = 'sync'
        self.assertEqual(panel._neighbour_tool(1), 'pdf')

    def test_every_tool_page_gets_the_key_handler(self):
        script = self.module._build_nav_toggle_script()
        self.assertIn('synapse-tab-keys', script)
        self.assertIn("pycmd('notion_mini:switch:' +", script)
        self.assertNotIn('__BRIDGE__', script)


class CardImagesModuleTests(Base):
    def test_both_card_creators_get_the_picture_module(self):
        module = self.module
        tag = module._card_images_tag()
        self.assertTrue(tag.startswith('<script>') and tag.endswith('</script>'))
        self.assertIn('window.SynapseCardImages', tag)
        self.assertIn('window.SynapseCardEditor', tag)
        for part in tag.split('</script><script>'):
            self.assertNotIn('</script', part.replace('<script>', '').replace('</script>', ''), 'the inlined code cannot close its own tag')
        for page in ('index.html', 'pdf_viewer.html'):
            html = (ROOT / 'web_notebook' / page).read_text(encoding='utf-8')
            self.assertEqual(html.count(module._CARD_IMAGES_PLACEHOLDER), 1, page)
            self.assertIn('card_images.js', html, 'a page opened as a file loads the module itself')
            self.assertIn('card_editor.js', html)
            replaced = html.replace(module._CARD_IMAGES_PLACEHOLDER, tag, 1)
            self.assertIn('window.SynapseCardImages', replaced)
        self.assertEqual(module._CARD_IMAGES_PLACEHOLDER, '<script id="synapse-card-images-placeholder"></script>')


if __name__ == '__main__':
    unittest.main()
