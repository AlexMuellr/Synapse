"""Git transport and a full two-computer cycle with real Git repositories in a temp dir."""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
package = types.ModuleType('synapse_git_pkg')
package.__path__ = [str(ROOT)]
sys.modules['synapse_git_pkg'] = package


def load(name):
    spec = importlib.util.spec_from_file_location(f'synapse_git_pkg.{name}', ROOT / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[f'synapse_git_pkg.{name}'] = module
    spec.loader.exec_module(module)
    return module


ns, sg, sm = load('notebook_sync'), load('sync_git'), load('sync_manager')
HAVE_GIT = shutil.which('git') is not None


def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=True,
                          env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}).stdout.strip()


def page(pid, text='x'):
    return {'id': pid, 'title': pid.upper(), 'icon': '', 'blocks': [{'type': 'text', 'html': text}]}


class Computer:
    def __init__(self, base, name, remote):
        self.folder = os.path.join(base, name)
        git(base, 'clone', '-q', remote, self.folder)
        self.state = {'version': 1, 'device': name[:8], 'base': {}, 'last_sync': 0}
        self.notebook = json.dumps({'activePageId': '', 'pages': []})
        self.todos = '[]'

    def pages(self):
        return {p['id']: p['blocks'][0]['html'] for p in json.loads(self.notebook)['pages']}

    def set_pages(self, pages):
        data = json.loads(self.notebook)
        data['pages'] = pages
        data['activePageId'] = pages[0]['id'] if pages else ''
        self.notebook = json.dumps(data)

    def sync(self, **kw):
        result = sm.sync_once(self.notebook, self.todos, self.folder, self.state, **kw)
        if result['notebook'] is not None:
            self.notebook = result['notebook']
        if result['todos'] is not None:
            self.todos = result['todos']
        self.state = result['state']
        return result


