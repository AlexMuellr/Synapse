"""Folder-based sync for the notebook and to-do snapshots (pure logic, no Anki imports).

The SQLite file never leaves the machine. Instead every entry (page, section, template,
task) becomes one small JSON file in a sync folder that Git, Dropbox or Syncthing can
carry. A three-way merge against the state of the last sync decides, per entry, what wins:

    local   remote  -> result
    same    same    -> keep
    same    changed -> take remote            (local == base)
    changed same    -> keep local             (remote == base)
    changed changed -> keep local + conflict copy of the remote version
    deleted elsewhere -> follow the deletion only if this side did not change the entry

Nothing is ever dropped silently: an entry changed on one side survives a deletion on
the other, and a real conflict leaves both versions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from typing import Any, Callable, Optional

SYNC_VERSION = 1
STRATEGIES = ("merge", "local", "remote")
MAX_PDF_SYNC_BYTES = 50 * 1024 * 1024     # larger PDFs sync as library entries only
NOTEBOOK_COLLECTIONS = ("pages", "folders", "templates")
_TOP_KEY = {"pages": "pages", "folders": "folders", "templates": "notebookTemplates"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class SyncError(Exception):
    """The sync folder or the local data cannot be synced safely."""


# ── basics ────────────────────────────────────────────────────────────────────

def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def entry_hash(entry: Any) -> str:
    return hashlib.sha256(json.dumps(entry, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()[:20]


def file_stem(entry_id: str) -> str:
    """Filename for an id: the id itself when harmless, otherwise a hash (never a path)."""
    if _SAFE_NAME.match(entry_id) and not entry_id.endswith("."):
        return entry_id
    return "x-" + hashlib.sha1(entry_id.encode("utf-8")).hexdigest()[:20]


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


# ── per-device state (never synced) ───────────────────────────────────────────

def load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        if isinstance(state, dict) and isinstance(state.get("device"), str):
            return state
    except (OSError, ValueError):
        pass
    return {"version": SYNC_VERSION, "device": uuid.uuid4().hex[:8], "base": {}, "last_sync": 0}


def save_state(path: str, state: dict) -> None:
    _atomic_write(path, canonical(state))


# ── generic three-way merge over {id: entry} ──────────────────────────────────

def merge_entries(
    local: dict[str, Any],
    remote: dict[str, Any],
    tombstones: dict[str, Any],
    base: dict[str, str],
    strategy: str = "merge",
    device: str = "device",
    label_conflict: Optional[Callable[[Any], Any]] = None,
    now: Optional[float] = None,
) -> dict:
    """Merge two id->entry maps. ``base`` maps id -> hash as of the last completed sync.

    Returns {"entries", "tombstones", "conflicts" [(id, copy_id)], "taken" [ids from remote],
    "removed" [ids deleted because remote deleted them], "deleted" [ids deleted locally]}.
    """
    if strategy not in STRATEGIES:
        raise ValueError("unknown strategy")
    now = time.time() if now is None else now
    result: dict[str, Any] = {}
    conflicts: list[tuple[str, str]] = []
    taken, removed, deleted = [], [], []
    new_tombs = {k: v for k, v in tombstones.items()}
    ids = list(dict.fromkeys([*local, *remote, *tombstones, *base]))

    for entry_id in ids:
        mine, theirs = local.get(entry_id), remote.get(entry_id)
        known = base.get(entry_id)
        mine_hash = entry_hash(mine) if entry_id in local else None
        their_hash = entry_hash(theirs) if entry_id in remote else None

        if mine_hash is not None and their_hash is not None:
            if mine_hash == their_hash:
                result[entry_id] = mine
            elif known == mine_hash:
                result[entry_id] = copy.deepcopy(theirs)
                taken.append(entry_id)
            elif known == their_hash:
                result[entry_id] = mine
            elif known is None and strategy == "local":
                result[entry_id] = mine
            elif known is None and strategy == "remote":
                result[entry_id] = copy.deepcopy(theirs)
                taken.append(entry_id)
            else:
                result[entry_id] = mine
                copy_id = f"{entry_id}-conflict-{their_hash[:6]}"
                if copy_id not in local and copy_id not in remote:
                    duplicate = copy.deepcopy(theirs)
                    if isinstance(duplicate, dict):
                        duplicate["id"] = copy_id
                        if label_conflict:
                            duplicate = label_conflict(duplicate)
                    result[copy_id] = duplicate
                    conflicts.append((entry_id, copy_id))
            new_tombs.pop(entry_id, None)
        elif mine_hash is not None:
            if entry_id in tombstones and known == mine_hash:
                removed.append(entry_id)          # deleted elsewhere, untouched here
            else:
                result[entry_id] = mine           # new here, or changed here despite a deletion
                new_tombs.pop(entry_id, None)
        elif their_hash is not None:
            if known is not None and known == their_hash:
                deleted.append(entry_id)          # deleted here, untouched there
            else:
                result[entry_id] = copy.deepcopy(theirs)
                taken.append(entry_id)
                new_tombs.pop(entry_id, None)
        else:
            # gone on both sides (or only remembered): keep/refresh the tombstone
            pass
        if entry_id in base and entry_id not in result and entry_id not in new_tombs:
            new_tombs[entry_id] = round(now)
    for entry_id in deleted:
        new_tombs[entry_id] = round(now)
    return {"entries": result, "tombstones": new_tombs, "conflicts": conflicts,
            "taken": taken, "removed": removed, "deleted": deleted}


# ── sync folder layout ────────────────────────────────────────────────────────

def _read_entries(directory: str) -> tuple[dict[str, Any], list[str]]:
    entries: dict[str, Any] = {}
    skipped: list[str] = []
    if not os.path.isdir(directory):
        return entries, skipped
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                entry = json.load(handle)
        except (OSError, ValueError):
            skipped.append(name)
            continue
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(entry_id, str) or not entry_id or entry_id in entries \
                or file_stem(entry_id) + ".json" != name:
            skipped.append(name)
            continue
        entries[entry_id] = entry
    return entries, skipped


def _write_entries(directory: str, entries: dict[str, Any]) -> None:
    """Write only what changed; remove files of entries that no longer exist."""
    existing = set(os.listdir(directory)) if os.path.isdir(directory) else set()
    wanted = set()
    for entry_id, entry in entries.items():
        name = file_stem(entry_id) + ".json"
        wanted.add(name)
        text = canonical(entry)
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                if handle.read() == text:
                    continue
        except OSError:
            pass
        _atomic_write(path, text)
    for name in existing - wanted:
        if name.endswith(".json"):
            os.remove(os.path.join(directory, name))


def _read_tombstones(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items()}
    except (OSError, ValueError):
        pass
    return {}


def _check_folder(sync_dir: str) -> str:
    marker = os.path.join(sync_dir, "synapse-sync.json")
    if os.path.exists(marker):
        try:
            with open(marker, "r", encoding="utf-8") as handle:
                version = json.load(handle).get("version")
        except (OSError, ValueError, AttributeError):
            raise SyncError("The sync folder marker is unreadable.")
        if not isinstance(version, int) or version > SYNC_VERSION:
            raise SyncError("The sync folder was written by a newer version of Synapse.")
    return marker


def _label_conflict(entry: Any) -> Any:
    if isinstance(entry, dict):
        for key in ("title", "name"):
            if isinstance(entry.get(key), str):
                entry[key] = f"{entry[key]} (conflict)"
                break
    return entry


def _split(items: Any) -> tuple[dict[str, Any], list[Any]]:
    """id->entry (first one wins) plus entries that cannot be synced and stay local."""
    synced: dict[str, Any] = {}
    extras: list[Any] = []
    for item in items if isinstance(items, list) else []:
        entry_id = item.get("id") if isinstance(item, dict) else None
        if isinstance(entry_id, str) and entry_id and entry_id not in synced:
            synced[entry_id] = item
        else:
            extras.append(item)
    return synced, extras


def _ordered(local_items: Any, merged: dict[str, Any], extras: list[Any]) -> list[Any]:
    order = [item["id"] for item in local_items if isinstance(item, dict) and item.get("id") in merged] \
        if isinstance(local_items, list) else []
    order = list(dict.fromkeys(order))
    rest = [key for key in merged if key not in set(order)]
    return [merged[key] for key in [*order, *rest]] + list(extras)


# ── public API ────────────────────────────────────────────────────────────────

def _sync_collections(
    collections: dict[str, dict[str, Any]],
    sync_dir: str,
    subdir: str,
    state: dict,
    strategy: str,
    now: Optional[float],
) -> tuple[dict[str, dict], dict]:
    marker = _check_folder(sync_dir)
    root = os.path.join(sync_dir, subdir)
    base_all = state.setdefault("base", {}).setdefault(subdir, {})
    merged_all: dict[str, dict] = {}
    report: dict[str, Any] = {"conflicts": [], "taken": 0, "removed": 0, "deleted": 0, "skipped": []}
    pending_writes: list[Callable[[], None]] = []
    new_base: dict[str, dict[str, str]] = {}
    for name, local in collections.items():
        remote, skipped = _read_entries(os.path.join(root, name))
        report["skipped"] += [f"{subdir}/{name}/{file}" for file in skipped]
        tombs = _read_tombstones(os.path.join(root, name + "-deleted.json"))
        outcome = merge_entries(local, remote, tombs, base_all.get(name, {}), strategy,
                                state["device"], _label_conflict, now)
        merged_all[name] = outcome
        report["conflicts"] += [(name, a, b) for a, b in outcome["conflicts"]]
        report["taken"] += len(outcome["taken"])
        report["removed"] += len(outcome["removed"])
        report["deleted"] += len(outcome["deleted"])
        new_base[name] = {key: entry_hash(value) for key, value in outcome["entries"].items()}
        pending_writes.append(lambda n=name, o=outcome: (
            _write_entries(os.path.join(root, n), o["entries"]),
            _atomic_write(os.path.join(root, n + "-deleted.json"), canonical(o["tombstones"]))
            if o["tombstones"] or os.path.exists(os.path.join(root, n + "-deleted.json")) else None))
    for write in pending_writes:
        write()
    if not os.path.exists(marker):
        _atomic_write(marker, canonical({"version": SYNC_VERSION}))
    state["base"][subdir] = new_base
    state["last_sync"] = round(time.time() if now is None else now)
    return merged_all, report


def sync_notebook(local_body: str, sync_dir: str, state: dict,
                  strategy: str = "merge", now: Optional[float] = None) -> dict:
    """Merge the local notebook snapshot with the sync folder and write the result back.

    Returns {"body": merged snapshot JSON, "changed": bool, "report": {...}}. ``state`` is
    updated in place: the caller stores ``body`` through the normal validated save path
    first and only then persists ``state`` (pass a copy and keep it on success).
    """
    try:
        data = json.loads(local_body)
    except (TypeError, ValueError) as exc:
        raise SyncError("The local notebook is not valid JSON.") from exc
    if data == []:
        data = {"pages": []}            # a notebook that was never opened (fresh database)
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        raise SyncError("Open the notebook once so it is upgraded to the page format, then sync.")
    collections, extras = {}, {}
    for name in NOTEBOOK_COLLECTIONS:
        collections[name], extras[name] = _split(data.get(_TOP_KEY[name]))
    merged, report = _sync_collections(collections, sync_dir, "notebook", state, strategy, now)

    result = {key: value for key, value in data.items()}
    for name in NOTEBOOK_COLLECTIONS:
        items = _ordered(data.get(_TOP_KEY[name]), merged[name]["entries"], extras[name])
        if items or _TOP_KEY[name] in data:
            result[_TOP_KEY[name]] = items
    page_ids = [p["id"] for p in result["pages"] if isinstance(p, dict) and isinstance(p.get("id"), str)]
    if page_ids and data.get("activePageId") and result.get("activePageId") not in page_ids:
        result["activePageId"] = page_ids[0]
    body = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    changed = canonical(json.loads(body)) != canonical(data)
    return {"body": body, "changed": changed, "report": report}


def sync_todos(local_body: str, sync_dir: str, state: dict,
               strategy: str = "merge", now: Optional[float] = None) -> dict:
    """Same merge for the to-do list (a JSON list of tasks with ids)."""
    try:
        tasks = json.loads(local_body)
    except (TypeError, ValueError) as exc:
        raise SyncError("The local to-do list is not valid JSON.") from exc
    if not isinstance(tasks, list):
        raise SyncError("The local to-do list has an unexpected format.")
    local, extras = _split(tasks)
    merged, report = _sync_collections({"tasks": local}, sync_dir, "todos", state, strategy, now)
    result = _ordered(tasks, merged["tasks"]["entries"], extras)
    body = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    return {"body": body, "changed": canonical(json.loads(body)) != canonical(tasks), "report": report}


def local_changes(local_body: str, state: dict) -> dict:
    """What differs from the last sync, for the "Changes" view: {added, modified, deleted}."""
    try:
        data = json.loads(local_body)
    except (TypeError, ValueError):
        return {"added": [], "modified": [], "deleted": []}
    pages, _ = _split(data.get("pages") if isinstance(data, dict) else None)
    base = state.get("base", {}).get("notebook", {}).get("pages", {})
    title = lambda page: str(page.get("title") or "") if isinstance(page, dict) else ""
    return {
        "added": [{"id": k, "title": title(v)} for k, v in pages.items() if k not in base],
        "modified": [{"id": k, "title": title(v)} for k, v in pages.items()
                     if k in base and entry_hash(v) != base[k]],
        "deleted": [{"id": k, "title": ""} for k in base if k not in pages],
    }


def todo_changes(local_body: str, state: dict) -> dict:
    """Same as local_changes for the to-do list: {added, modified, deleted} with the task text as title."""
    try:
        tasks = json.loads(local_body)
    except (TypeError, ValueError):
        return {"added": [], "modified": [], "deleted": []}
    items, _ = _split(tasks)
    base = state.get("base", {}).get("todos", {}).get("tasks", {})
    title = lambda task: str(task.get("text") or task.get("title") or "") if isinstance(task, dict) else ""
    return {
        "added": [{"id": k, "title": title(v)} for k, v in items.items() if k not in base],
        "modified": [{"id": k, "title": title(v)} for k, v in items.items()
                     if k in base and entry_hash(v) != base[k]],
        "deleted": [{"id": k, "title": ""} for k in base if k not in items],
    }


# ── PDF library ───────────────────────────────────────────────────────────────
# Entries and folders merge like pages. An entry's ``path`` only means something on its own
# computer, so it is never exported; the PDF files themselves are copied next to the entries.

_PDF_LOCAL_KEYS = ("path", "_exists")


def _pdf_view(entry: Any) -> Any:
    return {k: v for k, v in entry.items() if k not in _PDF_LOCAL_KEYS} if isinstance(entry, dict) else entry


def _pdf_parts(body: Any) -> dict:
    """The library as {"data": dict, "entries": {id: entry}, "folders": {id: folder}, extras…}."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise SyncError("The local PDF library is not valid JSON.") from exc
    if isinstance(data, list):
        data = {"version": 4, "folders": [], "pdfs": data}
    if not isinstance(data, dict) or not isinstance(data.get("pdfs"), list):
        raise SyncError("The local PDF library has an unexpected format.")
    entries, extra_entries = _split(data["pdfs"])
    folders, extra_folders = _split(data.get("folders"))
    return {"data": data, "entries": entries, "folders": folders,
            "extra_entries": extra_entries, "extra_folders": extra_folders}


