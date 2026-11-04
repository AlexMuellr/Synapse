"""Opening width of the sidebar groups. Qt is stubbed; only the width rule is exercised."""
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load():
    qt = types.ModuleType('aqt.qt')
    qt.QObject = type('QObject', (), {'__init__': lambda self, *a, **k: None})
    qt.QEvent = MagicMock()
    qt.QTimer = MagicMock()
    qt.Qt = MagicMock()                    # one stable object, so enum attributes compare equal
    spec = importlib.util.spec_from_file_location('sidebar_widths_under_test', ROOT / 'sidebar_widths.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'aqt': types.ModuleType('aqt'), 'aqt.qt': qt}):
        spec.loader.exec_module(module)
    return module


class DefaultWidthTests(unittest.TestCase):
    def test_notebook_group_opens_at_forty_percent_of_the_window(self):
        sw = load()
        self.assertEqual(sw.default_width('notebook', 1600), 640)
        self.assertEqual(sw.default_width('notebook', 1920), 768)
        self.assertEqual(sw.default_width(sw.width_group('todo'), 2000), 800, 'to-do, PDF and Changes share the notebook group')
        self.assertEqual(sw.default_width(sw.width_group('sync'), 2000), 800)

    def test_small_windows_keep_the_shared_minimum_and_other_tools_are_unchanged(self):
        sw = load()
        self.assertEqual(sw.default_width('notebook', 800), 400)
        self.assertEqual(sw.default_width('notebook', 0), 400)
        for group in ('maps', 'ai', 'website', 'music'):
            self.assertEqual(sw.default_width(group, 3000), 400, group)

    def test_restore_uses_the_default_until_the_user_drags(self):
        sw = load()
        window, dock = MagicMock(), MagicMock()
        window.contentsRect.return_value.width.return_value = 1500
        window.centralWidget.return_value.minimumWidth.return_value = 100
        window.centralWidget.return_value.minimumSizeHint.return_value.width.return_value = 100
        dock.minimumWidth.return_value = 200
        tracker = sw.SidebarWidth.__new__(sw.SidebarWidth)
        tracker.window, tracker.dock, tracker.feature, tracker.widths = window, dock, 'notebook', {}
        tracker.active = lambda: True
        tracker.restoring = False
        tracker.restore()
        window.resizeDocks.assert_called_with([dock], [600], sw.Qt.Orientation.Horizontal)
        tracker.widths['notebook'] = 520
        tracker.restore()
        window.resizeDocks.assert_called_with([dock], [520], sw.Qt.Orientation.Horizontal)
        window.contentsRect.return_value.width.return_value = 500      # little room: never wider than what is free
        tracker.widths.clear()
        tracker.restore()
        self.assertLessEqual(window.resizeDocks.call_args[0][1][0], 388)


if __name__ == '__main__':
    unittest.main()
