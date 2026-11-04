# Notebook sync between computers

Keeps the notebook and the to-do list the same on several computers, through a Git repository or any shared folder (Dropbox, Syncthing…). It lives in the **Changes** tab, the fourth tab next to Notebook, To-do and PDF.

## Principles
- **The SQLite file never leaves the computer.** Each notebook snapshot stores the whole state in one row, so file-level or row-level database sync can only ever say "all of A or all of B". Instead every page, section, template and task is exported as its own small JSON file, and Git or the file-sync tool only carries that folder.
- **Nothing is dropped silently.** An entry changed on one side survives a deletion on the other. Real conflicts keep both versions.
- **Network trouble never blocks local work.** Merge and export always happen; a failed pull or push is reported and retried next time.
- **The database is only written through the normal validated save path** (`_save_snapshot`), after the open page has been saved.

## Setup
1. Create a private, empty repository (or pick a shared folder).
2. **Changes → Setup**: choose an empty sync folder, paste the repository address and use **Download repository**. Save the settings and press **Sync now**.
3. Repeat on the other computer. "First sync on a new computer" decides what happens to pages that exist on both sides and differ: keep both (default, never loses data), prefer this computer, or prefer the sync folder.

Git is run non-interactively: it needs an SSH key or a saved login (credential helper), because the tab cannot ask for a password. Use a private repository; notes including pictures are uploaded.

## Settings
Stored per profile in `SynapsePro_Data/addon_settings.json`.

| Key | Default | Meaning |
| --- | --- | --- |
| `sync_folder` | empty | The sync folder. Empty = sync is off |
| `sync_git` | on | Use Git. Off for Dropbox and similar tools |
| `sync_auto_pull` | on | Pull and merge shortly after Anki starts |
| `sync_auto_push` | off | Push when Anki closes (10 s time limit) |
| `sync_notebook` / `sync_todos` | on | What is synced |
| `sync_pdfs` | **off** | PDF library, folders and files (see below). Off by default because files are large |
| `sync_first` | `merge` | `merge`, `local` or `remote`; only used for the very first sync |

Progress, mind maps, add-on settings and music are **not** synced yet. API keys are never synced.

## Folder layout
```
synapse-sync.json                format version, checked before anything is written
notebook/pages/<id>.json         one file per page
notebook/folders/<id>.json       sections
notebook/templates/<id>.json     page templates
notebook/pages-deleted.json      ids of deleted pages (also folders-, templates-)
todos/tasks/<id>.json            one file per task
todos/tasks-deleted.json
pdfs/entries/<id>.json           PDF library entries (without the local path)
pdfs/folders/<id>.json           PDF folders
pdfs/files/<id>.pdf              the PDF files themselves (only with sync_pdfs)
pdfs/entries-deleted.json  pdfs/folders-deleted.json
.gitattributes  .gitignore       written when Git is used
```
File names are the id when it is harmless (`[A-Za-z0-9][A-Za-z0-9._-]{0,99}`), otherwise `x-<sha1>`, so an id can never become a path. JSON is written canonically (sorted keys) and only when the content changed, which keeps Git diffs small.

Not part of the files: view state at the top of the snapshot (open page, sidebar) and unknown top-level keys stay local. Pages without an id and duplicate ids stay local and untouched. A notebook that still uses the old block-list format must be opened once; an empty, never-opened notebook counts as empty.

## PDFs (optional)
Turn on **PDFs** under *What to sync*. Entries and folders merge exactly like pages; a folder move or rename is just another entry change.
- An entry's `path` only means something on its own computer, so it is **never exported**. Reading position of the viewer, recent decks and the Card Creator session stay local too.
- **Files:** your own file is copied to `pdfs/files/<id>.pdf` (and re-copied when it changes). On another computer a file that only exists in the sync folder is copied into `SynapsePro_Data/synced_pdfs/` and the entry points there. Files you added yourself are never overwritten or deleted by sync; only copies sync made are refreshed or removed (when the entry is deleted everywhere).
- **Size:** files above 50 MB (`MAX_PDF_SYNC_BYTES`) sync as library entries only; the result message says how many. An entry without a file on a computer appears as missing in the PDF tab and can be relinked there.
- **Repository size:** every version of a PDF stays in Git history. For many or large PDFs use Git LFS (`git lfs track "*.pdf"` in the sync folder) or let Dropbox carry the folder (Git off). `.gitattributes` marks `*.pdf` as binary; repositories from older versions get the rule added automatically.
- The Changes tab lists PDF entries and folders next to pages and tasks, and the tab turns red for unsynced PDF changes.

