"""Track host-observed notifications and messages independently of UI unread state.

This companion shares the private session database. It neither opens an inbox
nor polls a platform; every content snapshot and progress record is supplied
by the active host after an actual observation.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from relay_core import NotFoundError, StateError, ValidationError, _id, _json, _zoned_timestamp
from relay_session import DISPOSITIONS, _ref


STREAMS = {"notifications", "messages"}
SOURCE_METHODS = {"observe_source", "record_source_progress", "inspect_source", "pending_sources", "source_packet"}
_STAGES = {"inspected", "relayed", "disposition", "result", "returned"}
_TERMINAL_DISPOSITIONS = {"QUIET", "DISAGREE", "NO_ACTION"}


def _timestamp(value: Any, label: str) -> datetime:
    _zoned_timestamp(value, label)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _key(actor_id: str, stream: str, source_id: str) -> tuple[str, str, str]:
    _id(actor_id, "actor_id")
    _id(source_id, "source_id")
    if stream not in STREAMS:
        raise ValidationError("stream must be notifications or messages")
    return actor_id, stream, source_id


def _next(revision: dict[str, Any]) -> str:
    progress = revision["progress"]
    result = progress.get("result")
    if result:
        if result["outcome"] == "uncertain":
            return "reconcile_before_retry"
        return "complete" if "returned" in progress else "return_observed_result"
    disposition = progress.get("disposition", {}).get("disposition")
    if disposition in _TERMINAL_DISPOSITIONS:
        return "complete"
    if disposition in {"HOLD", "NEED_CONTEXT", "NEED_RESEARCH"}:
        return {"HOLD": "respect_hold", "NEED_CONTEXT": "gather_requested_context",
                "NEED_RESEARCH": "perform_scoped_research"}[disposition]
    if disposition:
        return "resume_selected_work"
    if "relayed" in progress:
        return "retrieve_existing_author_response"
    if "inspected" in progress:
        return "prepare_author_intake"
    return "inspect_source"


class SourceStore:
    """Local observations, revisions and progress; no external-read authority."""

    def __init__(self, db_path: str | Path):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path), timeout=10, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS relay_observed_sources (
            actor_id TEXT NOT NULL, stream TEXT NOT NULL, source_id TEXT NOT NULL,
            document TEXT NOT NULL, PRIMARY KEY(actor_id, stream, source_id))""")

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _write(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def _get(self, key):
        row = self._db.execute("SELECT document FROM relay_observed_sources WHERE actor_id=? AND stream=? AND source_id=?", key).fetchone()
        return json.loads(row[0]) if row else None

    def _save(self, document):
        self._db.execute("INSERT OR REPLACE INTO relay_observed_sources VALUES (?,?,?,?)",
                         (document["actor_id"], document["stream"], document["source_id"], _json(document, "source record")))

    def observe_source(self, actor_id: str, actor_kind: str, stream: str, source_id: str,
                       source_ref: Any, content: Any, observed_at: str,
                       unread: bool | None = None) -> dict[str, Any]:
        """Record a scoped host snapshot, creating a revision only when content changes.

        ``content`` contains only observed item content and stable media refs,
        excluding unread badges, relative timestamps and other volatile UI.
        ``unread=None`` means the UI read status was not observed.
        """
        key = _key(actor_id, stream, source_id)
        if actor_kind not in {"page", "profile"}:
            raise ValidationError("actor_kind must be page or profile")
        _ref(source_ref, "source_ref")
        when = _timestamp(observed_at, "observed_at")
        if unread is not None and not isinstance(unread, bool):
            raise ValidationError("unread must be true, false, or null")
        if not isinstance(content, (str, dict)) or not content:
            raise ValidationError("content must contain observed text or a nonempty object")
        encoded = _json(content, "observed content")
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._write():
            document = self._get(key)
            if document is None:
                document = {"actor_id": actor_id, "actor_kind": actor_kind, "stream": stream,
                            "source_id": source_id, "first_observed_at": observed_at, "revisions": []}
            elif document["actor_kind"] != actor_kind:
                raise ValidationError("actor_kind differs from the existing source actor")
            elif when < _timestamp(document["last_observed_at"], "last_observed_at"):
                raise StateError("older observation cannot replace the latest source snapshot")
            revisions = document["revisions"]
            changed = not revisions or revisions[-1]["fingerprint"] != fingerprint
            if changed:
                revisions.append({"revision": len(revisions) + 1, "fingerprint": fingerprint,
                                  "content": json.loads(encoded), "source_ref": source_ref,
                                  "first_observed_at": observed_at, "last_observed_at": observed_at,
                                  "progress": {}, "progress_history": []})
            else:
                revisions[-1]["last_observed_at"] = observed_at
                revisions[-1]["source_ref"] = source_ref
            document.update(last_observed_at=observed_at, source_ref=source_ref,
                            ui_unread={"value": unread, "observed_at": observed_at, "source_ref": source_ref})
            self._save(document)
        return {"record": document, "new_revision": changed,
                "revision": revisions[-1]["revision"], "host_step": _next(revisions[-1])}

    def inspect_source(self, actor_id: str, stream: str, source_id: str) -> dict[str, Any]:
        key = _key(actor_id, stream, source_id)
        with self._lock:
            document = self._get(key)
        if document is None:
            raise NotFoundError("observed source not found")
        return document

    def record_source_progress(self, actor_id: str, stream: str, source_id: str, revision: int,
                               stage: str, source_ref: Any, observed_at: str,
                               session_ref: Any = None, disposition: str | None = None,
                               result_ref: Any = None, outcome: str | None = None) -> dict[str, Any]:
        """Append evidence to an exact revision without consuming a newer edit.

        Inspection, actual relay, author disposition, observed action result and
        actual result return remain separate. UI unread status is never changed.
        """
        key = _key(actor_id, stream, source_id)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValidationError("revision must be a positive integer")
        if stage not in _STAGES:
            raise ValidationError("unsupported source progress stage")
        _ref(source_ref, "source_ref")
        when = _timestamp(observed_at, "observed_at")
        event = {"stage": stage, "source_ref": source_ref, "observed_at": observed_at}
        if stage == "relayed":
            event["session_ref"] = _ref(session_ref, "session_ref")
        elif session_ref is not None:
            raise ValidationError("session_ref belongs to the relayed stage")
        if stage == "disposition":
            if disposition not in DISPOSITIONS | {"NO_ACTION"}:
                raise ValidationError("unsupported source disposition")
            event["disposition"] = disposition
        elif disposition is not None:
            raise ValidationError("disposition belongs to the disposition stage")
        if stage == "result":
            if outcome not in {"verified", "uncertain", "failed_before_submit"}:
                raise ValidationError("unsupported source result outcome")
            event.update(result_ref=_ref(result_ref, "result_ref"), outcome=outcome)
        elif result_ref is not None or outcome is not None:
            raise ValidationError("result_ref and outcome belong to the result stage")
        with self._write():
            document = self._get(key)
            if document is None:
                raise NotFoundError("observed source not found")
            selected = next((item for item in document["revisions"] if item["revision"] == revision), None)
            if selected is None:
                raise NotFoundError("source revision not found")
            if when < _timestamp(selected["first_observed_at"], "first_observed_at"):
                raise StateError("progress cannot precede the source observation")
            progress = selected["progress"]
            prior = progress.get(stage)
            if prior == event:
                return {"record": document, "recorded_revision": revision,
                        "current_revision": document["revisions"][-1]["revision"],
                        "host_step": _next(document["revisions"][-1])}
            if prior and when < _timestamp(prior["observed_at"], "prior progress observation"):
                raise StateError("older progress cannot replace a later observation for this stage")
            if stage == "relayed" and (prior is None or prior["session_ref"] != event["session_ref"]):
                result = progress.get("result")
                if result and (result["outcome"] == "uncertain" or "returned" not in progress):
                    raise StateError("reconcile and return the existing result before relaying a new author turn")
                if any(when < _timestamp(item["observed_at"], "prior progress observation")
                       for item in progress.values()):
                    raise StateError("a new relayed turn cannot precede its existing progress")
                # Fresh author work supersedes its earlier disposition, while a
                # later verification of the same saved turn leaves it intact.
                for downstream in ("disposition", "result", "returned"):
                    progress.pop(downstream, None)
            if stage == "returned" and "result" not in progress:
                raise StateError("record the actual result before its return")
            if stage == "returned" and when < _timestamp(progress["result"]["observed_at"], "result observation"):
                raise StateError("a return observation cannot precede the current result")
            if stage == "result" and "returned" in progress:
                # A reconciled result still needs its own actual return.
                del progress["returned"]
            progress[stage] = event
            selected["progress_history"].append(event)
            self._save(document)
        return {"record": document, "recorded_revision": revision,
                "current_revision": document["revisions"][-1]["revision"],
                "host_step": _next(document["revisions"][-1])}

    def pending_sources(self, actor_id: str, stream: str) -> list[dict[str, Any]]:
        """Return locally pending observed items, including held/awaiting-author work.

        This is not an inbox scan and does not establish absence of other items.
        """
        _key(actor_id, stream, "validation-only")
        with self._lock:
            rows = self._db.execute("SELECT document FROM relay_observed_sources WHERE actor_id=? AND stream=? ORDER BY source_id",
                                    (actor_id, stream)).fetchall()
        pending = []
        for row in rows:
            document = json.loads(row[0])
            current_number = document["revisions"][-1]["revision"]
            for revision in document["revisions"]:
                # An edit supersedes unrelayed old context, not a pending author
                # response or outcome already in flight for that earlier revision.
                if revision["revision"] != current_number and not any(
                    stage in revision["progress"] for stage in ("relayed", "result")
                ):
                    continue
                if _next(revision) != "complete":
                    pending.append({"actor_id": actor_id, "actor_kind": document["actor_kind"], "stream": stream,
                                    "source_id": document["source_id"], "last_observed_at": document["last_observed_at"],
                                    "ui_unread": document["ui_unread"], "revision": revision,
                                    "current_revision": current_number, "host_step": _next(revision)})
        return pending

    def source_packet(self, actor_id: str, stream: str) -> dict[str, Any]:
        return {"schema": "interlocutors-relay/source-packet/v1", "local_only": True, "executes": False,
                "actor_id": actor_id, "stream": stream, "items": self.pending_sources(actor_id, stream),
                "coverage": "previously observed sources only; inspect the authorized stream separately"}


def apply_source_request(store: SourceStore, request: dict[str, Any]) -> Any:
    if not isinstance(request, dict) or set(request) - {"method", "arguments"}:
        raise ValidationError("request must contain only method and arguments")
    method, arguments = request.get("method"), request.get("arguments", {})
    if not isinstance(method, str) or method not in SOURCE_METHODS or not isinstance(arguments, dict):
        raise ValidationError("unknown source method or invalid arguments")
    return getattr(store, method)(**arguments)