@unittest.skipUnless(HAVE_GIT, 'git is not installed')
class GitSyncTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.remote = os.path.join(self.dir.name, 'remote.git')
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', self.remote], check=True)
        self.pc = Computer(self.dir.name, 'pc', self.remote)
        self.laptop = Computer(self.dir.name, 'laptop', self.remote)

    def test_status_reports_changes_ahead_and_behind_without_network(self):
        self.pc.set_pages([page('a', 'one')])
        self.pc.sync(push=False)
        info = sg.status(self.pc.folder)
        self.assertTrue(info['repo'])
        self.assertEqual(info['changed'], [])
        self.assertTrue(info['remote'])
        self.pc.sync()
        info = sg.status(self.pc.folder)
        self.assertEqual((info['ahead'], info['behind']), (0, 0))
        (pathlib.Path(self.pc.folder) / 'notebook' / 'pages' / 'zz.json').write_text('{}')
        self.assertIn(('A', 'notebook/pages/zz.json'), sg.status(self.pc.folder)['changed'])

    def test_full_cycle_between_two_computers(self):
        self.pc.set_pages([page('a', 'one'), page('b', 'two')])
        self.assertTrue(self.pc.sync()['pushed'])
        result = self.laptop.sync()
        self.assertTrue(result['pulled'])
        self.assertEqual(self.laptop.pages(), {'a': 'one', 'b': 'two'})
        pages = json.loads(self.laptop.notebook)['pages']
        pages[0]['blocks'][0]['html'] = 'laptop edit'
        self.laptop.set_pages(pages)
        self.laptop.sync()
        self.pc.sync()
        self.assertEqual(self.pc.pages()['a'], 'laptop edit')

    def test_same_page_edited_on_both_gives_conflict_copy_not_data_loss(self):
        self.pc.set_pages([page('a', 'start')]); self.pc.sync(); self.laptop.sync()
        for computer, text in ((self.pc, 'pc text'), (self.laptop, 'laptop text')):
            pages = json.loads(computer.notebook)['pages']
            pages[0]['blocks'][0]['html'] = text
            computer.set_pages(pages)
        self.pc.sync()
        self.laptop.sync()
        texts = sorted(self.laptop.pages().values())
        self.assertEqual(texts, ['laptop text', 'pc text'])
        self.laptop.sync(); self.pc.sync()
        self.assertEqual(sorted(self.pc.pages().values()), texts)
        self.assertEqual(self.pc.pages()['a'], self.laptop.pages()['a'], 'both computers end in the same state')
        for path in pathlib.Path(self.pc.folder).rglob('*.json'):
            json.loads(path.read_text())          # no git conflict markers ever reach a file

    def test_offline_sync_still_exports_locally_and_pushes_later(self):
        self.pc.set_pages([page('a', 'one')]); self.pc.sync()
        git(self.pc.folder, 'remote', 'set-url', 'origin', os.path.join(self.dir.name, 'missing.git'))
        self.pc.set_pages([page('a', 'one'), page('n', 'offline note')])
        result = self.pc.sync()
        self.assertIsNotNone(result['error'])
        self.assertFalse(result['pushed'])
        self.assertTrue((pathlib.Path(self.pc.folder) / 'notebook' / 'pages' / 'n.json').exists())
        self.assertEqual(sg.status(self.pc.folder)['changed'], [], 'work is committed locally')
        git(self.pc.folder, 'remote', 'set-url', 'origin', self.remote)
        self.assertTrue(self.pc.sync()['pushed'])
        self.assertEqual(self.laptop.sync()['error'], None)
        self.assertIn('n', self.laptop.pages())

    def test_deletion_travels_through_git(self):
        self.pc.set_pages([page('a', 'one'), page('b', 'two')]); self.pc.sync(); self.laptop.sync()
        self.pc.set_pages([page('a', 'one')]); self.pc.sync()
        self.laptop.sync()
        self.assertEqual(list(self.laptop.pages()), ['a'])
        self.assertFalse((pathlib.Path(self.laptop.folder) / 'notebook' / 'pages' / 'b.json').exists())

    def test_non_git_folder_still_syncs_and_reports(self):
        plain = os.path.join(self.dir.name, 'dropbox')
        state = {'device': 'x', 'base': {}}
        result = sm.sync_once(json.dumps({'pages': [page('a')]}), '[]', plain, state)
        self.assertEqual(result['error'][0], 'missing')
        self.assertTrue((pathlib.Path(plain) / 'notebook' / 'pages' / 'a.json').exists())
        plain_only = sm.sync_once(json.dumps({'pages': [page('a')]}), '[]', plain, result['state'], use_git=False)
        self.assertIsNone(plain_only['error'])

    def test_categories_can_be_switched_off(self):
        self.pc.todos = json.dumps([{'id': '1', 'text': 'milk'}])
        self.pc.set_pages([page('a')])
        self.pc.sync(categories=('notebook',))
        self.assertFalse((pathlib.Path(self.pc.folder) / 'todos').exists())
        self.assertTrue((pathlib.Path(self.pc.folder) / 'notebook').exists())

    def test_attribute_rules_are_added_to_a_repository_from_an_older_version(self):
        old = pathlib.Path(self.pc.folder, '.gitattributes')
        old.write_text('*.json -merge\n*.json -diff\n')
        self.pc.set_pages([page('a')])
        self.pc.sync(push=False)
        self.assertIn('*.pdf binary', old.read_text())
        self.assertEqual(old.read_text().count('*.json -merge'), 1, 'existing rules are not duplicated')

    def test_git_errors_are_classified_and_never_prompt(self):
        with self.assertRaises(sg.GitError) as ctx:
            sg.run(['fetch', os.path.join(self.dir.name, 'nope.git')], self.pc.folder, 10)
        self.assertIn(ctx.exception.kind, ('offline', 'other'))
        self.assertEqual(sg._classify('fatal: Authentication failed for https://x'), 'auth')
        self.assertEqual(sg._classify('fatal: could not read Username: terminal prompts disabled'), 'auth')
        self.assertEqual(sg._classify('ssh: Could not resolve host: github.com'), 'offline')
        self.assertEqual(sg._classify('! [rejected] main -> main (fetch first)'), 'rejected')


if __name__ == '__main__':
    unittest.main()
