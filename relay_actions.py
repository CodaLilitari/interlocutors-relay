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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from relay_core import (
    ConflictError, NotFoundError, StateError, Store, ValidationError,
    _enforce_policy, _id, _json, _now, _source_ref, _validate_policy, _zoned_timestamp,
)


ACTIONS = {"message", "react", "share", "share_with_commentary", "post", "original_post",
           "comment", "reply", "friend_request", "block", "like_page", "follow_page",
           "edit_own_content", "delete_own_content", "unfollow_page", "unfollow_profile",
           "hide_post", "hide_source"}
ACTION_METHODS = {"create_action", "begin_action", "record_outcome", "reconcile",
                  "inspect", "action_packet", "recover"}
_SURFACES = {"feed_item", "post", "comment", "profile_timeline", "page_timeline", "message_thread", "profile", "page"}
_CONTENT_ACTIONS = {"message", "post", "original_post", "comment", "reply", "share_with_commentary", "edit_own_content"}
_PUBLICATION_ACTIONS = {"post", "original_post", "comment", "reply", "share", "share_with_commentary"}
_OUTCOMES = {"verified", "uncertain", "failed_before_submit", "submitted_pending", "not_published"}
_PACKET_SCHEMA = "interlocutors-relay/action-packet/v1"
_PAGE_RELATIONSHIP_STATES = {
    "like_page": ("page_like_state", "not_liked", "liked", "already_liked"),
    "follow_page": ("page_follow_state", "not_following", "following", "already_following"),
}
_OWN_CONTENT_ACTIONS = {"edit_own_content", "delete_own_content"}
_MODERATION_STATES = {
    "unfollow_page": ("page_follow_state", "following", "not_following", {"page"}),
    "unfollow_profile": ("profile_follow_state", "following", "not_following", {"profile"}),
    "hide_post": ("post_visibility", "visible", "hidden", {"post", "feed_item"}),
    "hide_source": ("source_visibility", "visible", "hidden", {"profile", "page"}),
    "block": ("block_state", "not_blocked", "blocked", {"profile", "page"}),
}
_GUARDED_ACTIONS = _OWN_CONTENT_ACTIONS | set(_MODERATION_STATES)
_EXCLUDED_LIVE_TESTS = {"block", "delete_own_content"}
_PREFLIGHT_MAX_AGE_SECONDS = 300


def _reference(value: Any, label: str) -> Any:
    """Keep the reference exactly; its claimed provenance remains an attestation."""
    if not isinstance(value, (str, dict)) or not value or (isinstance(value, str) and not value.strip()):
        raise ValidationError(f"{label} must be a nonempty reference string or object")
    return json.loads(_source_ref(value, label))


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    return json.loads(_json(value, label))


def _https_url(value: Any, label: str) -> str:
    try:
        parsed = urlsplit(value) if isinstance(value, str) else None
        valid = (parsed is not None and parsed.scheme == "https" and parsed.hostname
                 and parsed.path not in {"", "/"} and not parsed.username and not parsed.password
                 and not any(char.isspace() for char in value))
    except ValueError:
        valid = False
    if not valid:
        raise ValidationError(f"{label} requires an absolute HTTPS target URL")
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value, "snapshot").encode("utf-8")).hexdigest()


def _snapshot(value: Any, label: str) -> dict[str, Any]:
    value = _object(value, label)
    fields = {"owner_id", "owner_kind", "revision_ref", "text", "media_refs"}
    if set(value) != fields:
        raise ValidationError(f"{label} requires owner, revision, exact text and ordered media references")
    _id(value["owner_id"], f"{label}.owner_id")
    if value["owner_kind"] not in {"page", "profile"}:
        raise ValidationError(f"{label}.owner_kind must be page or profile")
    _reference(value["revision_ref"], f"{label}.revision_ref")
    if not isinstance(value["text"], str) or not isinstance(value["media_refs"], list):
        raise ValidationError(f"{label} requires exact text and ordered media_refs")
    for ref in value["media_refs"]:
        _reference(ref, f"{label}.media_refs")
    return value


def _replacement_media(candidate: dict[str, Any]) -> list[Any]:
    data = candidate["action_data"]
    if data["media_mode"] == "preserve":
        return data["original_snapshot"]["media_refs"]
    refs = [item["ref"] for item in candidate.get("attachments", [])]
    if data.get("platform_media") is not None:
        refs.append(data["platform_media"]["ref"])
    return refs


