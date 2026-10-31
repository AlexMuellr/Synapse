"""Notebook/to-do folder sync: export, three-way merge, conflicts, deletions. Synthetic data."""
import copy
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('notebook_sync', ROOT / 'notebook_sync.py')
ns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ns)


def page(pid, title='T', text='x', **extra):
    return {'id': pid, 'title': title, 'icon': '', 'blocks': [{'type': 'text', 'html': text}], **extra}


def notebook(*pages, folders=None, active=None, **extra):
    data = {'activePageId': active or (pages[0]['id'] if pages else ''), 'sidebarOpen': True, 'pages': list(pages), **extra}
    if folders is not None:
        data['folders'] = folders
    return json.dumps(data)


class Device:
    """One computer: its own database snapshot and sync state, a shared folder."""
    def __init__(self, folder, body):
        self.folder, self.body, self.state = folder, body, {'version': 1, 'device': 'dev' + str(id(self))[-3:], 'base': {}, 'last_sync': 0}

    def data(self):
        return json.loads(self.body)

    def ids(self):
        return [p['id'] for p in self.data()['pages']]

    def sync(self, **kw):
        state = copy.deepcopy(self.state)
        result = ns.sync_notebook(self.body, self.folder, state, **kw)
        self.body, self.state = result['body'], state
        return result['report']

    def edit(self, pid, text):
        data = self.data()
        next(p for p in data['pages'] if p['id'] == pid)['blocks'][0]['html'] = text
        self.body = json.dumps(data)

    def text(self, pid):
        return next(p for p in self.data()['pages'] if p['id'] == pid)['blocks'][0]['html']


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.folder = os.path.join(self.dir.name, 'sync')

    def pair(self):
        pc = Device(self.folder, notebook(page('a', 'Alpha', 'one'), page('b', 'Beta', 'two')))
        pc.sync()
        laptop = Device(self.folder, notebook())
        laptop.sync()
        return pc, laptop

    def test_first_sync_exports_one_file_per_page(self):
        pc = Device(self.folder, notebook(page('a', 'Alpha'), page('b/../evil', 'Evil'), folders=[{'id': 'f1', 'name': 'S'}]))
        pc.sync()
        files = sorted(os.listdir(os.path.join(self.folder, 'notebook', 'pages')))
        self.assertEqual(len(files), 2)
        self.assertIn('a.json', files)
        self.assertTrue(all('/' not in f and '..' not in f for f in files))
        self.assertTrue(os.path.exists(os.path.join(self.folder, 'notebook', 'folders', 'f1.json')))
        self.assertEqual(json.loads(pathlib.Path(self.folder, 'synapse-sync.json').read_text()), {'version': 1})

    def test_second_device_receives_everything_and_keeps_local_view_state(self):
        pc, laptop = self.pair()
        self.assertEqual(sorted(laptop.ids()), ['a', 'b'])
        self.assertEqual(laptop.text('a'), 'one')
        pc_data = json.loads(notebook(page('a'), active='a', sidebarOpen=False))
        self.assertEqual(laptop.data()['sidebarOpen'], True)

    def test_remote_change_is_taken_when_local_is_untouched(self):
        pc, laptop = self.pair()
        pc.edit('a', 'changed on pc'); pc.sync()
        report = laptop.sync()
        self.assertEqual(laptop.text('a'), 'changed on pc')
        self.assertEqual(report['taken'], 1)
        self.assertEqual(report['conflicts'], [])

    def test_local_change_survives_and_is_exported(self):
        pc, laptop = self.pair()
        laptop.edit('b', 'laptop edit'); laptop.sync()
        pc.sync()
        self.assertEqual(pc.text('b'), 'laptop edit')

    def test_conflict_keeps_both_versions_and_is_idempotent(self):
        pc, laptop = self.pair()
        pc.edit('a', 'pc version'); pc.sync()
        laptop.edit('a', 'laptop version')
        report = laptop.sync()
        self.assertEqual(len(report['conflicts']), 1)
        self.assertEqual(laptop.text('a'), 'laptop version')
        [copy_id] = [i for i in laptop.ids() if i not in ('a', 'b')]
        self.assertEqual(laptop.text(copy_id), 'pc version')
        self.assertTrue(next(p for p in laptop.data()['pages'] if p['id'] == copy_id)['title'].endswith('(conflict)'))
        laptop.sync(); laptop.sync()
        self.assertEqual(len(laptop.ids()), 3, 'repeated syncs never multiply the copy')
        pc.sync()
        self.assertEqual(sorted(pc.ids()), sorted(laptop.ids()))
        self.assertEqual(pc.text('a'), 'laptop version')

    def test_deletion_propagates_only_if_untouched(self):
        pc, laptop = self.pair()
        pc.body = json.dumps({**pc.data(), 'pages': [p for p in pc.data()['pages'] if p['id'] != 'b']})
        pc.sync()
        laptop.sync()
        self.assertEqual(laptop.ids(), ['a'])
        # delete on one device while the other changed the page: the change wins
        pc, laptop = Device(self.folder + '2', pc.body), None
        pc.sync()
        laptop = Device(self.folder + '2', notebook())
        laptop.sync()
        pc.body = json.dumps({**pc.data(), 'pages': []})
        laptop.edit('a', 'still needed')
        pc.sync()
        laptop.sync()
        self.assertEqual(laptop.ids(), ['a'])
        self.assertEqual(laptop.text('a'), 'still needed')
        pc.sync()
        self.assertEqual(pc.ids(), ['a'], 'the survivor returns to the other device')

    def test_page_deleted_here_but_edited_there_comes_back(self):
        pc, laptop = self.pair()
        laptop.body = json.dumps({**laptop.data(), 'pages': [p for p in laptop.data()['pages'] if p['id'] != 'b']})
        pc.edit('b', 'edited meanwhile'); pc.sync()
        laptop.sync()
        self.assertEqual(sorted(laptop.ids()), ['a', 'b'])
        self.assertEqual(laptop.text('b'), 'edited meanwhile')
        pc.sync()
        self.assertEqual(sorted(pc.ids()), ['a', 'b'])

    def test_deleting_an_untouched_page_removes_it_everywhere(self):
        pc, laptop = self.pair()
        laptop.body = json.dumps({**laptop.data(), 'pages': [p for p in laptop.data()['pages'] if p['id'] != 'b']})
        laptop.sync(); pc.sync()
        self.assertEqual(pc.ids(), ['a'])
        self.assertFalse(os.path.exists(os.path.join(self.folder, 'notebook', 'pages', 'b.json')))

    def test_new_pages_on_both_sides_are_combined_and_unknown_fields_kept(self):
        pc, laptop = self.pair()
        pc.body = json.dumps({**pc.data(), 'pages': pc.data()['pages'] + [page('c', 'C', 'pc new', custom={'keep': True})]})
        laptop.body = json.dumps({**laptop.data(), 'pages': laptop.data()['pages'] + [page('d', 'D', 'laptop new')]})
        pc.sync(); laptop.sync(); pc.sync()
        self.assertEqual(sorted(pc.ids()), ['a', 'b', 'c', 'd'])
        self.assertEqual(sorted(laptop.ids()), ['a', 'b', 'c', 'd'])
        self.assertEqual(next(p for p in laptop.data()['pages'] if p['id'] == 'c')['custom'], {'keep': True})

    def test_first_sync_strategies_for_differing_pages(self):
        Device(self.folder, notebook(page('a', 'Alpha', 'remote'))).sync()
        for strategy, expected_text, count in (('merge', 'local', 2), ('local', 'local', 1), ('remote', 'remote', 1)):
            device = Device(self.folder, notebook(page('a', 'Alpha', 'local')))
            state_before = os.listdir(os.path.join(self.folder, 'notebook', 'pages'))
            device.state = {'version': 1, 'device': 'x', 'base': {}, 'last_sync': 0}
            result = ns.sync_notebook(device.body, self.folder, device.state, strategy=strategy)
            device.body = result['body']
            self.assertEqual(device.text('a'), expected_text, strategy)
            self.assertEqual(len(device.ids()), count, strategy)
            # reset folder for the next strategy
            for name in os.listdir(os.path.join(self.folder, 'notebook', 'pages')):
                os.remove(os.path.join(self.folder, 'notebook', 'pages', name))
            Device(self.folder, notebook(page('a', 'Alpha', 'remote'))).sync()

    def test_active_page_is_repaired_when_it_was_deleted_elsewhere(self):
        pc, laptop = self.pair()
        pc.body = json.dumps({**pc.data(), 'pages': [p for p in pc.data()['pages'] if p['id'] != 'b']})
        pc.sync()
        laptop.body = json.dumps({**laptop.data(), 'activePageId': 'b'})
        laptop.sync()
        self.assertEqual(laptop.data()['activePageId'], 'a')

    def test_folders_templates_and_unsyncable_pages(self):
        body = notebook(page('a'), {'title': 'no id'}, page('a', 'duplicate id'),
                        folders=[{'id': 'f1', 'name': 'Anatomy', 'open': True}],
                        notebookTemplates=[{'id': 't1', 'name': 'Tpl', 'page': page('x')}])
        pc = Device(self.folder, body); pc.sync()
        self.assertEqual(len(pc.data()['pages']), 3, 'nothing is lost locally')
        laptop = Device(self.folder, notebook()); laptop.sync()
        self.assertEqual([f['name'] for f in laptop.data()['folders']], ['Anatomy'])
        self.assertEqual([t['name'] for t in laptop.data()['notebookTemplates']], ['Tpl'])
        self.assertEqual(laptop.ids(), ['a'], 'only the syncable page is shared')

    def test_damaged_remote_files_are_skipped_not_fatal(self):
        pc, laptop = self.pair()
        pages_dir = os.path.join(self.folder, 'notebook', 'pages')
        pathlib.Path(pages_dir, 'broken.json').write_text('<<<<<<< HEAD\n{')
        pathlib.Path(pages_dir, 'wrongname.json').write_text(json.dumps(page('zzz')))
        report = laptop.sync()
        self.assertEqual(sorted(report['skipped']), ['notebook/pages/broken.json', 'notebook/pages/wrongname.json'])
        self.assertEqual(sorted(laptop.ids()), ['a', 'b'])

    def test_refuses_newer_folder_legacy_and_invalid_data(self):
        os.makedirs(self.folder)
        pathlib.Path(self.folder, 'synapse-sync.json').write_text(json.dumps({'version': 99}))
        with self.assertRaises(ns.SyncError):
            ns.sync_notebook(notebook(page('a')), self.folder, {'device': 'x', 'base': {}})
        other = os.path.join(self.dir.name, 'other')
        for bad in ('not json', json.dumps([{'type': 'text', 'html': 'old'}]), json.dumps({'blocks': []})):
            with self.assertRaises(ns.SyncError):
                ns.sync_notebook(bad, other, {'device': 'x', 'base': {}})
        self.assertFalse(os.path.exists(other), 'nothing is written on refusal')

    def test_fresh_empty_database_is_an_empty_notebook(self):
        laptop = Device(self.folder, '[]')
        pc = Device(self.folder, notebook(page('a', 'one')))
        pc.sync()
        laptop.sync()
        self.assertEqual(laptop.ids(), ['a'], 'a never-opened notebook receives the remote pages')
        lonely = Device(os.path.join(self.dir.name, 'other'), '[]')
        report = lonely.sync()
        self.assertEqual(lonely.ids(), [])
        self.assertEqual(report['conflicts'], [])

    def test_unchanged_sync_changes_nothing_on_disk(self):
        pc, laptop = self.pair()
        pages_dir = os.path.join(self.folder, 'notebook', 'pages')
        before = {n: os.stat(os.path.join(pages_dir, n)).st_mtime_ns for n in os.listdir(pages_dir)}
        result = ns.sync_notebook(laptop.body, self.folder, copy.deepcopy(laptop.state))
        self.assertFalse(result['changed'])
        after = {n: os.stat(os.path.join(pages_dir, n)).st_mtime_ns for n in os.listdir(pages_dir)}
        self.assertEqual(before, after)

    def test_local_changes_report(self):
        pc, laptop = self.pair()
        laptop.edit('a', 'edited')
        laptop.body = json.dumps({**laptop.data(), 'pages': [p for p in laptop.data()['pages'] if p['id'] != 'b'] + [page('n', 'New')]})
        changes = ns.local_changes(laptop.body, laptop.state)
        self.assertEqual([c['id'] for c in changes['modified']], ['a'])
        self.assertEqual([c['id'] for c in changes['added']], ['n'])
        self.assertEqual([c['id'] for c in changes['deleted']], ['b'])

    def test_state_file_roundtrip_and_device_id(self):
        path = os.path.join(self.dir.name, 'state.json')
        state = ns.load_state(path)
        self.assertEqual(len(state['device']), 8)
        ns.save_state(path, state)
        self.assertEqual(ns.load_state(path)['device'], state['device'])
        pathlib.Path(path).write_text('garbage')
        self.assertEqual(ns.load_state(path)['base'], {})


class TodoTests(unittest.TestCase):
    def test_tasks_merge_per_task(self):
        with tempfile.TemporaryDirectory() as folder:
            def sync(body, state):
                result = ns.sync_todos(json.dumps(body), folder, state)
                return json.loads(result['body'])
            pc, lap = {'device': 'p', 'base': {}}, {'device': 'l', 'base': {}}
            tasks = [{'id': '1', 'text': 'milk', 'done': False}, {'id': '2', 'text': 'bread', 'done': False}]
            sync(tasks, pc)
            got = sync([], lap)
            self.assertEqual([t['text'] for t in got], ['milk', 'bread'])
            got[0]['done'] = True
            tasks_pc = tasks + [{'id': '3', 'text': 'eggs', 'done': False}]
            sync(got, lap)
            merged = sync(tasks_pc, pc)
            self.assertEqual({t['id']: t['done'] for t in merged}, {'1': True, '2': False, '3': False})
            self.assertEqual(len(sync(merged, lap)), 3)


if __name__ == '__main__':
    unittest.main()
