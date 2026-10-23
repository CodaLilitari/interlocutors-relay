"""Actor-bound platform action records for a supported-tool host operator.

Messages, reactions, shares and relationship actions are not fake text posts.
This module records their exact selected intent and observations, but never
opens a browser or performs an account action. Its additive tables share the
existing core's delivery guard so uncertain actions cannot overlap core posts.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from relay_core import (
    ConflictError, NotFoundError, StateError, Store, ValidationError,
    _enforce_policy, _id, _json, _now, _source_ref, _validate_policy, _zoned_timestamp,
)


ACTIONS = {"message", "react", "share", "share_with_commentary", "post", "original_post",
           "comment", "reply", "friend_request", "block"}
ACTION_METHODS = {"create_action", "begin_action", "record_outcome", "reconcile",
                  "inspect", "action_packet", "recover"}
_SURFACES = {"feed_item", "post", "comment", "profile_timeline", "page_timeline", "message_thread", "profile", "page"}
_CONTENT_ACTIONS = {"message", "post", "original_post", "comment", "reply", "share_with_commentary"}
_PUBLICATION_ACTIONS = {"post", "original_post", "comment", "reply", "share", "share_with_commentary"}
_OUTCOMES = {"verified", "uncertain", "failed_before_submit", "submitted_pending", "not_published"}
_PACKET_SCHEMA = "interlocutors-relay/action-packet/v1"


def _reference(value: Any, label: str) -> Any:
    """Keep the reference exactly; its claimed provenance remains an attestation."""
    if not isinstance(value, (str, dict)) or not value or (isinstance(value, str) and not value.strip()):
        raise ValidationError(f"{label} must be a nonempty reference string or object")
    return json.loads(_source_ref(value, label))


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    return json.loads(_json(value, label))


def _guard_id(operation_id: str) -> str:
    return "relay-action:" + operation_id


def _publication_binding(action: dict[str, Any]) -> dict[str, Any]:
    """Stable delivery identity, excluding presentation and UI observation data.

    The same post can appear as a feed item, and the same destination timeline
    can appear through a profile or Page view. Those surfaces stay exact in the
    action digest and outcome evidence, but cannot make a repeated submission
    into a different action. Shared-source and commentary differences remain
    part of the content comparison.
    """
    return {"action": {"original_post": "post", "reply": "comment", "share_with_commentary": "share"}.get(action["action"], action["action"]),
            "actor": {key: action["actor"][key] for key in ("actor_id", "actor_kind")},
            "target": {key: action["target"].get(key) for key in
                       ("destination_id", "audience", "reply_parent_id")}}


def _publication_content(action: dict[str, Any]) -> dict[str, Any]:
    """Match pending text/media even when a clone supplies new provenance IDs."""
    candidate = action["candidate"]
    data = candidate.get("action_data", {})
    attachments = candidate.get("attachments", [])
    return {"text": candidate.get("text", ""),
            "attachments": [{"sha256": item["sha256"], "byte_size": item["byte_size"],
                             "alt_text": attachment.get("alt_text", attachment.get("core_media", {}).get("alt_text"))}
                            for item, attachment in zip(action.get("local_attachments", []), attachments)],
            "platform_media": {key: data["platform_media"].get(key) for key in ("kind", "ref")}
                              if data.get("platform_media") is not None else None,
            "shared_source_ref": data.get("source_ref") if action["action"] in {"share", "share_with_commentary"} else None}


def _candidate_provenance(action: dict[str, Any]) -> dict[str, Any]:
    return {key: action["candidate"].get(key) for key in ("candidate_id", "source_ref", "response_ref")}


def _local_attachments(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Capture actual selected upload bytes without changing candidate provenance.

    A ref can itself be an absolute path, or ``local_path`` can explicitly map
    a source reference to its upload file. Native assets use platform_media.
    Hashes describe local bytes, not a transcoded platform rendering.
    """
    captured = []
    for item in candidate.get("attachments", []):
        ref = item["ref"]
        value = item.get("local_path", ref.get("path") if isinstance(ref, dict) else ref)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValidationError("local attachment requires an exact absolute ref path or local_path")
        path = Path(value)
        try:
            before = path.stat()
            if not path.is_file():
                raise ValidationError("local attachment must be a file")
            digest, size = hashlib.sha256(), 0
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(128 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            after = path.stat()
        except OSError as exc:
            raise ValidationError("local attachment is unavailable") from exc
        if size == 0 or before.st_size != size or after.st_size != size or before.st_mtime_ns != after.st_mtime_ns:
            raise ValidationError("local attachment is empty or changed during capture")
        descriptor = {"ref": ref, "path": str(path), "sha256": digest.hexdigest(), "byte_size": size}
        media = item.get("core_media", {})
        if not isinstance(media, dict):
            raise ValidationError("attachment.core_media must be an object")
        for field in ("sha256", "byte_size"):
            for supplied in (item, media):
                if field in supplied and supplied[field] != descriptor[field]:
                    raise ValidationError(f"local attachment bytes do not match selected {field}")
        if "alt_text" in item and "alt_text" in media and item["alt_text"] != media["alt_text"]:
            raise ValidationError("attachment alt_text disagrees with selected core_media")
        captured.append(descriptor)
    return captured


class ActionStore(Store):
    """Additive action companion reusing Store's transactions and writer guard.

    Core content tables/history are preserved. Only the allowlisted action
    methods belong to this interface; inherited core methods are not exposed
    by ``apply_action_request``. Use a normal ``Store`` for existing core cases.
    """

    def __init__(self, db_path: str | Path):
        super().__init__(db_path)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS relay_actions (
                action_id TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS relay_action_operations (
                ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL UNIQUE,
                action_id TEXT NOT NULL REFERENCES relay_actions(action_id),
                document TEXT NOT NULL);
        """)

    def _action(self, db, action_id):
        row = db.execute("SELECT document FROM relay_actions WHERE action_id=?", (action_id,)).fetchone()
        if row is None:
            raise NotFoundError("action not found")
        return json.loads(row[0])

    def _operation(self, db, operation_id):
        row = db.execute("SELECT document FROM relay_action_operations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            raise NotFoundError("action operation not found")
        return json.loads(row[0])

    @staticmethod
    def _operations(db, action_id):
        return [json.loads(row[0]) for row in db.execute(
            "SELECT document FROM relay_action_operations WHERE action_id=? ORDER BY ordinal", (action_id,))]

    @staticmethod
    def _save_operation(db, operation):
        db.execute("UPDATE relay_action_operations SET document=? WHERE operation_id=?",
                   (_json(operation, "action operation"), operation["operation_id"]))

    def _pending_duplicate(self, db, action):
        """A new local ID cannot authorize another copy of a submitted item.

        While pending, match actual actor/target/content regardless of candidate
        IDs. Once resolved, retain the original candidate's no-resend boundary;
        a genuinely new author selection is a separate action.
        """
        if action["action"] not in _PUBLICATION_ACTIONS:
            return None
        binding = _publication_binding(action)
        for row in db.execute("SELECT document FROM relay_actions WHERE action_id<>?", (action["action_id"],)):
            other = json.loads(row[0])
            if other["action"] not in _PUBLICATION_ACTIONS or _publication_binding(other) != binding:
                continue
            for operation in self._operations(db, other["action_id"]):
                if operation["state"] == "submitted_pending" and _publication_content(other) == _publication_content(action):
                    return operation["operation_id"]
                has_submission = any(item["outcome"] in {"submitted_pending", "not_published"}
                                     for item in operation["observations"])
                if has_submission and _candidate_provenance(other) == _candidate_provenance(action):
                    return operation["operation_id"]
        return None

    def create_action(self, action_id: str, action: str, actor: dict[str, Any],
                      target: dict[str, Any], candidate: dict[str, Any], selection_ref: Any,
                      authority: dict[str, Any], scope: str = "live",
                      friend_request_permission: dict[str, Any] | None = None,
                      account_policy: dict[str, Any] | None = None) -> dict[str, Any]:
        """Freeze actual selected content/action, actor, target and user authority.

        ``authority.source_ref`` identifies applicable human instructions. A
        friend request additionally needs affirmative per-target user permission
        and a rationale; author selection alone is insufficient. These are local
        attestations, not authentication of the claimed source.
        """
        _id(action_id, "action_id")
        if action not in ACTIONS:
            raise ValidationError("unsupported platform action")
        if scope not in {"live", "live_test"}:
            raise ValidationError("scope must be live or live_test")
        actor = _object(actor, "actor")
        _id(actor.get("actor_id"), "actor.actor_id")
        if actor.get("actor_kind") not in {"page", "profile"}:
            raise ValidationError("actor.actor_kind must be page or profile")
        _reference(actor.get("source_ref"), "actor.source_ref")
        if "observed_at" in actor:
            _zoned_timestamp(actor["observed_at"], "actor.observed_at")
        target = _object(target, "target")
        _id(target.get("destination_id"), "target.destination_id")
        _id(target.get("audience"), "target.audience")
        if target.get("surface") not in _SURFACES:
            raise ValidationError("target.surface is unsupported")
        if action == "message" and (target["surface"] != "message_thread" or target["audience"] != "private"):
            raise ValidationError("message requires its actual message_thread and private audience")
        if action == "friend_request" and target["surface"] != "profile":
            raise ValidationError("friend request requires an exact profile target")
        if action == "block" and target["surface"] not in {"profile", "page"}:
            raise ValidationError("block requires an exact profile or Page target")
        if action == "friend_request" and actor["actor_kind"] != "profile":
            raise ValidationError("friend request requires the intended profile actor")
        if action in {"comment", "reply"}:
            _id(target.get("reply_parent_id"), "target.reply_parent_id")
        if action in {"post", "original_post"} and ("reply_parent_id" not in target or target["reply_parent_id"] is not None):
            raise ValidationError("original post requires explicit reply_parent_id null")
        if "reply_parent_id" in target and target["reply_parent_id"] is not None:
            _id(target["reply_parent_id"], "target.reply_parent_id")
        policy = _validate_policy({} if account_policy is None else account_policy)
        _enforce_policy({"actor_id": actor["actor_id"], "actor_kind": actor["actor_kind"],
                         "destination_id": target["destination_id"]}, policy)
        candidate = _object(candidate, "candidate")
        _id(candidate.get("candidate_id"), "candidate.candidate_id")
        _reference(candidate.get("source_ref"), "candidate.source_ref")
        if not isinstance(candidate.get("text", ""), str):
            raise ValidationError("candidate.text must be a string")
        attachments = candidate.get("attachments", [])
        if not isinstance(attachments, list):
            raise ValidationError("candidate.attachments must be an ordered list")
        for attachment in attachments:
            if not isinstance(attachment, dict):
                raise ValidationError("candidate attachment must be an object")
            _reference(attachment.get("ref"), "candidate attachment.ref")
        action_data = candidate.get("action_data", {})
        if not isinstance(action_data, dict):
            raise ValidationError("candidate.action_data must be an object")
        platform_media = action_data.get("platform_media")
        if platform_media is not None:
            if not isinstance(platform_media, dict) or platform_media.get("kind") not in {"gif", "image"}:
                raise ValidationError("platform_media must identify a gif or image")
            _reference(platform_media.get("ref"), "platform_media.ref")
        if action == "react":
            _id(action_data.get("reaction"), "action_data.reaction")
        if action in {"share", "share_with_commentary"}:
            _reference(action_data.get("source_ref"), "action_data.source_ref")
        if action in _CONTENT_ACTIONS and not (candidate.get("text", "").strip() or attachments or platform_media):
            raise ValidationError("content action requires actual text, attachments or selected platform media")
        authority = _object(authority, "authority")
        _reference(authority.get("source_ref"), "authority.source_ref")
        selection_ref = _reference(selection_ref, "selection_ref")
        permission = None
        if action == "friend_request":
            permission = _object(friend_request_permission, "friend_request_permission")
            if permission.get("approved") is not True or permission.get("granted_by") != "user":
                raise ValidationError("friend request requires explicit user permission")
            for field, value in (("actor_id", actor["actor_id"]), ("destination_id", target["destination_id"])):
                if permission.get(field) != value:
                    raise ValidationError(f"friend request permission does not match {field}")
            _id(permission.get("rationale"), "friend_request_permission.rationale")
            _reference(permission.get("source_ref"), "friend_request_permission.source_ref")
        elif friend_request_permission is not None:
            raise ValidationError("friend_request_permission applies only to friend_request")
        intent = {"action": action, "actor": actor, "target": target, "candidate": candidate,
                  "selection_ref": selection_ref, "authority": authority, "scope": scope,
                  "friend_request_permission": permission, "account_policy": policy,
                  "local_attachments": _local_attachments(candidate)}
        action_sha256 = hashlib.sha256(_json(intent, "selected action").encode("utf-8")).hexdigest()
        document = {"action_id": action_id, "created_at": _now(), "action_sha256": action_sha256, **intent}
        with self._tx() as db:
            try:
                db.execute("INSERT INTO relay_actions VALUES (?,?)", (action_id, _json(document, "action")))
            except sqlite3.IntegrityError as exc:
                raise ConflictError("action_id already exists; selected actions are immutable") from exc
        return document

    def inspect(self, action_id: str) -> dict[str, Any]:
        with self._lock:
            action = self._action(self._db, action_id)
            action["operations"] = self._operations(self._db, action_id)
        action["execution_state"] = action["operations"][-1]["state"] if action["operations"] else "not_started"
        return action

    def action_packet(self, action_id: str) -> dict[str, Any]:
        action = self.inspect(action_id)
        state = action["execution_state"]
        step = {"not_started": "begin_action", "in_flight": "reconcile_before_retry",
                "uncertain": "reconcile_before_retry", "verified": "return_observed_result",
                "failed_before_submit": "review_before_new_attempt",
                "submitted_pending": "observe_pending_publication",
                "not_published": "return_not_published_result"}[state]
        if action["action"] == "block" and action["scope"] == "live_test":
            step = "excluded_from_live_testing"
        return {"schema": _PACKET_SCHEMA, "local_only": True, "executes": False,
                "content_state": "selected_action", "action_authority": action["authority"],
                "execution_state": state, "host_step": step, "record": action}

    def begin_action(self, action_id: str, operation_id: str) -> dict[str, Any]:
        """Commit a single shared-guard claim before the host operates the UI."""
        _id(operation_id, "operation_id")
        with self._tx() as db:
            action = self._action(db, action_id)
            prior = db.execute("SELECT document FROM relay_action_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if prior is not None:
                operation = json.loads(prior[0])
                if operation["action_id"] != action_id:
                    raise ConflictError("operation_id belongs to a different action")
                return {**operation, "newly_claimed": False, "executes": False,
                        "host_step": "inspect_existing_operation"}
            if action["action"] == "block" and action["scope"] == "live_test":
                raise StateError("blocking is excluded from live testing")
            operations = self._operations(db, action_id)
            if operations and operations[-1]["state"] != "failed_before_submit":
                raise StateError("action already submitted or unresolved; reconcile instead of repeating")
            duplicate = self._pending_duplicate(db, action)
            if duplicate is not None:
                raise StateError(f"selected publication duplicates submitted operation {duplicate}; do not resubmit")
            if _local_attachments(action["candidate"]) != action.get("local_attachments", []):
                raise StateError("selected local attachment bytes changed; inspect and obtain a new selection")
            guard = db.execute("SELECT * FROM delivery_guard WHERE singleton=1").fetchone()
            if guard is not None:
                raise StateError(f"shared delivery is blocked by {guard['state']} operation {guard['operation_id']}")
            operation = {"operation_id": operation_id, "action_id": action_id,
                         "action_sha256": action["action_sha256"], "state": "in_flight",
                         "started_at": _now(), "observations": []}
            db.execute("INSERT INTO relay_action_operations(operation_id,action_id,document) VALUES (?,?,?)",
                       (operation_id, action_id, _json(operation, "action operation")))
            db.execute("INSERT INTO delivery_guard VALUES (1,?,'in_flight')", (_guard_id(operation_id),))
        return {**operation, "newly_claimed": True, "executes": False,
                "host_step": "execute_action_once_using_supported_tools", "record": action}

    @staticmethod
    def _evidence(action, outcome, evidence):
        if outcome not in _OUTCOMES:
            raise ValidationError("unsupported action outcome")
        evidence = _object(evidence, "evidence")
        _id(evidence.get("observation"), "evidence.observation")
        _reference(evidence.get("source_ref"), "evidence.source_ref")
        if outcome == "failed_before_submit" and evidence.get("positive_no_submit") is not True:
            raise ValidationError("failed_before_submit requires positive_no_submit true")
        if outcome in {"submitted_pending", "not_published"}:
            if action["action"] not in _PUBLICATION_ACTIONS:
                raise ValidationError("publication outcomes apply only to publication actions")
            if evidence.get("submission_observed") is not True or evidence.get("positive_no_submit") is True:
                raise ValidationError("publication outcome requires submission_observed true and cannot claim no submission")
            if outcome == "submitted_pending" and evidence.get("publication_state") != "pending":
                raise ValidationError("submitted_pending requires publication_state pending")
            if outcome == "not_published" and (evidence.get("publication_state") not in ("rejected", "removed", "withdrawn")
                                               or evidence.get("terminal_observed") is not True):
                raise ValidationError("not_published requires an observed rejection, removal or withdrawal")
        if outcome == "verified":
            if "publication_state" in evidence and evidence["publication_state"] != "published":
                raise ValidationError("verified evidence cannot describe a pending or unpublished result")
            if evidence.get("submission_observed") is False or evidence.get("positive_no_submit") is True:
                raise ValidationError("verified evidence cannot deny submission")
        if outcome in {"verified", "submitted_pending", "not_published"}:
            expected = {"actor_id": action["actor"]["actor_id"], "actor_kind": action["actor"]["actor_kind"],
                        "destination_id": action["target"]["destination_id"], "surface": action["target"]["surface"],
                        "audience": action["target"]["audience"], "action": action["action"],
                        "action_sha256": action["action_sha256"]}
            if "reply_parent_id" in action["target"]:
                expected["reply_parent_id"] = action["target"]["reply_parent_id"]
            if action["action"] in _CONTENT_ACTIONS:
                expected["text"] = action["candidate"].get("text", "")
            attachments = action["candidate"].get("attachments", [])
            if attachments:
                expected["attachment_refs"] = [item["ref"] for item in attachments]
                alt_texts = [item.get("alt_text", item.get("core_media", {}).get("alt_text")) for item in attachments]
                if any(value is not None for value in alt_texts):
                    expected["attachment_alt_texts"] = alt_texts
                observations = evidence.get("attachment_observations")
                if not isinstance(observations, list) or len(observations) != len(attachments):
                    raise ValidationError(f"{outcome} evidence requires one attachment observation per selected upload")
                for observation in observations:
                    if not isinstance(observation, dict):
                        raise ValidationError("attachment observation must be an object")
                    _reference(observation.get("source_ref"), "attachment observation.source_ref")
                    _id(observation.get("observation"), "attachment observation.observation")
            action_data = action["candidate"].get("action_data", {})
            if action["action"] == "react":
                expected["reaction"] = action_data["reaction"]
            if action["action"] in {"share", "share_with_commentary"}:
                expected["shared_source_ref"] = action_data["source_ref"]
            if action_data.get("platform_media") is not None:
                expected["platform_media_ref"] = action_data["platform_media"]["ref"]
                expected["platform_media_kind"] = action_data["platform_media"]["kind"]
            for field, value in expected.items():
                if field not in evidence or evidence[field] != value:
                    raise ValidationError(f"{outcome} evidence.{field} does not match selected action")
            _zoned_timestamp(evidence.get("observed_at"), "evidence.observed_at")
        return evidence

    def _finish(self, operation_id, outcome, evidence, reconciliation_ref=None):
        with self._tx() as db:
            operation = self._operation(db, operation_id)
            if reconciliation_ref is None and operation["state"] != "in_flight":
                raise StateError("record_outcome requires an in-flight operation")
            if reconciliation_ref is not None and operation["state"] not in {"uncertain", "submitted_pending"}:
                raise StateError("reconciliation requires an uncertain or submitted-pending operation")
            if operation["state"] == "submitted_pending" and outcome not in {"submitted_pending", "verified", "not_published"}:
                raise StateError("a known submitted publication may only remain pending, publish, or be observed not published")
            action = self._action(db, operation["action_id"])
            evidence = self._evidence(action, outcome, evidence)
            if operation["state"] == "submitted_pending" and outcome == "verified" and (
                    evidence.get("publication_state") != "published" or evidence.get("submission_observed") is not True):
                raise ValidationError("pending publication requires explicit observed publication before verification")
            observation = {"outcome": outcome, "recorded_at": _now(),
                           "evidence": evidence}
            if reconciliation_ref is not None:
                observation["reconciliation_ref"] = _reference(reconciliation_ref, "reconciliation_ref")
            operation["observations"].append(observation)
            operation["state"] = outcome
            self._save_operation(db, operation)
            if outcome == "uncertain":
                db.execute("UPDATE delivery_guard SET state='uncertain' WHERE singleton=1 AND operation_id=?",
                           (_guard_id(operation_id),))
            else:
                db.execute("DELETE FROM delivery_guard WHERE singleton=1 AND operation_id=?", (_guard_id(operation_id),))
        return operation

    def record_outcome(self, operation_id: str, outcome: str, evidence: Any) -> dict[str, Any]:
        return self._finish(operation_id, outcome, evidence)

    def reconcile(self, operation_id: str, outcome: str, evidence: Any, reconciliation_ref: Any) -> dict[str, Any]:
        """Append an observation of uncertain dispatch or pending publication; never resend."""
        _reference(reconciliation_ref, "reconciliation_ref")
        return self._finish(operation_id, outcome, evidence, reconciliation_ref)

    def recover(self) -> list[str]:
        """Mark this companion's interrupted operations uncertain, preserving core records."""
        with self._tx() as db:
            operations = [json.loads(row[0]) for row in db.execute("SELECT document FROM relay_action_operations")]
            recovered = []
            for operation in operations:
                if operation["state"] != "in_flight":
                    continue
                operation["state"] = "uncertain"
                operation["observations"].append({"outcome": "uncertain", "recorded_at": _now(),
                                                 "evidence": {"recovery": "interrupted_operation"}})
                self._save_operation(db, operation)
                db.execute("UPDATE delivery_guard SET state='uncertain' WHERE singleton=1 AND operation_id=?",
                           (_guard_id(operation["operation_id"]),))
                recovered.append(operation["operation_id"])
        return recovered


def apply_action_request(store: ActionStore, request: dict[str, Any]) -> Any:
    """Dispatch only this companion's supported local methods."""
    if not isinstance(request, dict) or set(request) - {"method", "arguments"}:
        raise ValidationError("request must contain only method and arguments")
    method, arguments = request.get("method"), request.get("arguments", {})
    if not isinstance(method, str) or method not in ACTION_METHODS or not isinstance(arguments, dict):
        raise ValidationError("unknown action method or invalid arguments")
    return getattr(store, method)(**arguments)
