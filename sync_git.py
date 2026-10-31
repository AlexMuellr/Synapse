"""Thin, non-interactive Git wrapper for the sync folder (no Anki imports).

Git only transports files. It never merges page contents textually: every JSON file is
marked ``-merge`` so a file changed on both sides is a conflict, which is resolved by
taking the remote file. The real decision (keep, take, conflict copy) is made afterwards
by notebook_sync against the local database and the state of the last sync.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Optional

DEFAULT_TIMEOUT = 30
ATTRIBUTES = "*.json -merge\n*.json -diff\n"
IGNORE = "*.tmp\n"


class GitError(Exception):
    """A Git command failed. ``kind``: missing, timeout, auth, offline, rejected or other."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def available() -> bool:
    return shutil.which("git") is not None


def _classify(stderr: str) -> str:
    text = stderr.lower()
    if any(s in text for s in ("could not resolve host", "unable to access", "network is unreachable",
                               "connection timed out", "connection refused", "could not read from remote")) \
            and "permission denied" not in text and "authentication" not in text:
        return "offline"
    if any(s in text for s in ("authentication failed", "permission denied", "terminal prompts disabled",
                               "could not read username", "host key verification failed")):
        return "auth"
    if "non-fast-forward" in text or "[rejected]" in text or "fetch first" in text:
        return "rejected"
    return "other"


def _env() -> dict:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=10")
    return env


def run(args: list[str], cwd: str, timeout: float = DEFAULT_TIMEOUT, check: bool = True) -> str:
    """Run ``git <args>`` without a shell and without ever asking for input."""
    if not available():
        raise GitError("missing", "Git is not installed.")
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        done = subprocess.run(["git", *args], cwd=cwd, env=_env(), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              stdin=subprocess.DEVNULL, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise GitError("timeout", f"git {args[0]} timed out") from exc
    except OSError as exc:
        raise GitError("missing", str(exc)) from exc
    if check and done.returncode != 0:
        raise GitError(_classify(done.stderr), (done.stderr or done.stdout).strip()[:500])
    return done.stdout


def is_repo(folder: str) -> bool:
    if not os.path.isdir(folder):
        return False
    try:
        return run(["rev-parse", "--is-inside-work-tree"], folder, 10).strip() == "true"
    except GitError:
        return False


def _ensure_files(folder: str) -> None:
    for name, content in ((".gitattributes", ATTRIBUTES), (".gitignore", IGNORE)):
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)


def _identity_args(folder: str) -> list[str]:
    args: list[str] = []
    for key, fallback in (("user.name", "Synapse Sync"), ("user.email", "synapse-sync@localhost")):
        if not run(["config", "--get", key], folder, 10, check=False).strip():
            args += ["-c", f"{key}={fallback}"]
    return args


def branch_name(folder: str) -> str:
    return run(["rev-parse", "--abbrev-ref", "HEAD"], folder, 10).strip()


def status(folder: str) -> dict:
    """{repo, branch, remote, upstream, ahead, behind, changed: [(A|M|D|R, path)]} without network access."""
    info = {"repo": False, "branch": "", "remote": False, "upstream": "", "ahead": 0, "behind": 0, "changed": []}
    if not is_repo(folder):
        return info
    info["repo"] = True
    try:
        info["branch"] = branch_name(folder)
    except GitError:
        pass
    info["remote"] = bool(run(["remote"], folder, 10).strip())
    porcelain = run(["status", "--porcelain=v1", "--untracked-files=all"], folder, 15)
    info["changed"] = [("A" if line[:2] == "??" else (line[:2].strip() or "M")[0], line[3:].strip().strip('"'))
                       for line in porcelain.splitlines() if line]
    upstream = run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], folder, 10, check=False).strip()
    if upstream and "@{u}" not in upstream and not upstream.startswith("fatal"):
        info["upstream"] = upstream
        counts = run(["rev-list", "--left-right", "--count", f"HEAD...{upstream}"], folder, 10, check=False).split()
        if len(counts) == 2 and all(c.isdigit() for c in counts):
            info["ahead"], info["behind"] = int(counts[0]), int(counts[1])
    return info


def _stage_exists(folder: str, stage: int, name: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f":{stage}:{name}"], cwd=folder, env=_env(),
                          capture_output=True, stdin=subprocess.DEVNULL).returncode == 0


def commit_all(folder: str, message: str = "Synapse sync") -> bool:
    """Commit everything in the sync folder. Returns False when there was nothing to commit."""
    _ensure_files(folder)
    run(["add", "-A"], folder, 30)
    if not run(["status", "--porcelain"], folder, 15).strip():
        return False
    run([*_identity_args(folder), "commit", "-m", message, "--no-gpg-sign"], folder, 30)
    return True


def pull(folder: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Fetch and merge the upstream branch. Returns {"updated": bool, "resolved": [paths]}.

    Local changes are committed first, so nothing can be overwritten. Files changed on both
    sides take the remote version here; notebook_sync then reconciles against the database.
    """
    commit_all(folder, "Synapse sync (before pull)")
    info = status(folder)
    if not info["remote"]:
        raise GitError("other", "No remote is configured for the sync folder.")
    run(["fetch", "--prune"], folder, timeout)
    upstream = info["upstream"]
    if not upstream:
        remote = run(["remote"], folder, 10).split()[0]
        candidate = f"{remote}/{info['branch']}"
        if run(["rev-parse", "--verify", "--quiet", candidate], folder, 10, check=False).strip():
            run(["branch", "--set-upstream-to", candidate], folder, 10)
            upstream = candidate
        else:
            return {"updated": False, "resolved": []}      # nothing on the remote yet
    before = run(["rev-parse", "HEAD"], folder, 10, check=False).strip()
    resolved: list[str] = []
    try:
        run([*_identity_args(folder), "merge", "--no-edit", "--no-gpg-sign", "--allow-unrelated-histories", upstream],
            folder, timeout)
    except GitError:
        conflicted = [n for n in run(["diff", "--name-only", "--diff-filter=U"], folder, 15, check=False).split("\n") if n]
        if not conflicted:
            run(["merge", "--abort"], folder, 15, check=False)
            raise
        for name in conflicted:
            remote_has_it = _stage_exists(folder, 3, name)
            if remote_has_it:
                run(["checkout", "--theirs", "--", name], folder, 15)
                run(["add", "--", name], folder, 15)
            else:
                run(["rm", "-f", "--", name], folder, 15)
            resolved.append(name)
        run([*_identity_args(folder), "commit", "--no-edit", "--no-gpg-sign"], folder, 30)
    after = run(["rev-parse", "HEAD"], folder, 10, check=False).strip()
    return {"updated": after != before, "resolved": resolved}


def push(folder: str, timeout: float = DEFAULT_TIMEOUT) -> None:
    info = status(folder)
    if not info["remote"]:
        raise GitError("other", "No remote is configured for the sync folder.")
    if info["upstream"]:
        run(["push"], folder, timeout)
    else:
        remote = run(["remote"], folder, 10).split()[0]
        run(["push", "-u", remote, "HEAD"], folder, timeout)


def clone(url: str, folder: str, timeout: float = 120) -> None:
    """Clone into an empty or missing folder."""
    if os.path.isdir(folder) and os.listdir(folder):
        raise GitError("other", "The target folder is not empty.")
    os.makedirs(folder, exist_ok=True)
    run(["clone", "--", url, folder], os.path.dirname(os.path.abspath(folder)) or ".", timeout)