def _fingerprint(path: str) -> str:
    info = os.stat(path)
    return f"{info.st_size}-{info.st_mtime_ns}"


def _copy_file(source: str, target: str) -> None:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    shutil.copyfile(source, tmp)
    os.replace(tmp, target)


def _inside(path: str, folder: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(folder)]) == os.path.abspath(folder)
    except ValueError:
        return False


def sync_pdfs(local_body: str, sync_dir: str, state: dict, store_dir: str,
              strategy: str = "merge", now: Optional[float] = None,
              max_bytes: Optional[int] = None) -> dict:
    """Merge the PDF library (entries and folders) and copy the PDF files.

    Own files are uploaded, never replaced. A file that only exists in the sync folder is copied
    into ``store_dir`` (a copy this computer owns and may refresh); the entry then points there.
    Returns {"body", "changed", "report"}; the report adds files_up, files_down, oversized, missing.
    """
    max_bytes = MAX_PDF_SYNC_BYTES if max_bytes is None else max_bytes
    parts = _pdf_parts(local_body)
    data = parts["data"]
    local_paths = {k: str(v.get("path") or "") for k, v in parts["entries"].items()}
    before = set(state.get("base", {}).get("pdfs", {}).get("entries", {}))
    merged, report = _sync_collections(
        {"entries": {k: _pdf_view(v) for k, v in parts["entries"].items()}, "folders": parts["folders"]},
        sync_dir, "pdfs", state, strategy, now)
    entries = merged["entries"]["entries"]
    report.update({"files_up": 0, "files_down": 0, "oversized": [], "missing": []})

    files_dir = os.path.join(sync_dir, "pdfs", "files")
    paths_map = state.setdefault("pdf_paths", {})
    uploaded = state.setdefault("pdf_up", {})
    downloaded = state.setdefault("pdf_down", {})
    final_paths: dict[str, str] = {}
    unknown: list[str] = []            # entries whose file this computer does not have
    for entry_id, entry in entries.items():
        blob = os.path.join(files_dir, file_stem(entry_id) + ".pdf")
        own = local_paths.get(entry_id, "")
        managed = paths_map.get(entry_id, "")
        title = str(entry.get("name") or entry_id) if isinstance(entry, dict) else entry_id
        if own and os.path.isfile(own) and not _inside(own, store_dir):
            final_paths[entry_id] = own
            size = os.path.getsize(own)
            if size > max_bytes:
                report["oversized"].append(title)
            elif not os.path.isfile(blob) or uploaded.get(entry_id) != _fingerprint(own):
                _copy_file(own, blob)
                uploaded[entry_id] = _fingerprint(own)
                report["files_up"] += 1
            continue
        current = own if own and os.path.isfile(own) else managed
        if current and os.path.isfile(current) and _inside(current, store_dir):
            final_paths[entry_id] = current          # our own copy: refresh it when the remote file changed
            if os.path.isfile(blob) and downloaded.get(entry_id) != _fingerprint(blob):
                _copy_file(blob, current)
                downloaded[entry_id] = _fingerprint(blob)
                report["files_down"] += 1
            continue
        if os.path.isfile(blob):
            target = os.path.join(store_dir, file_stem(entry_id) + ".pdf")
            _copy_file(blob, target)
            paths_map[entry_id] = target
            downloaded[entry_id] = _fingerprint(blob)
            final_paths[entry_id] = target
            report["files_down"] += 1
            continue
        final_paths[entry_id] = own or managed       # file unknown here: the library offers "relink"
        unknown.append(entry_id)

    # A conflict copy of an entry is the same PDF: it shares the original's file.
    for name, original_id, copy_id in report["conflicts"]:
        if name == "entries" and copy_id in unknown and final_paths.get(original_id):
            final_paths[copy_id] = final_paths[original_id]
            unknown.remove(copy_id)
    report["missing"] = [str(entries[k].get("name") or k) if isinstance(entries[k], dict) else k for k in unknown]

    # Entries gone everywhere: remove the shared file and the copy this computer made of it.
    for entry_id in before - set(entries):
        blob = os.path.join(files_dir, file_stem(entry_id) + ".pdf")
        if os.path.isfile(blob):
            os.remove(blob)
        copy_path = paths_map.pop(entry_id, "")
        if copy_path and _inside(copy_path, store_dir) and os.path.isfile(copy_path):
            os.remove(copy_path)
        uploaded.pop(entry_id, None)
        downloaded.pop(entry_id, None)

    result = {key: value for key, value in data.items()}
    result["version"] = data.get("version", 4)
    result["pdfs"] = _ordered(data["pdfs"], {k: ({**v, "path": final_paths.get(k, "")} if isinstance(v, dict) else v)
                                             for k, v in entries.items()}, parts["extra_entries"])
    result["folders"] = _ordered(data.get("folders"), merged["folders"]["entries"], parts["extra_folders"])
    body = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    original = {"version": 4, "folders": [], "pdfs": data["pdfs"]} if isinstance(json.loads(local_body), list) else data
    return {"body": body, "changed": canonical(json.loads(body)) != canonical(original), "report": report}


def pdf_changes(local_body: str, state: dict) -> dict:
    """{added, modified, deleted} for the PDF library (entries and folders) since the last sync."""
    try:
        parts = _pdf_parts(local_body)
    except SyncError:
        return {"added": [], "modified": [], "deleted": []}
    base = state.get("base", {}).get("pdfs", {})
    out: dict[str, list] = {"added": [], "modified": [], "deleted": []}
    for name, items in (("entries", {k: _pdf_view(v) for k, v in parts["entries"].items()}), ("folders", parts["folders"])):
        known = base.get(name, {})
        title = lambda item: str(item.get("name") or "") if isinstance(item, dict) else ""
        out["added"] += [{"id": k, "title": title(v)} for k, v in items.items() if k not in known]
        out["modified"] += [{"id": k, "title": title(v)} for k, v in items.items() if k in known and entry_hash(v) != known[k]]
        out["deleted"] += [{"id": k, "title": ""} for k in known if k not in items]
    return out
