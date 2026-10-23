"""Check GitLab main and optionally install a tested fast-forward at idle startup.

No reset, push, database migration, account action or runtime restart is performed.
Call before any browser/runtime work, with no other operator, and restart after
an installed update. Apply requires the host's explicit idle-startup assertion.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_REMOTE = "https://gitlab.com/codalilitari/interlocutors-relay.git"
_SHA = re.compile(r"[0-9a-f]{40,64}\Z")
_UNRESOLVED = {"in_flight", "uncertain"}
_PENDING_AUTHOR = {"prepared", "not_sent", "in_flight", "uncertain"}


class UpdateStopped(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


def _git(repo, *args, allowed=(0,), timeout=120):
    # Do not return Git diagnostics: authentication URLs and helpers can expose secrets.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    try:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=", "-C", str(repo), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateStopped("check_failed", "Git was unavailable or timed out.") from exc
    if result.returncode not in allowed:
        raise UpdateStopped("check_failed", "A Git operation failed; no raw authentication diagnostics retained.")
    return result


def _validate_remote_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "gitlab.com"
            or parsed.username or parsed.password or parsed.port
            or parsed.query or parsed.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.git", parsed.path)):
        raise UpdateStopped("configuration_mismatch", "Expected remote must be a credential-free GitLab HTTPS repository URL ending in .git.")


def _checkout(repo, expected_remote):
    root = Path(_git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if root != repo:
        raise UpdateStopped("configuration_mismatch", "Pass the exact repository root.")
    _validate_remote_url(expected_remote)
    urls = _git(repo, "remote", "get-url", "--all", "origin").stdout.splitlines()
    if urls != [expected_remote]:
        raise UpdateStopped("configuration_mismatch", "origin does not match the expected GitLab repository.")
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1)).stdout.strip()
    remote = _git(repo, "config", "--get", "branch.main.remote", allowed=(0, 1)).stdout.strip()
    merge = _git(repo, "config", "--get", "branch.main.merge", allowed=(0, 1)).stdout.strip()
    if (branch, remote, merge) != ("main", "origin", "refs/heads/main"):
        raise UpdateStopped("configuration_mismatch", "Checkout must be main tracking origin/main.")
    if _git(repo, "status", "--porcelain", "--untracked-files=all").stdout:
        raise UpdateStopped("dirty_checkout", "Tracked changes or untracked files are present; preserve them and skip update.")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _outside_repo(path, repo):
    path = Path(path).resolve()
    if path == repo or repo in path.parents:
        raise UpdateStopped("runtime_unavailable", "Private runtime databases must be outside the source checkout.")
    if not path.is_file():
        raise UpdateStopped("runtime_unavailable", "A required existing runtime database is unavailable.")
    return path


def _inspect_runtime(connection, role):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = "relay_session_turns" if role == "session" else "delivery_guard"
    if required not in tables:
        raise UpdateStopped("runtime_unavailable", "A runtime database lacks its expected relay schema.")
    if "delivery_guard" in tables and connection.execute("SELECT 1 FROM delivery_guard LIMIT 1").fetchone():
        raise UpdateStopped("runtime_busy", "An unresolved delivery guard requires reconciliation before updating.")
    if "deliveries" in tables and connection.execute(
            "SELECT 1 FROM deliveries WHERE state IN ('in_flight','uncertain') LIMIT 1").fetchone():
        raise UpdateStopped("runtime_busy", "An unresolved core delivery requires reconciliation before updating.")
    for table in ("relay_session_turns", "relay_action_operations"):
        if table in tables:
            for row in connection.execute(f"SELECT document FROM {table}"):
                document = json.loads(row[0])
                if not isinstance(document, dict):
                    raise UpdateStopped("runtime_unavailable", "A runtime operation record is invalid.")
                state = document.get("state")
                if table == "relay_session_turns" and (
                        state in _PENDING_AUTHOR
                        or (state == "sent" and document.get("expects_response", True))):
                    raise UpdateStopped("runtime_busy", "An author turn has a pending send, reconciliation or expected response; finish that turn before updating.")
                if table == "relay_action_operations" and state in _UNRESOLVED:
                    raise UpdateStopped("runtime_busy", "An unresolved author or action operation requires reconciliation before updating.")


@contextmanager
def _runtime_guard(repo, session_db, delivery_db, *, lock=False):
    if not session_db or not delivery_db:
        raise UpdateStopped("runtime_unavailable", "Apply requires both session_db and delivery_db paths.")
    connections = {}
    try:
        for role, value in (("session", session_db), ("delivery", delivery_db)):
            path = _outside_repo(value, repo)
            if path not in connections:
                mode = "rw" if lock else "ro"
                con = sqlite3.connect(path.as_uri() + "?mode=" + mode, uri=True, timeout=0, isolation_level=None)
                connections[path] = con
                if lock:
                    # Hold the existing writers while replacing code; no schema/data changes.
                    con.execute("BEGIN IMMEDIATE")
            _inspect_runtime(connections[path], role)
        yield
    except (sqlite3.Error, ValueError, TypeError) as exc:
        raise UpdateStopped("runtime_unavailable", "Runtime state is locked, unreadable or invalid; preserve it and skip update.") from exc
    finally:
        for con in connections.values():
            if con.in_transaction:
                con.rollback()
            con.close()


@contextmanager
def _update_lock(metadata):
    path = metadata / "relay-update.lock"
    token = json.dumps({"pid": os.getpid(), "token": uuid.uuid4().hex})
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise UpdateStopped("updater_busy", "Another update lock exists; do not remove it without checking its owner.") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
        yield
    finally:
        # Never remove a replacement lock owned by a different invocation.
        if path.exists() and path.read_text(encoding="utf-8") == token:
            path.unlink()


def _candidate(repo, stage, expected_remote, old_revision, advertised):
    _git(stage, "init", "--quiet")
    _git(stage, "remote", "add", "origin", expected_remote)
    _git(stage, "fetch", "--quiet", "--no-tags", "origin", "refs/heads/main")
    candidate = _git(stage, "rev-parse", "FETCH_HEAD").stdout.strip()
    if candidate != advertised:
        raise UpdateStopped("remote_changed", "GitLab main changed during staging; check again next startup.")
    # Import local ancestry as well, so divergent/unpublished local commits are detected.
    _git(stage, "fetch", "--quiet", "--no-tags", str(repo), old_revision)
    relation = _git(stage, "merge-base", "--is-ancestor", old_revision, candidate, allowed=(0, 1))
    if relation.returncode:
        raise UpdateStopped("non_fast_forward", "Remote main is not a fast-forward descendant of this checkout.")
    _git(stage, "checkout", "--quiet", "--detach", candidate)
    if not (stage / "tests").is_dir() or not any((stage / "tests").glob("test*.py")):
        raise UpdateStopped("validation_failed", "Candidate does not contain a discoverable unittest suite.")
    try:
        test = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
            cwd=stage, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300, check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateStopped("validation_failed", "Candidate tests could not complete; current checkout was not changed.") from exc
    count = re.search(r"Ran (\d+) tests?\b", test.stderr + "\n" + test.stdout)
    if test.returncode or not count or int(count.group(1)) == 0:
        raise UpdateStopped("validation_failed", "Candidate unittest suite failed; current checkout was not changed.")
    # Do not copy files produced by tests, only the already pinned Git commit.
    if _git(stage, "rev-parse", "HEAD").stdout.strip() != candidate:
        raise UpdateStopped("validation_failed", "Validation changed the staged revision.")
    if _git(stage, "diff", "--name-only", candidate).stdout:
        raise UpdateStopped("validation_failed", "Validation changed tracked candidate files.")
    return candidate


def run_update(repo, *, apply=False, expected_remote=DEFAULT_REMOTE,
               session_db=None, delivery_db=None, host_idle=False):
    """Return and persist one sanitized receipt; only apply=True can install.

    Check-only makes a GitLab reference query, with no fetch or runtime DB access.
    Apply requires existing private databases, a clean main, and host_idle=True:
    the caller asserts no browser/runtime work has started and no other operator
    is active. This is a host assertion, not browser activity detection.
    """
    repo = Path(repo).resolve()
    result = {"schema": "interlocutors-relay/update/v1", "mode": "apply" if apply else "check",
              "checked_at": datetime.now(timezone.utc).isoformat(), "installed": False,
              "old_revision": None, "candidate_revision": None, "new_revision": None,
              "validation": "not_run", "restart_required": False,
              "host_idle_asserted": host_idle is True}
    metadata = None
    install_started = False
    try:
        raw = _git(repo, "rev-parse", "--git-common-dir").stdout.strip()
        metadata = (repo / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        with _update_lock(metadata):
            old = _checkout(repo, expected_remote)
            result.update(old_revision=old, new_revision=old)
            if apply:
                if host_idle is not True:
                    raise UpdateStopped("host_busy", "Apply requires an explicit idle-startup assertion before any browser/runtime work, with no other operator active.")
                with _runtime_guard(repo, session_db, delivery_db):
                    pass
            lines = _git(repo, "ls-remote", "--exit-code", "--heads", "origin", "refs/heads/main").stdout.splitlines()
            if len(lines) != 1 or len(lines[0].split()) != 2 or not _SHA.fullmatch(lines[0].split()[0]):
                raise UpdateStopped("check_failed", "Remote did not return one valid main revision.")
            advertised = lines[0].split()[0]
            result["candidate_revision"] = advertised
            if old == advertised:
                result.update(status="up_to_date", reason="Local main matches GitLab main.")
            elif not apply:
                result.update(status="update_available", reason="GitLab main differs; ancestry and tests will be checked during apply.")
            else:
                with tempfile.TemporaryDirectory(prefix="interlocutors-relay-update-") as directory:
                    stage = Path(directory).resolve()
                    candidate = _candidate(repo, stage, expected_remote, old, advertised)
                    result["validation"] = "passed"
                    # Locks block cooperating runtime writers throughout final check and install.
                    with _runtime_guard(repo, session_db, delivery_db, lock=True):
                        if _checkout(repo, expected_remote) != old:
                            raise UpdateStopped("checkout_changed", "Local main changed while validating; no installation attempted.")
                        _git(repo, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", str(stage), candidate)
                        install_started = True
                        result["restart_required"] = True
                        merge = _git(repo, "merge", "--ff-only", "--no-overwrite-ignore", candidate, allowed=(0, 1, 128))
                        actual = _git(repo, "rev-parse", "HEAD").stdout.strip()
                        result["new_revision"] = actual
                        if merge.returncode or actual != candidate or _git(repo, "status", "--porcelain", "--untracked-files=all").stdout:
                            raise UpdateStopped("installation_uncertain", "Fast-forward did not finish cleanly; inspect checkout before operating. No reset or retry was performed.")
                        result.update(status="updated", installed=True, restart_required=True,
                                      reason="Validated candidate installed by fast-forward; restart before runtime operations.")
    except UpdateStopped as exc:
        if install_started and exc.status != "installation_uncertain":
            result.update(status="installation_uncertain", new_revision=None,
                          reason="Installation was interrupted; inspect checkout before operating. No reset or retry was performed.")
        else:
            result.update(status=exc.status, reason=exc.reason)
        if exc.status == "validation_failed":
            result["validation"] = "failed"
    except (OSError, ValueError) as exc:
        result.update(status="installation_uncertain" if install_started else "check_failed",
                      reason="Local update metadata or configuration was unavailable; inspect state before retrying. No raw diagnostics retained.")
    if metadata is not None:
        try:
            with (metadata / "relay-update-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
            result["receipt_recorded"] = True
        except OSError:
            result["receipt_recorded"] = False
    else:
        result["receipt_recorded"] = False
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--apply", action="store_true", help="install a validated fast-forward at idle startup")
    parser.add_argument("--expected-remote", default=DEFAULT_REMOTE)
    parser.add_argument("--session-db")
    parser.add_argument("--delivery-db")
    parser.add_argument("--host-idle", action="store_true", help="assert startup is before browser/runtime work and no other operator is active")
    args = parser.parse_args(argv)
    result = run_update(args.repo, apply=args.apply, expected_remote=args.expected_remote,
                        session_db=args.session_db, delivery_db=args.delivery_db, host_idle=args.host_idle)
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0 if result["status"] in {"up_to_date", "update_available", "updated"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
