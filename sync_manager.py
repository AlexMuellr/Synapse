"""One sync run: pull, merge notebook and to-dos, export, commit, push (no Anki imports).

The caller (add-on glue) reads the local snapshots, passes them in, then stores the
returned bodies through the normal validated save path and persists ``state`` only after
that succeeded. Network trouble never blocks syncing locally: the merge and export still
happen and the error is reported, so the "Changes" view can show unpushed work.
"""
from __future__ import annotations

import copy
from typing import Optional

from . import notebook_sync, sync_git

CATEGORIES = ("notebook", "todos")


def sync_once(
    notebook_body: Optional[str],
    todos_body: Optional[str],
    folder: str,
    state: dict,
    categories: tuple[str, ...] = CATEGORIES,
    use_git: bool = True,
    push: bool = True,
    strategy: str = "merge",
) -> dict:
    """Returns {"state", "notebook", "todos", "pulled", "pushed", "error", "reports"}.

    ``notebook``/``todos`` are new snapshot bodies, or None when unchanged or not selected.
    Raises notebook_sync.SyncError for problems that make syncing unsafe (nothing written).
    """
    state = copy.deepcopy(state)
    out: dict = {"state": state, "notebook": None, "todos": None, "pulled": False,
                 "pushed": False, "error": None, "reports": {}}
    git_on = use_git and sync_git.available() and sync_git.is_repo(folder)
    if use_git and not git_on:
        out["error"] = ("missing", "The sync folder is not a Git repository.")
    if git_on:
        try:
            out["pulled"] = sync_git.pull(folder)["updated"]
        except sync_git.GitError as exc:
            out["error"] = (exc.kind, exc.message)
    if "notebook" in categories and notebook_body is not None:
        result = notebook_sync.sync_notebook(notebook_body, folder, state, strategy)
        out["reports"]["notebook"] = result["report"]
        out["notebook"] = result["body"] if result["changed"] else None
    if "todos" in categories and todos_body is not None:
        result = notebook_sync.sync_todos(todos_body, folder, state, strategy)
        out["reports"]["todos"] = result["report"]
        out["todos"] = result["body"] if result["changed"] else None
    if git_on:
        try:
            sync_git.commit_all(folder, "Synapse sync")
            if push and (out["error"] is None):
                sync_git.push(folder)
                out["pushed"] = True
        except sync_git.GitError as exc:
            out["error"] = (exc.kind, exc.message)
    return out
