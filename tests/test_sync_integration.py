"""The Anki side of sync (notebook_sidebar glue) with a fake Anki, a real SQLite file and real Git."""
import importlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[1]
STUBS = ['aqt', 'aqt.qt', 'aqt.webview', 'aqt.utils', 'aqt.operations', 'anki', 'anki.collection', 'aqt.gui_hooks']
HAVE_GIT = shutil.which('git') is not None


class _Meta(type):
    def __getattr__(cls, name):
        return MagicMock()


class _QtStub(types.ModuleType):
    """aqt.qt: every name is a real (empty) class, so `class Panel(QWidget)` and isinstance() work."""
    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        stub = _Meta(name, (), {'__init__': lambda self, *a, **k: None, '__getattr__': lambda self, n: MagicMock()})
        setattr(self, name, stub)
        return stub


_counter = [0]


def load_module():
    """A fresh copy of notebook_sidebar in its own package, so two 'computers' never share state."""
    saved = {name: sys.modules.get(name) for name in STUBS}
    for name in STUBS:
        sys.modules[name] = MagicMock()
    sys.modules['aqt.qt'] = _QtStub('aqt.qt')
    _counter[0] += 1
    name = f'synint_pkg_{_counter[0]}'
    package = types.ModuleType(name)
    package.__path__ = [str(ROOT)]
    sys.modules[name] = package
    module = importlib.import_module(f'{name}.notebook_sidebar')
    module.mw = MagicMock()
    return module, package, saved


def page(pid, text='x'):
    return {'id': pid, 'title': pid.upper(), 'icon': '', 'blocks': [{'type': 'text', 'html': text}]}


class Profile:
    """One 'computer': its own profile folder, add-on settings and a SQLite notebook."""
    def __init__(self, base, name, remote):
        self.module, self.package, self.saved = load_module()
        self.folder = os.path.join(base, name + '-profile')
        os.makedirs(self.folder)
        self.sync_dir = os.path.join(base, name + '-sync')
        subprocess.run(['git', 'clone', '-q', remote, self.sync_dir], check=True, stderr=subprocess.DEVNULL)
        self.settings = {'sync_folder': self.sync_dir}
        self.package.addon_settings = self.settings
        self.package.save_addon_settings = lambda: setattr(self, 'saved_settings', dict(self.settings))
        mw = self.module.mw
        mw.pm.profileFolder.return_value = self.folder
        self.tasks = []
        def run_in_background(fn, callback):
            future = MagicMock()
            future.result.return_value = fn()
            callback(future)
        mw.taskman.run_in_background.side_effect = run_in_background
        self.module._dock = None
        self.module._sync_controller = None

    def write_notes(self, pages):
        body = json.dumps({'activePageId': pages[0]['id'], 'sidebarOpen': False, 'pages': pages})
        assert self.module._save_snapshot('notes', body, self.module.db_path())

    def notes(self):
        data = json.loads(self.module.load_latest_note(self.module.db_path()))
        pages = data['pages'] if isinstance(data, dict) else []
        return {p['id']: p['blocks'][0]['html'] for p in pages}

    def run(self, kind, **kw):
        out = []
        self.module._get_sync_controller().run(kind, out.append, **kw)
        assert len(out) == 1, out
        return out[0]


