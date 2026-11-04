"""One sync run: pull, merge notebook and to-dos, export, commit, push (no Anki imports).

The work is split into three phases so the caller can keep network access off the GUI thread
and database writes on it:

    pull()     network; fetches and merges the Git remote into the sync folder
    merge()    local files only; three-way merge with the database snapshots, exports the result
    publish()  network; commits the folder and pushes it

The caller stores the bodies returned by merge() through the normal validated save path and
persists the returned state only after that succeeded. Network trouble never blocks syncing
locally: merge and export still happen and the error is reported, so the "Changes" view can
show unpushed work.
"""
from __future__ import annotations

import copy
from typing import Optional

from . import notebook_sync, sync_git

CATEGORIES = ("notebook", "todos", "pdfs")


def git_ready(folder: str, use_git: bool) -> bool:
    return bool(use_git and sync_git.available() and sync_git.is_repo(folder))


def pull(folder: str, use_git: bool = True, timeout: float = sync_git.DEFAULT_TIMEOUT) -> dict:
    """Fetch and merge the remote. Returns {"pulled": bool, "error": (kind, message) or None}."""
    out: dict = {"pulled": False, "error": None}
    if not use_git:
        return out
    if not git_ready(folder, use_git):
        out["error"] = ("missing", "The sync folder is not a Git repository.")
        return out
    try:
        out["pulled"] = sync_git.pull(folder, timeout)["updated"]
    except sync_git.GitError as exc:
        out["error"] = (exc.kind, exc.message)
    return out


def merge(
    notebook_body: Optional[str],
    todos_body: Optional[str],
    folder: str,
    state: dict,
    categories: tuple[str, ...] = CATEGORIES,
    strategy: str = "merge",
    pdfs_body: Optional[str] = None,
    pdf_store: str = "",
) -> dict:
    """Merge the snapshots with the folder and export. Returns {"state", "notebook", "todos", "pdfs", "reports"}.

    ``notebook``/``todos``/``pdfs`` are new snapshot bodies, or None when unchanged or not selected.
    ``pdf_store`` is where PDF files that arrive from another computer are kept.
    Raises notebook_sync.SyncError for problems that make syncing unsafe (nothing written).
    """
    state = copy.deepcopy(state)
    out: dict = {"state": state, "notebook": None, "todos": None, "pdfs": None, "reports": {}}
    if "notebook" in categories and notebook_body is not None:
        result = notebook_sync.sync_notebook(notebook_body, folder, state, strategy)
        out["reports"]["notebook"] = result["report"]
        out["notebook"] = result["body"] if result["changed"] else None
    if "todos" in categories and todos_body is not None:
        result = notebook_sync.sync_todos(todos_body, folder, state, strategy)
        out["reports"]["todos"] = result["report"]
        out["todos"] = result["body"] if result["changed"] else None
    if "pdfs" in categories and pdfs_body is not None and pdf_store:
        result = notebook_sync.sync_pdfs(pdfs_body, folder, state, pdf_store, strategy)
        out["reports"]["pdfs"] = result["report"]
        out["pdfs"] = result["body"] if result["changed"] else None
    return out


def publish(folder: str, use_git: bool = True, push: bool = True, message: str = "Synapse sync",
            timeout: float = sync_git.DEFAULT_TIMEOUT) -> dict:
    """Commit the folder and push it. Returns {"committed", "pushed", "error": (kind, message) or None}."""
    out: dict = {"committed": False, "pushed": False, "error": None}
    if not git_ready(folder, use_git):
        if use_git:
            out["error"] = ("missing", "The sync folder is not a Git repository.")
        return out
    try:
        out["committed"] = sync_git.commit_all(folder, message)
        if push:
            sync_git.push(folder, timeout)
            out["pushed"] = True
    except sync_git.GitError as exc:
        out["error"] = (exc.kind, exc.message)
    return out


def sync_once(
    notebook_body: Optional[str],
    todos_body: Optional[str],
    folder: str,
    state: dict,
    categories: tuple[str, ...] = CATEGORIES,
    use_git: bool = True,
    push: bool = True,
    strategy: str = "merge",
    pdfs_body: Optional[str] = None,
    pdf_store: str = "",
) -> dict:
    """All phases in one call. Returns {"state", "notebook", "todos", "pdfs", "pulled", "pushed", "error", "reports"}."""
    pulled = pull(folder, use_git)
    merged = merge(notebook_body, todos_body, folder, state, categories, strategy, pdfs_body, pdf_store)
    error = pulled["error"]
    published = publish(folder, use_git, push and error is None)
    return {
        "state": merged["state"], "notebook": merged["notebook"], "todos": merged["todos"], "pdfs": merged["pdfs"],
        "reports": merged["reports"], "pulled": pulled["pulled"], "pushed": published["pushed"],
        "error": error or published["error"],
    }
