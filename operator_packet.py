"""Minimal, non-executing handoff packets for relay operators.

This module deliberately turns the Store audit view into a small, purpose-bound
dictionary.  It does not authenticate a source, call a provider, open a
browser, select an account, or perform an external action.
"""

from __future__ import annotations

from typing import Any

from relay_core import NotFoundError, StateError, ValidationError


PACKET_SCHEMA = "interlocutors-relay/operator-packet/v1"
AUTHOR_REVIEW = "author_review"
OPERATOR_PREFLIGHT = "operator_preflight"
_MODES = {AUTHOR_REVIEW, OPERATOR_PREFLIGHT}
_ORIGINAL_POST_ACTIONS = {"post", "original_post"}
_REPLY_ACTIONS = {"comment", "reply"}
_MEDIA_FIELDS = ("file_id", "sha256", "byte_size", "mime_type", "alt_text")


def _nonempty(value: Any) -> bool:
    """Accept a meaningful JSON value without treating falsey scalars as scope."""
    return not (
        value is None
        or value is False
        or value == ""
        or value == {}
        or value == []
    )


def _revision(state: dict[str, Any], revision_id: str) -> dict[str, Any]:
    for revision in state["revisions"]:
        if revision["revision_id"] == revision_id:
            return revision
    raise NotFoundError("revision not found for case")


def _public_media(revision: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only media fields that belong in an operator packet.

    The core may retain implementation-only metadata in its local audit view.
    In particular, this function must never propagate a local file path or an
    arbitrary source reference merely because it was attached to a media item.
    """
    media = revision.get("media", [])
    if media is None:
        return []
    if not isinstance(media, list):
        raise StateError("revision media is not a list")

    public: list[dict[str, Any]] = []
    for item in media:
        if not isinstance(item, dict) or any(field not in item for field in _MEDIA_FIELDS):
            raise StateError("revision media lacks a complete immutable manifest")
        public.append({field: item[field] for field in _MEDIA_FIELDS})
    return public


def _payload(revision: dict[str, Any]) -> dict[str, Any]:
    """Build the exact public payload and its local consistency metadata."""
    payload = {
        "text": revision["text"],
        "content_sha256": revision["content_sha256"],
        "transport_lf_sha256": revision["transport_lf_sha256"],
        "media": _public_media(revision),
    }
    # Core versions before media support have no combined-payload digest.  Do
    # not invent one: the text digest remains the truthful text-only digest.
    if "full_payload_sha256" in revision:
        payload["full_payload_sha256"] = revision["full_payload_sha256"]
    return payload


def _operator_intent(binding: dict[str, Any]) -> dict[str, Any]:
    """Validate delivery-only target scope and return its strict allowlist."""
    action = binding.get("action")
    if action not in _ORIGINAL_POST_ACTIONS | _REPLY_ACTIONS:
        raise StateError("operator preflight supports only original-post, comment, or reply actions")
    if "reply_parent_id" not in binding:
        raise StateError("operator preflight requires an explicit reply_parent_id")

    parent = binding["reply_parent_id"]
    if action in _ORIGINAL_POST_ACTIONS:
        if parent is not None:
            raise StateError("an original post must bind reply_parent_id to null")
    elif not isinstance(parent, str) or not parent.strip():
        raise StateError("a comment or reply requires a nonempty reply_parent_id")

    if "audience" not in binding or not _nonempty(binding["audience"]):
        raise StateError("operator preflight requires a nonempty audience")

    return {
        "actor_id": binding["actor_id"],
        "actor_kind": binding["actor_kind"],
        "destination_id": binding["destination_id"],
        "reply_parent_id": parent,
        "audience": binding["audience"],
        "action": action,
    }


def _require_preflight_eligibility(state: dict[str, Any], revision_id: str) -> None:
    if state["selected_revision"] != revision_id:
        raise StateError("operator preflight requires the currently staged revision")

    # Existing final delivery evidence for this exact revision is a duplicate
    # risk, while an in-flight/uncertain record means its outcome is unresolved.
    # A positively established pre-submit failure is intentionally not blocking:
    # it still requires a new, independently checked human authorization.
    blocking = {
        "in_flight": "has an in-flight delivery",
        "uncertain": "has an uncertain delivery",
        "verified": "already has a verified delivery",
    }
    for delivery in state["deliveries"]:
        if delivery["revision_id"] == revision_id and delivery["state"] in blocking:
            raise StateError(f"operator preflight {blocking[delivery['state']]}")


def render_operator_packet(
    store: Any,
    case_id: str,
    revision_id: str,
    mode: str = AUTHOR_REVIEW,
) -> dict[str, Any]:
    """Render a narrowly scoped local packet for an author or operator.

    ``author_review`` can return an un-staged candidate to its authoring route.
    ``operator_preflight`` is stricter: it requires the current staged revision,
    complete delivery target fields, and no unresolved or duplicate local
    delivery record.  Neither mode is an execution permit.
    """
    if mode not in _MODES:
        raise ValidationError("mode must be author_review or operator_preflight")

    state = store.inspect(case_id)
    revision = _revision(state, revision_id)
    packet: dict[str, Any] = {
        "schema": PACKET_SCHEMA,
        "mode": mode,
        "case_id": state["case_id"],
        "revision_id": revision_id,
        "payload": _payload(revision),
        "local_only": True,
        "executes": False,
        "requires_fresh_human_authorization": True,
    }

    if mode == AUTHOR_REVIEW:
        # The author needs to review the candidate and proposed action, not the
        # account/target identifiers or the local authoring-route reference.
        packet["intent"] = {"action": state["binding"]["action"]}
        return packet

    _require_preflight_eligibility(state, revision_id)
    packet["intent"] = _operator_intent(state["binding"])
    return packet