@unittest.skipUnless(HAVE_GIT, 'git is not installed')
class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        remote = os.path.join(self.dir.name, 'remote.git')
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', remote], check=True)
        self.pc = Profile(self.dir.name, 'pc', remote)
        self.laptop = Profile(self.dir.name, 'laptop', remote)

    def test_real_database_round_trip_between_two_profiles(self):
        self.pc.write_notes([page('a', 'from the pc'), page('b', 'second')])
        result = self.pc.run('sync')
        self.assertTrue(result['ok'] and result['pushed'], result)
        result = self.laptop.run('sync')
        self.assertTrue(result['changed']['notebook'])
        self.assertEqual(self.laptop.notes(), {'a': 'from the pc', 'b': 'second'})
        # the validated save path keeps its rotation of earlier snapshots
        con = __import__('sqlite3').connect(self.laptop.module.db_path())
        self.assertGreaterEqual(con.execute('select count(*) from notes').fetchone()[0], 1)
        con.close()
        self.laptop.write_notes([page('a', 'from the laptop'), page('b', 'second')])
        self.laptop.run('sync'); self.pc.run('sync')
        self.assertEqual(self.pc.notes()['a'], 'from the laptop')
        self.assertTrue(os.path.exists(os.path.join(self.pc.folder, 'SynapsePro_Data', 'sync_state.json')))

    def test_todos_use_the_normalizing_save_path(self):
        self.pc.module._save_snapshot('todos', json.dumps([{'id': 't1', 'text': 'milk', 'done': False}]), self.pc.module.db_path())
        self.pc.run('sync'); self.laptop.run('sync')
        tasks = json.loads(self.laptop.module.load_latest_todos(self.laptop.module.db_path()))
        self.assertEqual([t['text'] for t in tasks], ['milk'])

    def test_pdf_library_through_the_real_database_and_profile_store(self):
        for profile in (self.pc, self.laptop):
            profile.settings['sync_pdfs'] = True
        source = pathlib.Path(self.dir.name, 'lecture.pdf')
        source.write_bytes(b'%PDF-1.4 synthetic')
        library = {'version': 4, 'folders': [{'id': 'f1', 'name': 'Anatomy'}], 'recentDecks': [], 'cardCreatorSession': None,
                   'pdfs': [{'id': 'p1', 'name': 'lecture.pdf', 'path': str(source), 'addedAt': '2026-01-01', 'folderId': 'f1'}]}
        self.assertTrue(self.pc.module._save_snapshot('pdfs', json.dumps(library), self.pc.module.db_path()))
        self.assertTrue(self.pc.run('sync')['ok'])
        result = self.laptop.run('sync')
        self.assertTrue(result['changed']['pdfs'], result)
        received = json.loads(self.laptop.module.load_pdf_list(self.laptop.module.db_path()))
        entry = received['pdfs'][0]
        self.assertEqual(entry['folderId'], 'f1')
        self.assertEqual(os.path.dirname(entry['path']), os.path.join(self.laptop.folder, 'SynapsePro_Data', 'synced_pdfs'))
        self.assertEqual(pathlib.Path(entry['path']).read_bytes(), b'%PDF-1.4 synthetic')
        enriched = json.loads(self.laptop.module.load_pdf_list_enriched(self.laptop.module.db_path()))
        self.assertTrue(enriched['pdfs'][0]['_exists'], 'the PDF viewer finds the file')

    def test_settings_are_stored_in_the_profile_settings(self):
        controller = self.pc.module._get_sync_controller()
        controller.save_config({'sync_auto_push': True, 'sync_auto_pull': False})
        self.assertTrue(self.pc.settings['sync_auto_push'])
        self.assertEqual(self.pc.saved_settings['sync_auto_pull'], False)

    def test_nav_has_the_changes_tab_and_turns_red_when_dirty(self):
        module = self.pc.module
        clean = module._build_nav_html('notebook')
        self.assertIn('snav-sync-btn', clean)
        self.assertIn(':switch:sync', clean)
        self.assertNotIn('snav-dirty', clean)
        self.pc.write_notes([page('a')])
        controller = module._get_sync_controller()
        controller.refresh_status()
        self.assertTrue(controller.dirty)
        self.assertIn('snav-dirty', module._build_nav_html('notebook'))
        self.assertRegex(module._build_nav_html('sync'), r'class="snav-btn snav-active[^"]*"[^>]*onclick="pycmd\(\'notion_mini:switch:sync')
        self.assertIn('__synapseSyncDirty', module._build_nav_toggle_script())
        self.assertIn('sync', module.TOOL_FILES)
        self.assertTrue((ROOT / 'web_notebook' / module.TOOL_FILES['sync']).exists())

    def test_page_strings_exist_for_every_key_the_page_uses(self):
        import re
        html = (ROOT / 'web_notebook' / 'sync.html').read_text(encoding='utf-8')
        used = set(re.findall(r'data-i18n(?:-placeholder)?="(\w+)"', html)) | set(re.findall(r"T\('(\w+)'", html))
        script = self.pc.module._build_i18n_script('sync')
        missing = sorted(key for key in used if f'"{key}"' not in script)
        self.assertEqual(missing, [])

    def test_bridge_commands_from_the_page(self):
        module = self.pc.module
        panel = object.__new__(module.NotebookPanel)
        panel.web = MagicMock()
        panel.current_tool = 'sync'
        self.pc.write_notes([page('a', 'via page')])
        panel._handle_sync_command('save:' + json.dumps({'sync_auto_push': True, 'sync_folder': self.pc.sync_dir}))
        self.assertTrue(self.pc.settings['sync_auto_push'])
        panel._handle_sync_command('run:' + json.dumps({'kind': 'sync'}))
        calls = ' '.join(str(c) for c in panel.web.eval.call_args_list)
        self.assertIn('syncResult', calls)
        self.assertIn('"ok": true', calls)
        for hostile in ('run:{"kind":"rm -rf"}', 'run:not json', 'save:[1,2]', 'clone:{"url":"--upload-pack=x"}', 'nonsense'):
            panel.web.eval.reset_mock()
            panel._handle_sync_command(hostile)
        self.assertEqual(self.laptop.run('sync')['changed']['notebook'], True)
        self.assertEqual(self.laptop.notes(), {'a': 'via page'})

    def test_final_push_on_close_only_when_enabled_and_never_raises(self):
        module = self.pc.module
        self.pc.write_notes([page('a', 'before closing')])
        module._sync_final_push()
        self.assertEqual(self.laptop.run('pull')['changed']['notebook'], False, 'disabled: nothing was pushed')
        self.pc.settings['sync_auto_push'] = True
        module._sync_final_push()
        self.assertTrue(self.laptop.run('pull')['changed']['notebook'])
        self.assertEqual(self.laptop.notes(), {'a': 'before closing'})
        self.pc.settings['sync_folder'] = os.path.join(self.dir.name, 'does', 'not', 'exist')
        module._sync_final_push()

    def test_autopull_runs_only_when_configured(self):
        module = self.pc.module
        self.laptop.write_notes([page('z', 'remote news')]); self.laptop.run('sync')
        self.pc.settings['sync_auto_pull'] = False
        module._run_sync_autopull()
        self.assertEqual(self.pc.notes(), {}, 'auto-pull is off: nothing arrives')
        self.pc.settings['sync_auto_pull'] = True
        module._run_sync_autopull()
        self.assertEqual(self.pc.notes(), {'z': 'remote news'})
        self.pc.settings['sync_folder'] = ''
        module._run_sync_autopull()


if __name__ == '__main__':
    unittest.main()