## Merge rules
Each device keeps `SynapsePro_Data/sync_state.json` (device id, the hash of every entry as of the last sync, time of last sync). It is never synced. For every entry, local (database), remote (folder) and base (last sync) are compared:

| Local | Remote | Result |
| --- | --- | --- |
| equal | equal | keep |
| unchanged | changed | take remote |
| changed | unchanged | keep local |
| changed | changed | keep local, remote version becomes a copy `<id>-conflict-<hash>` titled "… (conflict)" |
| unchanged | deleted | delete locally |
| changed | deleted | keep local, remove the tombstone |
| deleted | unchanged | delete (tombstone) |
| deleted | changed | the changed version comes back |

Repeated syncs never multiply a conflict copy. Damaged files (for example Git conflict markers) are skipped and reported.

## A sync run (`sync_service.SyncController.run`)
Kinds: **sync** (pull, merge, push), **pull** (pull, merge, commit) and **push** (merge, commit, push).
1. Save the page that is open (`_flush_current_state`); abort with a message if that fails.
2. Git pull in the background: commit local files first, fetch, merge the upstream branch. `*.json -merge` makes a file changed on both sides a Git conflict, which is resolved by taking the remote file; the real decision follows in step 4.
3. Save the open page again (it may have changed while Git was working).
4. Merge (`sync_manager.merge`) with the current database snapshots, export the folder.
5. Store changed bodies with `_save_snapshot`. Only after that succeeded is `sync_state.json` written; a failed save leaves the state untouched, so the next run starts from the same base.
6. Reload the open page if data changed.
7. Commit and push in the background.

Git commands run without a shell or prompts (`GIT_TERMINAL_PROMPT=0`, SSH `BatchMode`), with a 30 s time limit (10 s when closing). Errors are classified as `offline`, `auth`, `rejected`, `timeout`, `missing` or `other`.

## Changes tab
- Status: not set up / everything synced / changes not pushed, branch, last sync, commits to push and to pull.
- **Not yet synced** lists new, changed and deleted pages and tasks; **Files in the sync folder** lists uncommitted files.
- Buttons: **Sync now**, **Pull**, **Commit & push** (a rejected push asks you to pull first).
- The tab turns **red** while work exists that is not in the remote: local changes not yet exported, uncommitted files, or commits ahead of the upstream.
- At startup `_run_sync_autopull` runs 2.5 s after the profile opens. When Anki closes, `_sync_final_push` runs after the open page has been saved. If Anki exits faster than that save, the push is skipped and the tab stays red until the next start.

## Code map
| File | Role |
| --- | --- |
| `notebook_sync.py` | Entry files, three-way merge, PDF files, state file (pure, no Anki) |
| `sync_git.py` | Non-interactive Git wrapper: status, commit, pull, push, clone |
| `sync_manager.py` | The phases `pull`, `merge`, `publish` and `sync_once` |
| `sync_service.py` | `SyncController`: settings, status, runs; Anki access through an `io` object |
| `notebook_sidebar.py` | `_SyncIO` (settings, database, page), the `sync` tool, nav tab, auto-pull and final push |
| `web_notebook/sync.html` | The Changes tab |

## Tests
`test_notebook_sync` (merge rules, files, damaged data), `test_sync_git` and `test_sync_service` (real Git repositories in a temp dir, two computers, offline, conflicts, failed saves), `test_sync_integration` (the Anki glue with a stubbed Anki, a real SQLite file and real Git), `web_sync_tab.cjs` (the page).

## Known limits
- Not tested against real remotes or on Windows; only local repositories were used.
- Pages are the smallest unit: two edits of the same page on two computers become two pages, not one merged page.
- Pictures are stored inside pages, so the repository grows with them; PDF files (if enabled) grow it much faster.