def _validate_guarded_plan(action: str, actor: dict[str, Any], target: dict[str, Any],
                           candidate: dict[str, Any]) -> None:
    """Validate an observed, explicitly selected transition, not a UI capability."""
    if action not in _GUARDED_ACTIONS:
        return
    data = candidate.get("action_data", {})
    selected = {"actor_id": actor["actor_id"], "actor_kind": actor["actor_kind"],
                "target_id": target["destination_id"], "target_url": target.get("target_url")}
    _https_url(selected["target_url"], "target.target_url")
    for key, value in selected.items():
        if data.get(key) != value:
            raise ValidationError(f"selected action_data.{key} must match the exact actor and target")
    _id(data.get("reason"), "action_data.reason")
    allowed = set(selected) | {"reason"}
    if action in _OWN_CONTENT_ACTIONS:
        if target["surface"] not in {"post", "comment"}:
            raise ValidationError("own-content mutation requires an exact post or comment")
        if target["surface"] == "comment":
            _id(target.get("reply_parent_id"), "target.reply_parent_id")
        elif target.get("reply_parent_id") is not None:
            raise ValidationError("own-post mutation cannot name a reply parent")
        original = _snapshot(data.get("original_snapshot"), "action_data.original_snapshot")
        if original != _snapshot(target.get("current_snapshot"), "target.current_snapshot"):
            raise ValidationError("selected original snapshot differs from observed current content")
        if (original["owner_id"], original["owner_kind"]) != (actor["actor_id"], actor["actor_kind"]):
            raise ValidationError("own-content mutation requires the acting identity to own the content")
        allowed.add("original_snapshot")
        if action == "edit_own_content":
            allowed |= {"media_mode", "platform_media"}
            mode = data.get("media_mode")
            if mode not in {"preserve", "replace", "remove"}:
                raise ValidationError("edit requires explicit preserve, replace or remove media_mode")
            new_media = candidate.get("attachments", []) or data.get("platform_media")
            if (mode == "replace" and not new_media) or (mode != "replace" and new_media):
                raise ValidationError("only replace media_mode may contain selected replacement media")
            after_media = _replacement_media(candidate)
            if candidate.get("text", "") == original["text"] and after_media == original["media_refs"]:
                raise ValidationError("unchanged content is a no-op, not an edit operation")
            if not candidate.get("text", "").strip() and not after_media:
                raise ValidationError("empty content requires a separately selected deletion")
        else:
            allowed |= {"deletion_mode", "recovery_description", "recovery_ref"}
            if data.get("deletion_mode") not in {"recoverable", "permanent"}:
                raise ValidationError("delete requires explicit recoverable or permanent deletion_mode")
            if data["deletion_mode"] == "recoverable":
                _id(data.get("recovery_description"), "action_data.recovery_description")
                _reference(data.get("recovery_ref"), "action_data.recovery_ref")
            elif "recovery_description" in data or "recovery_ref" in data:
                raise ValidationError("permanent deletion cannot claim a recovery route")
    else:
        field, before, _, surfaces = _MODERATION_STATES[action]
        if target["surface"] not in surfaces or target.get("reply_parent_id") is not None:
            raise ValidationError(f"{action} requires its exact supported target surface")
        if target.get(field) != before:
            raise ValidationError(f"{action} requires observed {before}; desired state is a no-op")
        _reference(target.get("state_revision_ref"), "target.state_revision_ref")
        if data.get("current_state_ref") != target["state_revision_ref"]:
            raise ValidationError("selected current_state_ref differs from observed state revision")
        allowed.add("current_state_ref")
        if action == "block":
            _reference(data.get("abuse_context_ref"), "action_data.abuse_context_ref")
            if data.get("block_scope") != actor["actor_kind"] + "_block":
                raise ValidationError("block_scope must identify the acting profile or Page block, not a Page ban")
            allowed |= {"abuse_context_ref", "block_scope"}
    if set(data) - allowed:
        raise ValidationError("selected mutation includes unrelated or unsupported action data")
    if action != "edit_own_content" and (candidate.get("text", "") or candidate.get("attachments")):
        raise ValidationError("moderation/deletion is separate from publishing text or media")


