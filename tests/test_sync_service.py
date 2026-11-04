"""Sync controller with real Git repositories and a fake Anki side. Synthetic data only."""
import importlib.util
import json
import os
import unittest.mock
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
package = types.ModuleType('synapse_service_pkg')
package.__path__ = [str(ROOT)]
sys.modules['synapse_service_pkg'] = package


def load(name):
    spec = importlib.util.spec_from_file_location(f'synapse_service_pkg.{name}', ROOT / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[f'synapse_service_pkg.{name}'] = module
    spec.loader.exec_module(module)
    return module


ns, sg, sm, svc = load('notebook_sync'), load('sync_git'), load('sync_manager'), load('sync_service')
HAVE_GIT = shutil.which('git') is not None


def page(pid, text='x'):
    return {'id': pid, 'title': pid.upper(), 'icon': '', 'blocks': [{'type': 'text', 'html': text}]}


class FakeIO:
    """The Anki side: a database of two snapshots, settings and a recorder for UI calls."""
    def __init__(self, base, name, folder):
        self.settings = {'sync_folder': folder}
        self.state_file = os.path.join(base, name + '-state.json')
        self.notebook = json.dumps({'activePageId': '', 'pages': []})
        self.todos = '[]'
        self.pdfs = json.dumps({'version': 4, 'folders': [], 'pdfs': [], 'recentDecks': [], 'cardCreatorSession': None})
        self.store = os.path.join(base, name + '-pdf-store')
        self.fail_write = False
        self.fail_flush = False
        self.flushes = self.reloads = 0
        self.notifications = []

    def get_setting(self, key, default): return self.settings.get(key, default)
    def set_settings(self, values): self.settings.update(values)
    def state_path(self): return self.state_file
    def read_notebook(self): return self.notebook
    def read_todos(self): return self.todos
    def read_pdfs(self): return self.pdfs
    def pdf_store_dir(self): return self.store

    def write_notebook(self, body):
        if self.fail_write:
            return False
        self.notebook = body
        return True

    def write_todos(self, body):
        if self.fail_write:
            return False
        self.todos = body
        return True

    def write_pdfs(self, body):
        if self.fail_write:
            return False
        self.pdfs = body
        return True

    def flush(self, callback):
        self.flushes += 1
        callback(not self.fail_flush)

    def reload(self): self.reloads += 1
    def run_bg(self, fn, callback): callback(fn())
    def notify(self, status): self.notifications.append(status)


class Computer:
    def __init__(self, base, name, remote=None, folder=None):
        self.folder = folder or os.path.join(base, name)
        if remote:
            subprocess.run(['git', 'clone', '-q', remote, self.folder], check=True)
        self.io = FakeIO(base, name, self.folder)
        self.controller = svc.SyncController(self.io)

    def pages(self):
        return {p['id']: p['blocks'][0]['html'] for p in json.loads(self.io.notebook)['pages']}

    def set_pages(self, pages):
        data = json.loads(self.io.notebook)
        data['pages'] = pages
        data['activePageId'] = pages[0]['id'] if pages else ''
        self.io.notebook = json.dumps(data)

    def run(self, kind, **kw):
        out = []
        self.controller.run(kind, out.append, **kw)
        assert len(out) == 1, out
        return out[0]


@unittest.skipUnless(HAVE_GIT, 'git is not installed')
class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.remote = os.path.join(self.dir.name, 'remote.git')
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', self.remote], check=True)
        self.pc = Computer(self.dir.name, 'pc', self.remote)
        self.laptop = Computer(self.dir.name, 'laptop', self.remote)

    def test_sync_between_two_computers_and_status_colors(self):
        self.pc.set_pages([page('a', 'one')])
        self.assertTrue(self.pc.controller.status()['local']['notebook']['added'], 'unsynced pages are pending')
        result = self.pc.run('sync')
        self.assertTrue(result['ok'] and result['pushed'], result)
        status = self.pc.controller.status()
        self.assertFalse(status['dirty'], 'everything is pushed, so the tab is not red')
        self.assertEqual(status['local']['notebook']['added'], [])
        result = self.laptop.run('sync')
        self.assertTrue(result['pulled'] and result['changed']['notebook'])
        self.assertEqual(self.laptop.pages(), {'a': 'one'})
        self.assertEqual(self.laptop.io.reloads, 1, 'the open page is refreshed after new data arrived')
        self.laptop.set_pages([page('a', 'edited on laptop')])
        self.assertTrue(self.laptop.controller.status()['local']['notebook']['modified'])
        self.laptop.controller.refresh_status()
        self.assertTrue(self.laptop.controller.dirty, 'a local edit makes the tab red')
        self.laptop.run('sync')
        self.assertFalse(self.laptop.controller.dirty)
        self.pc.run('sync')
        self.assertEqual(self.pc.pages()['a'], 'edited on laptop')

    def test_pull_only_commits_locally_and_push_sends_it(self):
        self.pc.set_pages([page('a', 'one')])
        result = self.pc.run('pull')
        self.assertFalse(result['pushed'])
        self.pc.controller.refresh_status()
        self.assertTrue(self.pc.controller.dirty, 'committed but not pushed: still red')
        self.assertGreaterEqual(self.pc.controller.status()['git']['ahead'], 1)
        result = self.pc.run('push')
        self.assertTrue(result['pushed'])
        self.pc.controller.refresh_status()
        self.assertFalse(self.pc.controller.dirty)

    def test_conflict_is_reported_not_hidden(self):
        self.pc.set_pages([page('a', 'start')]); self.pc.run('sync'); self.laptop.run('sync')
        self.pc.set_pages([page('a', 'pc')]); self.pc.run('sync')
        self.laptop.set_pages([page('a', 'laptop')])
        result = self.laptop.run('sync')
        self.assertEqual(len(result['conflicts']), 1)
        self.assertEqual(sorted(self.laptop.pages().values()), ['laptop', 'pc'])

    def test_offline_keeps_local_work_and_reports_the_reason(self):
        self.pc.set_pages([page('a', 'one')]); self.pc.run('sync')
        subprocess.run(['git', 'remote', 'set-url', 'origin', os.path.join(self.dir.name, 'gone.git')], cwd=self.pc.folder, check=True)
        self.pc.set_pages([page('a', 'one'), page('b', 'offline')])
        result = self.pc.run('sync')
        self.assertTrue(result['ok'])
        self.assertIsNotNone(result['error'])
        self.assertFalse(result['pushed'])
        self.assertTrue(os.path.exists(os.path.join(self.pc.folder, 'notebook', 'pages', 'b.json')))
        self.pc.controller.refresh_status()
        self.assertTrue(self.pc.controller.dirty)
        self.assertTrue(self.pc.controller.status()['error'])

    def test_failed_save_leaves_state_untouched_so_the_next_run_retries(self):
        self.pc.set_pages([page('a', 'one')]); self.pc.run('sync'); self.laptop.run('sync')
        self.pc.set_pages([page('a', 'new on pc')]); self.pc.run('sync')
        self.laptop.io.fail_write = True
        before = json.loads(pathlib.Path(self.laptop.io.state_file).read_text())
        result = self.laptop.run('sync')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'][0], 'save')
        self.assertEqual(json.loads(pathlib.Path(self.laptop.io.state_file).read_text()), before)
        self.assertEqual(self.laptop.pages()['a'], 'one', 'the database is unchanged')
        self.laptop.io.fail_write = False
        self.assertTrue(self.laptop.run('sync')['ok'])
        self.assertEqual(self.laptop.pages()['a'], 'new on pc')

    def test_unsaved_open_page_blocks_the_sync(self):
        self.pc.set_pages([page('a')])
        self.pc.io.fail_flush = True
        result = self.pc.run('sync')
        self.assertEqual((result['ok'], result['error'][0]), (False, 'flush'))
        self.assertFalse(os.path.exists(os.path.join(self.pc.folder, 'notebook')), 'nothing was exported')
        self.assertFalse(self.pc.controller.busy, 'a failure never leaves the controller locked')

    def test_flush_happens_before_and_after_git_but_not_when_disabled(self):
        self.pc.set_pages([page('a')])
        self.pc.run('sync')
        self.assertEqual(self.pc.io.flushes, 2)
        self.pc.io.flushes = 0
        self.pc.run('push', flush=False)
        self.assertEqual(self.pc.io.flushes, 0)

    def test_not_configured_busy_and_bad_kind(self):
        empty = Computer(self.dir.name, 'empty')
        empty.io.settings['sync_folder'] = ''
        self.assertEqual(empty.run('sync')['error'][0], 'not_configured')
        self.pc.controller.busy = True
        self.assertEqual(self.pc.run('sync')['error'][0], 'busy')
        self.assertTrue(self.pc.controller.busy, 'a rejected second run does not release the lock')
        with self.assertRaises(ValueError):
            self.pc.controller.run('format-disk')

    def test_categories_and_first_sync_strategy(self):
        self.pc.io.todos = json.dumps([{'id': '1', 'text': 'milk'}])
        self.pc.set_pages([page('a')])
        self.pc.controller.save_config({'sync_todos': False})
        self.pc.run('sync')
        self.assertFalse(os.path.exists(os.path.join(self.pc.folder, 'todos')))
        self.assertTrue(os.path.exists(os.path.join(self.pc.folder, 'notebook')))
        other = Computer(self.dir.name, 'other', self.remote)
        other.set_pages([page('a', 'different')])
        other.controller.save_config({'sync_first': 'remote'})
        other.run('sync')
        self.assertEqual(other.pages(), {'a': 'x'}, 'first sync can prefer the remote version')

    def make_pdf(self, computer, name, content=b'%PDF-1.4 synthetic'):
        path = os.path.join(computer.io.store + '-own', name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pathlib.Path(path).write_bytes(content)
        return path

    def pdf_library(self, computer):
        return json.loads(computer.io.pdfs)

    def test_pdfs_are_off_by_default_and_never_touched(self):
        self.assertFalse(self.pc.controller.config()['sync_pdfs'])
        path = self.make_pdf(self.pc, 'a.pdf')
        self.pc.io.pdfs = json.dumps({'version': 4, 'folders': [], 'pdfs': [{'id': 'p1', 'name': 'a.pdf', 'path': path, 'folderId': None}]})
        self.pc.set_pages([page('a')])
        self.pc.run('sync')
        self.assertFalse(os.path.exists(os.path.join(self.pc.folder, 'pdfs')), 'nothing about PDFs is exported unless enabled')
        self.assertIsNone(self.pc.controller.status()['local']['pdfs'], 'and they do not count as pending changes')

    def test_pdf_library_with_folders_and_files_reaches_the_other_computer(self):
        for computer in (self.pc, self.laptop):
            computer.controller.save_config({'sync_pdfs': True})
        a, b = self.make_pdf(self.pc, 'a.pdf', b'%PDF-A'), self.make_pdf(self.pc, 'b.pdf', b'%PDF-B')
        self.pc.io.pdfs = json.dumps({'version': 4, 'recentDecks': ['X'], 'cardCreatorSession': {'pdfPath': a}, 'folders': [{'id': 'f1', 'name': 'Anatomy'}],
                                      'pdfs': [{'id': 'p1', 'name': 'a.pdf', 'path': a, 'addedAt': '2026-01-01', 'folderId': 'f1'},
                                               {'id': 'p2', 'name': 'b.pdf', 'path': b, 'addedAt': '2026-01-02', 'folderId': None}]})
        result = self.pc.run('sync')
        self.assertTrue(result['ok'] and result['pushed'], result)
        self.assertEqual(result['files'], 2)
        exported = json.loads(pathlib.Path(self.pc.folder, 'pdfs', 'entries', 'p1.json').read_text())
        self.assertNotIn('path', exported, 'a local path is never exported')
        self.assertEqual(pathlib.Path(self.pc.folder, 'pdfs', 'files', 'p1.pdf').read_bytes(), b'%PDF-A')
        got = self.laptop.run('sync')
        self.assertTrue(got['changed']['pdfs'] and got['files'] == 2)
        library = self.pdf_library(self.laptop)
        self.assertEqual([f['name'] for f in library['folders']], ['Anatomy'])
        by_id = {p['id']: p for p in library['pdfs']}
        self.assertEqual(by_id['p1']['folderId'], 'f1', 'folder membership travels')
        self.assertTrue(by_id['p1']['path'].startswith(self.laptop.io.store), 'the file is kept in this profile')
        self.assertEqual(pathlib.Path(by_id['p2']['path']).read_bytes(), b'%PDF-B')
        self.assertEqual(library['recentDecks'], [], 'local view state is not taken over')
        self.assertIsNone(library['cardCreatorSession'])

    def test_pdf_changes_moves_deletes_and_replaced_files_follow(self):
        for computer in (self.pc, self.laptop):
            computer.controller.save_config({'sync_pdfs': True})
        a = self.make_pdf(self.pc, 'a.pdf', b'%PDF-1')
        self.pc.io.pdfs = json.dumps({'version': 4, 'folders': [{'id': 'f1', 'name': 'F'}], 'pdfs': [{'id': 'p1', 'name': 'a.pdf', 'path': a, 'folderId': None}]})
        self.pc.run('sync'); self.laptop.run('sync')
        copy_path = self.pdf_library(self.laptop)['pdfs'][0]['path']
        # moved into a folder on the laptop
        library = self.pdf_library(self.laptop); library['pdfs'][0]['folderId'] = 'f1'; self.laptop.io.pdfs = json.dumps(library)
        self.laptop.run('sync'); self.pc.run('sync')
        self.assertEqual(self.pdf_library(self.pc)['pdfs'][0]['folderId'], 'f1')
        self.assertEqual(self.pdf_library(self.pc)['pdfs'][0]['path'], a, 'the original file stays where it was')
        # the PDF itself is replaced on the pc: the laptop's copy follows, the pc's own file is never overwritten
        pathlib.Path(a).write_bytes(b'%PDF-2 longer')
        self.pc.run('sync'); self.laptop.run('sync')
        self.assertEqual(pathlib.Path(copy_path).read_bytes(), b'%PDF-2 longer')
        # deleting the entry removes the shared file and the laptop's copy, but never the pc's own file
        library = self.pdf_library(self.pc); library['pdfs'] = []; self.pc.io.pdfs = json.dumps(library)
        self.pc.run('sync'); self.laptop.run('sync')
        self.assertFalse(os.path.exists(os.path.join(self.pc.folder, 'pdfs', 'files', 'p1.pdf')))
        self.assertFalse(os.path.exists(copy_path))
        self.assertTrue(os.path.exists(a), "the user's own file is never deleted")
        self.assertEqual(self.pdf_library(self.laptop)['pdfs'], [])

    def test_large_pdfs_sync_as_entries_only_and_missing_files_are_reported(self):
        for computer in (self.pc, self.laptop):
            computer.controller.save_config({'sync_pdfs': True})
        big = self.make_pdf(self.pc, 'big.pdf', b'x' * 2048)
        self.pc.io.pdfs = json.dumps({'version': 4, 'folders': [], 'pdfs': [{'id': 'p1', 'name': 'big.pdf', 'path': big, 'folderId': None}]})
        with unittest.mock.patch.object(ns, 'MAX_PDF_SYNC_BYTES', 1024):
            result = self.pc.run('sync')
        self.assertEqual(result['oversized'], ['big.pdf'])
        self.assertFalse(os.path.exists(os.path.join(self.pc.folder, 'pdfs', 'files', 'p1.pdf')))
        got = self.laptop.run('sync')
        library = self.pdf_library(self.laptop)
        self.assertEqual([p['name'] for p in library['pdfs']], ['big.pdf'], 'the library entry still arrives')
        self.assertEqual(library['pdfs'][0]['path'], '', 'no file here: the library offers relink')
        self.assertEqual(got['missing_files'], 1)

    def test_pdf_status_and_red_tab_cover_the_library_when_enabled(self):
        self.pc.controller.save_config({'sync_pdfs': True})
        path = self.make_pdf(self.pc, 'a.pdf')
        self.pc.io.pdfs = json.dumps({'version': 4, 'folders': [{'id': 'f1', 'name': 'F'}], 'pdfs': [{'id': 'p1', 'name': 'a.pdf', 'path': path, 'folderId': 'f1'}]})
        local = self.pc.controller.status()['local']['pdfs']
        self.assertEqual(sorted(x['title'] for x in local['added']), ['F', 'a.pdf'])
        self.pc.run('sync')
        self.assertEqual(self.pc.controller.status()['local']['pdfs'], {'added': [], 'modified': [], 'deleted': []})
        self.pc.controller.refresh_status()
        self.assertFalse(self.pc.controller.dirty)

    def test_legacy_pdf_list_and_damaged_library_do_not_break_sync(self):
        self.pc.controller.save_config({'sync_pdfs': True})
        path = self.make_pdf(self.pc, 'a.pdf')
        self.pc.io.pdfs = json.dumps([{'id': 'p1', 'name': 'a.pdf', 'path': path}])        # the oldest format: a plain list
        self.assertTrue(self.pc.run('sync')['ok'])
        self.assertTrue(os.path.exists(os.path.join(self.pc.folder, 'pdfs', 'entries', 'p1.json')), 'the old list format is exported too')
        self.assertIsInstance(self.pdf_library(self.pc), list, 'and the local database is left as it was')
        self.pc.io.pdfs = 'not json'
        result = self.pc.run('sync')
        self.assertEqual((result['ok'], result['error'][0]), (False, 'unsafe'))

    def test_settings_are_validated(self):
        controller = self.pc.controller
        cfg = controller.save_config({'sync_folder': '~/x', 'sync_git': 'yes', 'sync_first': 'bogus', 'evil': 1, 'sync_auto_push': True})
        self.assertTrue(os.path.isabs(cfg['sync_folder']))
        self.assertIs(cfg['sync_git'], True, 'wrong types are ignored')
        self.assertEqual(cfg['sync_first'], 'merge')
        self.assertTrue(cfg['sync_auto_push'])
        self.assertNotIn('evil', self.pc.io.settings)
        controller.save_config({'sync_folder': 'a\x00b'})
        self.assertTrue(os.path.isabs(controller.config()['sync_folder']))
        self.assertEqual(controller.save_config('nonsense')['sync_auto_push'], True)

    def test_clone_into_chosen_folder(self):
        self.pc.set_pages([page('a', 'cloned')]); self.pc.run('sync')
        target = os.path.join(self.dir.name, 'fresh')
        fresh = Computer(self.dir.name, 'fresh', folder=target)
        out = []
        fresh.controller.clone(self.remote, out.append)
        self.assertTrue(out[0]['ok'], out)
        self.assertTrue(sg.is_repo(target))
        self.assertTrue(fresh.run('sync')['changed']['notebook'])
        self.assertEqual(fresh.pages(), {'a': 'cloned'})
        for bad in ('', '--upload-pack=evil', None, 'x' * 3000):
            out.clear()
            fresh.controller.clone(bad, out.append)
            self.assertFalse(out[0]['ok'], bad)

    def test_non_git_folder_works_like_a_plain_sync_folder(self):
        plain = Computer(self.dir.name, 'plain', folder=os.path.join(self.dir.name, 'dropbox'))
        plain.controller.save_config({'sync_git': False})
        plain.set_pages([page('a')])
        result = plain.run('sync')
        self.assertTrue(result['ok'])
        self.assertIsNone(result['error'])
        plain.controller.refresh_status()
        self.assertFalse(plain.controller.dirty)


if __name__ == '__main__':
    unittest.main()
