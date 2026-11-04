"""Sync controller: settings, status and the sync run, independent of Anki.

Everything that touches Anki or Qt comes in through the ``io`` object (see notebook_sidebar's
``_SyncIO``), so the whole flow can be tested with real Git repositories and fakes:

    io.get_setting(key, default) / io.set_settings({..})
    io.state_path()                      per-device state file (never synced)
    io.read_notebook() / io.read_todos() / io.read_pdfs() current snapshot bodies from the database
    io.write_notebook(body) / io.write_todos(body) / io.write_pdfs(body) -> bool   validated save path
    io.pdf_store_dir()                   where PDFs that arrive from another computer are kept
    io.flush(callback)                   save the page that is open, then callback(bool)
    io.reload()                          show the new data in the open page
    io.run_bg(fn, callback)              run fn off the GUI thread, callback(result) on it
    io.notify(status)                    push a status dict to the visible page
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional

from . import notebook_sync, sync_git, sync_manager

DEFAULTS: dict[str, Any] = {
    "sync_folder": "",
    "sync_git": True,
    "sync_auto_pull": True,
    "sync_auto_push": False,
    "sync_notebook": True,
    "sync_todos": True,
    "sync_pdfs": False,               # PDF files can be large: opt in, like pushing when Anki closes
    "sync_first": "merge",
}
KINDS = ("sync", "pull", "push")
MAX_FOLDER_CHARS = 1000


class SyncController:
    def __init__(self, io: Any) -> None:
        self.io = io
        self.busy = False
        self.last_error: Optional[tuple[str, str]] = None
        self.last_result: Optional[dict] = None
        self._git_cache: dict = {}
        self._dirty = False

    # ── settings ──────────────────────────────────────────────────────────────
    def config(self) -> dict:
        cfg = {key: self.io.get_setting(key, default) for key, default in DEFAULTS.items()}
        for key, default in DEFAULTS.items():
            if not isinstance(cfg[key], type(default)):
                cfg[key] = default
        if cfg["sync_first"] not in notebook_sync.STRATEGIES:
            cfg["sync_first"] = "merge"
        cfg["sync_folder"] = cfg["sync_folder"].strip()[:MAX_FOLDER_CHARS]
        return cfg

    def save_config(self, partial: Any) -> dict:
        """Validate and store settings. Unknown keys and wrong types are ignored."""
        if not isinstance(partial, dict):
            return self.config()
        clean: dict[str, Any] = {}
        for key, default in DEFAULTS.items():
            if key in partial and isinstance(partial[key], type(default)):
                clean[key] = partial[key]
        if "sync_folder" in clean:
            folder = clean["sync_folder"].strip()
            if "\x00" in folder or len(folder) > MAX_FOLDER_CHARS:
                clean.pop("sync_folder")
            else:
                clean["sync_folder"] = os.path.abspath(os.path.expanduser(folder)) if folder else ""
        if clean.get("sync_first") not in (None, *notebook_sync.STRATEGIES):
            clean.pop("sync_first")
        self.io.set_settings(clean)
        return self.config()

    def categories(self, cfg: Optional[dict] = None) -> tuple[str, ...]:
        cfg = cfg or self.config()
        return tuple(name for name, key in (("notebook", "sync_notebook"), ("todos", "sync_todos"), ("pdfs", "sync_pdfs")) if cfg[key])

    def state(self) -> dict:
        return notebook_sync.load_state(self.io.state_path())

    # ── status ────────────────────────────────────────────────────────────────
    @property
    def dirty(self) -> bool:
        """True while work exists that is not yet in the remote (drives the red tab)."""
        return self._dirty

    def _local_part(self, cfg: dict, state: dict) -> dict:
        local: dict[str, Any] = {"notebook": None, "todos": None, "pdfs": None}
        try:
            if cfg["sync_notebook"]:
                local["notebook"] = notebook_sync.local_changes(self.io.read_notebook(), state)
            if cfg["sync_todos"]:
                local["todos"] = notebook_sync.todo_changes(self.io.read_todos(), state)
            if cfg["sync_pdfs"]:
                local["pdfs"] = notebook_sync.pdf_changes(self.io.read_pdfs(), state)
        except Exception as exc:
            print(f"Synapse sync: could not read local changes: {exc}")
        return local

    def _git_part(self, cfg: dict) -> dict:
        info = {"repo": False, "branch": "", "remote": False, "upstream": "", "ahead": 0, "behind": 0, "changed": []}
        if cfg["sync_folder"] and cfg["sync_git"] and sync_git.available() and os.path.isdir(cfg["sync_folder"]):
            try:
                info = sync_git.status(cfg["sync_folder"])
            except sync_git.GitError as exc:
                self.last_error = (exc.kind, exc.message)
        return info

    def _assemble(self, cfg: dict, state: dict, local: dict, git: dict) -> dict:
        pending = any(part and (part["added"] or part["modified"] or part["deleted"]) for part in local.values())
        self._dirty = bool(cfg["sync_folder"]) and (pending or bool(git["changed"]) or git["ahead"] > 0)
        return {
            "config": cfg,
            "configured": bool(cfg["sync_folder"]),
            "folder_exists": bool(cfg["sync_folder"]) and os.path.isdir(cfg["sync_folder"]),
            "git_available": sync_git.available(),
            "git": git,
            "local": local,
            "device": state.get("device", ""),
            "last_sync": state.get("last_sync", 0),
            "first_sync": not any(state.get("base", {}).get(name) for name in ("notebook", "todos", "pdfs")),
            "busy": self.busy,
            "error": list(self.last_error) if self.last_error else None,
            "result": self.last_result,
            "dirty": self._dirty,
        }

    def status(self) -> dict:
        """Status with the Git part from the last refresh (no subprocess on the GUI thread)."""
        cfg = self.config()
        state = self.state()
        return self._assemble(cfg, state, self._local_part(cfg, state), self._git_cache or self._git_part_empty())

    @staticmethod
    def _git_part_empty() -> dict:
        return {"repo": False, "branch": "", "remote": False, "upstream": "", "ahead": 0, "behind": 0, "changed": []}

    def refresh_status(self, done: Optional[Callable[[dict], None]] = None) -> None:
        """Recompute the status (Git in the background) and push it to the page."""
        cfg = self.config()
        state = self.state()
        local = self._local_part(cfg, state)

        def finish(git: dict) -> None:
            self._git_cache = git
            status = self._assemble(cfg, state, local, git)
            self.io.notify(status)
            if done:
                done(status)

        self.io.run_bg(self._safe(lambda: self._git_part(cfg), lambda exc: self._git_part_empty()), finish)

    # ── runs ──────────────────────────────────────────────────────────────────
    @staticmethod
    def _safe(fn: Callable[[], Any], fallback: Callable[[Exception], Any]) -> Callable[[], Any]:
        """Wrap background work so an unexpected error becomes a result instead of a lost callback."""
        def guarded() -> Any:
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - the GUI must never wait for a callback that died
                print(f"Synapse sync: {type(exc).__name__}: {exc}")
                return fallback(exc)
        return guarded

    def run(self, kind: str, done: Optional[Callable[[dict], None]] = None,
            run_bg: Optional[Callable] = None, flush: bool = True,
            timeout: float = sync_git.DEFAULT_TIMEOUT, quiet: bool = False) -> None:
        """Run a sync: kind "sync" (pull, merge, push), "pull" (pull, merge) or "push" (merge, push)."""
        if kind not in KINDS:
            raise ValueError("unknown sync kind")
        bg = run_bg or self.io.run_bg
        cfg = self.config()
        result: dict[str, Any] = {"ok": False, "kind": kind, "pulled": False, "pushed": False,
                                  "changed": {"notebook": False, "todos": False, "pdfs": False}, "conflicts": [],
                                  "skipped": [], "files": 0, "oversized": [], "missing_files": 0,
                                  "error": None, "at": round(time.time())}

        def finish(error: Optional[tuple[str, str]] = None, ok: bool = False) -> None:
            self.busy = False
            result["ok"] = ok
            result["error"] = list(error) if error else None
            self.last_error = error
            self.last_result = result
            if done:
                done(result)
            if not quiet:
                self.refresh_status()

        if not cfg["sync_folder"]:
            finish(("not_configured", "Choose a sync folder first."))
            return
        if self.busy:
            if done:
                done({**result, "error": ["busy", "A sync is already running."]})
            return
        self.busy = True
        self.last_error = None
        if not quiet:
            self.io.notify(self.status())

        def step_pull(flushed: bool) -> None:
            if not flushed:
                finish(("flush", "Your open page could not be saved, so nothing was synced."))
                return
            if kind == "push" or not cfg["sync_git"]:
                step_merge(None)
                return
            bg(self._safe(lambda: sync_manager.pull(cfg["sync_folder"], True, timeout),
                          lambda exc: {"pulled": False, "error": ("other", str(exc))}), step_merge)

        def step_merge(pulled: Optional[dict]) -> None:
            pull_error = None
            if pulled:
                result["pulled"] = pulled["pulled"]
                pull_error = pulled["error"]
            if flush:
                # The page may have changed while Git was working; save it again, then merge in one go.
                self.io.flush(lambda ok: do_merge(ok, pull_error))
            else:
                do_merge(True, pull_error)

        def do_merge(flushed: bool, pull_error: Optional[tuple[str, str]]) -> None:
            if not flushed:
                finish(("flush", "Your open page could not be saved, so nothing was synced."))
                return
            state = self.state()
            first = not any(state.get("base", {}).get(name) for name in ("notebook", "todos", "pdfs"))
            try:
                merged = sync_manager.merge(
                    self.io.read_notebook() if "notebook" in self.categories(cfg) else None,
                    self.io.read_todos() if "todos" in self.categories(cfg) else None,
                    cfg["sync_folder"], state, self.categories(cfg),
                    cfg["sync_first"] if first else "merge",
                    self.io.read_pdfs() if "pdfs" in self.categories(cfg) else None,
                    self.io.pdf_store_dir() if "pdfs" in self.categories(cfg) else "")
            except notebook_sync.SyncError as exc:
                finish(("unsafe", str(exc)))
                return
            except OSError as exc:
                finish(("folder", str(exc)))
                return
            for name, body, writer in (("notebook", merged["notebook"], self.io.write_notebook),
                                       ("todos", merged["todos"], self.io.write_todos),
                                       ("pdfs", merged["pdfs"], self.io.write_pdfs)):
                if body is not None:
                    if not writer(body):
                        # Nothing about this run is remembered, so the next one starts from the same base.
                        finish(("save", "The merged data could not be stored. Your notes are unchanged."))
                        return
                    result["changed"][name] = True
            notebook_sync.save_state(self.io.state_path(), merged["state"])
            for name, report in merged["reports"].items():
                result["conflicts"] += [[name, *entry] for entry in report["conflicts"]]
                result["skipped"] += report["skipped"]
                result["files"] += report.get("files_up", 0) + report.get("files_down", 0)
                result["oversized"] += report.get("oversized", [])
                result["missing_files"] += len(report.get("missing", []))
            if any(result["changed"].values()):
                self.io.reload()
            if not cfg["sync_git"]:
                finish(None, ok=True)
                return
            push = kind in ("sync", "push") and pull_error is None
            bg(self._safe(lambda: sync_manager.publish(cfg["sync_folder"], True, push, timeout=timeout),
                          lambda exc: {"committed": False, "pushed": False, "error": ("other", str(exc))}),
               lambda published: end(published, pull_error))

        def end(published: dict, pull_error: Optional[tuple[str, str]]) -> None:
            result["pushed"] = published["pushed"]
            error = pull_error or published["error"]
            finish(error, ok=True)

        if flush:
            self.io.flush(step_pull)
        else:
            step_pull(True)

    def clone(self, url: Any, done: Optional[Callable[[dict], None]] = None) -> None:
        """Clone a repository into the chosen (empty or missing) sync folder."""
        folder = self.config()["sync_folder"]
        if not folder:
            if done:
                done({"ok": False, "error": ["not_configured", "Choose a sync folder first."]})
            return
        if not isinstance(url, str) or not url.strip() or len(url) > 2000 or url.strip().startswith("-") or "\x00" in url:
            if done:
                done({"ok": False, "error": ["other", "Enter a valid repository address."]})
            return

        def work() -> Optional[tuple[str, str]]:
            try:
                sync_git.clone(url.strip(), folder)
            except sync_git.GitError as exc:
                return (exc.kind, exc.message)
            return None

        def finish(error: Optional[tuple[str, str]]) -> None:
            self.last_error = error
            if done:
                done({"ok": error is None, "error": list(error) if error else None})
            self.refresh_status()

        self.io.run_bg(self._safe(work, lambda exc: ("other", str(exc))), finish)