def _recent_observation(value: Any, label: str, created_at: str) -> datetime:
    _zoned_timestamp(value, label)
    observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    if observed < created or observed > now or (now - observed).total_seconds() > _PREFLIGHT_MAX_AGE_SECONDS:
        raise ValidationError(f"{label} must be a fresh observation after action creation (maximum five minutes)")
    return observed


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

    def _mutation_duplicate(self, db, action):
        """Completed choices and consumed state snapshots cannot be cloned into retries."""
        if action["action"] not in _GUARDED_ACTIONS:
            return None
        for row in db.execute("SELECT document FROM relay_actions WHERE action_id<>?", (action["action_id"],)):
            other = json.loads(row[0])
            if other["action"] not in _GUARDED_ACTIONS:
                continue
            if any(other["actor"].get(key) != action["actor"].get(key) for key in ("actor_id", "actor_kind")):
                continue
            if other["target"]["destination_id"] != action["target"]["destination_id"]:
                continue
            own = action["action"] in _OWN_CONTENT_ACTIONS and other["action"] in _OWN_CONTENT_ACTIONS
            same_state = (own and other["target"].get("current_snapshot") == action["target"]["current_snapshot"])
            if not own and other["action"] == action["action"]:
                same_state = other["target"].get("state_revision_ref") == action["target"].get("state_revision_ref")
            same_choice = (other["action"] == action["action"]
                           and _candidate_provenance(other) == _candidate_provenance(action))
            if same_state or same_choice:
                for operation in self._operations(db, other["action_id"]):
                    if operation["state"] == "verified":
                        return operation["operation_id"]
        return None

    @staticmethod
    def _bound_evidence(action):
        expected = {"actor_id": action["actor"]["actor_id"], "actor_kind": action["actor"]["actor_kind"],
                    "destination_id": action["target"]["destination_id"], "surface": action["target"]["surface"],
                    "audience": action["target"]["audience"], "action": action["action"],
                    "action_sha256": action["action_sha256"]}
        if "reply_parent_id" in action["target"]:
            expected["reply_parent_id"] = action["target"]["reply_parent_id"]
        return expected

    @staticmethod
    def _matches(evidence, expected, label):
        for field, value in expected.items():
            if (field not in evidence or evidence[field] != value
                    or (isinstance(value, bool) and evidence[field] is not value)):
                raise ValidationError(f"{label}.{field} does not match selected action")

    @classmethod
    def _preflight(cls, action, evidence):
        evidence = _object(evidence, "preflight_evidence")
        _id(evidence.get("observation"), "preflight_evidence.observation")
        _id(evidence.get("control_label"), "preflight_evidence.control_label")
        _reference(evidence.get("source_ref"), "preflight_evidence.source_ref")
        _recent_observation(evidence.get("observed_at"), "preflight_evidence.observed_at", action["created_at"])
        expected = cls._bound_evidence(action) | {"target_url": action["target"]["target_url"],
                                                  "control_available": True}
        data = action["candidate"]["action_data"]
        if action["action"] in _OWN_CONTENT_ACTIONS:
            expected["current_snapshot"] = action["target"]["current_snapshot"]
            if action["action"] == "delete_own_content":
                expected["deletion_mode"] = data["deletion_mode"]
                if data["deletion_mode"] == "recoverable":
                    expected["recovery_description"] = data["recovery_description"]
        else:
            field, before, _, _ = _MODERATION_STATES[action["action"]]
            expected[field] = before
            expected["state_revision_ref"] = action["target"]["state_revision_ref"]
            if action["action"] == "block":
                expected["block_scope"] = data["block_scope"]
        cls._matches(evidence, expected, "preflight_evidence")
        return evidence

    @classmethod
    def _confirmation(cls, action, value, label, preflight, operation_id):
        """Record a freshly checked human decision; the host authenticates its source."""
        value = _object(value, label)
        expected = {"approved": True, "granted_by": "user", "actor_id": action["actor"]["actor_id"],
                    "actor_kind": action["actor"]["actor_kind"], "destination_id": action["target"]["destination_id"],
                    "target_url": action["target"]["target_url"], "action_sha256": action["action_sha256"],
                    "action": action["action"], "operation_id": operation_id}
        data = action["candidate"]["action_data"]
        if action["action"] == "delete_own_content":
            expected.update(deletion_mode="permanent", original_snapshot_sha256=_digest(data["original_snapshot"]))
        else:
            expected.update(block_scope=data["block_scope"], abuse_context_ref=data["abuse_context_ref"])
        cls._matches(value, expected, label)
        _id(value.get("rationale"), label + ".rationale")
        _reference(value.get("source_ref"), label + ".source_ref")
        _zoned_timestamp(value.get("granted_at"), label + ".granted_at")
        granted = datetime.fromisoformat(value["granted_at"].replace("Z", "+00:00"))
        created = datetime.fromisoformat(action["created_at"].replace("Z", "+00:00"))
        if granted < created or granted > datetime.now(timezone.utc):
            raise ValidationError(f"{label} must be an actual human confirmation after this exact action was prepared")
        return value

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
        if action in _PAGE_RELATIONSHIP_STATES:
            if target["surface"] != "page" or target.get("reply_parent_id") is not None:
                raise ValidationError(f"{action} requires an exact Page target, not a content item")
            page_url = target.get("page_url")
            if not isinstance(page_url, str):
                raise ValidationError(f"{action} requires an exact target.page_url")
            try:
                parsed = urlsplit(page_url)
                valid_url = (parsed.scheme == "https" and parsed.hostname and parsed.path not in {"", "/"}
                             and not parsed.username and not parsed.password
                             and not any(char.isspace() for char in page_url))
            except ValueError:
                valid_url = False
            if not valid_url:
                raise ValidationError(f"{action} requires an absolute HTTPS Page URL")
            field, before, _, no_op = _PAGE_RELATIONSHIP_STATES[action]
            if target.get(field) != before:
                raise ValidationError(f"{action} requires observed {before} state; {no_op} is a no-op")
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
        if action in _PAGE_RELATIONSHIP_STATES:
            if action_data.get("page_url") != target["page_url"]:
                raise ValidationError("selected page_url must match the exact Page target")
            if candidate.get("text", "") or attachments or set(action_data) - {"page_url", "rationale"}:
                raise ValidationError(f"{action} is distinct from content, reactions and other relationship actions")
        if action in {"share", "share_with_commentary"}:
            _reference(action_data.get("source_ref"), "action_data.source_ref")
        _validate_guarded_plan(action, actor, target, candidate)
        if action in _CONTENT_ACTIONS - {"edit_own_content"} and not (candidate.get("text", "").strip() or attachments or platform_media):
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
        if action in _OWN_CONTENT_ACTIONS:
            intent["original_snapshot_sha256"] = _digest(action_data["original_snapshot"])
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
        if action["action"] in _EXCLUDED_LIVE_TESTS and action["scope"] == "live_test":
            step = "excluded_from_live_testing"
        return {"schema": _PACKET_SCHEMA, "local_only": True, "executes": False,
                "content_state": "selected_action", "action_authority": action["authority"],
                "execution_state": state, "host_step": step, "record": action}

    def begin_action(self, action_id: str, operation_id: str, *, preflight_evidence: Any = None,
                     permanent_delete_confirmation: Any = None, block_permission: Any = None) -> dict[str, Any]:
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
            if action["action"] in _EXCLUDED_LIVE_TESTS and action["scope"] == "live_test":
                raise StateError("blocking and deletion are excluded from live testing")
            operations = self._operations(db, action_id)
            if operations and operations[-1]["state"] != "failed_before_submit":
                raise StateError("action already submitted or unresolved; reconcile instead of repeating")
            duplicate = self._pending_duplicate(db, action)
            if duplicate is not None:
                raise StateError(f"selected publication duplicates submitted operation {duplicate}; do not resubmit")
            duplicate = self._mutation_duplicate(db, action)
            if duplicate is not None:
                raise StateError(f"selected mutation repeats completed choice or consumed snapshot {duplicate}")
            preflight, delete_confirmation, blocking = None, None, None
            if action["action"] in _GUARDED_ACTIONS:
                _validate_guarded_plan(action["action"], action["actor"], action["target"], action["candidate"])
                preflight = self._preflight(action, preflight_evidence)
            elif preflight_evidence is not None:
                raise ValidationError("preflight_evidence applies only to explicit content-management/moderation actions")
            if action["action"] == "delete_own_content" and action["candidate"]["action_data"]["deletion_mode"] == "permanent":
                delete_confirmation = self._confirmation(action, permanent_delete_confirmation, "permanent_delete_confirmation", preflight, operation_id)
            elif permanent_delete_confirmation is not None:
                raise ValidationError("permanent_delete_confirmation applies only to permanent deletion")
            if action["action"] == "block":
                blocking = self._confirmation(action, block_permission, "block_permission", preflight, operation_id)
            elif block_permission is not None:
                raise ValidationError("block_permission applies only to blocking")
            if _local_attachments(action["candidate"]) != action.get("local_attachments", []):
                raise StateError("selected local attachment bytes changed; inspect and obtain a new selection")
            guard = db.execute("SELECT * FROM delivery_guard WHERE singleton=1").fetchone()
            if guard is not None:
                raise StateError(f"shared delivery is blocked by {guard['state']} operation {guard['operation_id']}")
            operation = {"operation_id": operation_id, "action_id": action_id,
                         "action_sha256": action["action_sha256"], "state": "in_flight",
                         "started_at": _now(), "observations": []}
            if preflight is not None:
                operation["preflight_evidence"] = preflight
            if delete_confirmation is not None:
                operation["permanent_delete_confirmation"] = delete_confirmation
            if blocking is not None:
                operation["block_permission"] = blocking
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
        if action["action"] in _GUARDED_ACTIONS and "publication_state" in evidence:
            raise ValidationError("content management/moderation results cannot use publication_state")
        _id(evidence.get("observation"), "evidence.observation")
        _reference(evidence.get("source_ref"), "evidence.source_ref")
        if action["action"] == "edit_own_content" and evidence.get("edit_revision_state") == "pending":
            if outcome != "uncertain" or evidence.get("submission_observed") is not True or evidence.get("positive_no_submit") is True:
                raise ValidationError("an observed pending edit has known submission and an unresolved revised-state outcome")
            ActionStore._matches(evidence, ActionStore._bound_evidence(action) | {
                "target_url": action["target"]["target_url"],
                "original_snapshot_sha256": action["original_snapshot_sha256"],
            }, "pending edit evidence")
            _reference(evidence.get("pending_revision_ref"), "evidence.pending_revision_ref")
            _zoned_timestamp(evidence.get("observed_at"), "evidence.observed_at")
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
            expected = ActionStore._bound_evidence(action)
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
            if action["action"] in _PAGE_RELATIONSHIP_STATES:
                field, _, after, _ = _PAGE_RELATIONSHIP_STATES[action["action"]]
                expected.update(page_url=action["target"]["page_url"], action_taken=True)
                expected[field] = after
            if action["action"] in _GUARDED_ACTIONS:
                _validate_guarded_plan(action["action"], action["actor"], action["target"], action["candidate"])
                expected.update(target_url=action["target"]["target_url"], action_taken=True)
                if action["action"] in _OWN_CONTENT_ACTIONS:
                    original = action_data["original_snapshot"]
                    expected["original_snapshot_sha256"] = _digest(original)
                    if action["action"] == "edit_own_content":
                        expected["content_state"] = "edited"
                        expected["media_mode"] = action_data["media_mode"]
                        result_snapshot = _snapshot(evidence.get("result_snapshot"), "evidence.result_snapshot")
                        if result_snapshot["revision_ref"] == original["revision_ref"]:
                            raise ValidationError("edited content needs a newly observed revision reference")
                        ActionStore._matches(result_snapshot, {
                            "owner_id": original["owner_id"], "owner_kind": original["owner_kind"],
                            "text": action["candidate"].get("text", ""),
                            "media_refs": _replacement_media(action["candidate"]),
                        }, "evidence.result_snapshot")
                    else:
                        recoverable = action_data["deletion_mode"] == "recoverable"
                        expected.update(deletion_mode=action_data["deletion_mode"], deletion_observed=True,
                                        deletion_state="trashed" if recoverable else "permanently_deleted",
                                        restore_available=recoverable)
                        _reference(evidence.get("deletion_confirmation_ref"), "evidence.deletion_confirmation_ref")
                        if recoverable:
                            expected["recovery_description"] = action_data["recovery_description"]
                else:
                    field, _, after, _ = _MODERATION_STATES[action["action"]]
                    expected[field] = after
                    expected["prior_state_revision_ref"] = action["target"]["state_revision_ref"]
                    _reference(evidence.get("result_state_ref"), "evidence.result_state_ref")
                    if evidence["result_state_ref"] == action["target"]["state_revision_ref"]:
                        raise ValidationError("verified mutation needs a newly observed result state reference")
                    if action["action"] == "block":
                        expected["block_scope"] = action_data["block_scope"]
            if action["action"] in {"share", "share_with_commentary"}:
                expected["shared_source_ref"] = action_data["source_ref"]
            if action_data.get("platform_media") is not None:
                expected["platform_media_ref"] = action_data["platform_media"]["ref"]
                expected["platform_media_kind"] = action_data["platform_media"]["kind"]
            ActionStore._matches(evidence, expected, f"{outcome} evidence")
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
            pending_edit_observed = any(item["evidence"].get("edit_revision_state") == "pending"
                                        and item["evidence"].get("submission_observed") is True
                                        for item in operation["observations"])
            if pending_edit_observed and outcome == "failed_before_submit":
                raise StateError("a positively observed submitted edit cannot become failed_before_submit")
            evidence = self._evidence(action, outcome, evidence)
            if action["action"] in _GUARDED_ACTIONS and (outcome == "verified" or evidence.get("edit_revision_state") == "pending"):
                observed = datetime.fromisoformat(evidence["observed_at"].replace("Z", "+00:00"))
                started = datetime.fromisoformat(operation["started_at"].replace("Z", "+00:00"))
                if observed < started or observed > datetime.now(timezone.utc):
                    raise ValidationError("verified mutation evidence must be observed after the claimed operation")
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
