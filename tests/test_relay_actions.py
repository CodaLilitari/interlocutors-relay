import hashlib
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from relay_actions import ActionStore, apply_action_request
from relay_core import ConflictError, StateError, Store, ValidationError


def actor(kind="profile"):
    return {"actor_id": "example-profile" if kind == "profile" else "example-page", "actor_kind": kind,
            "display_name": "Example interlocutor", "source_ref": "observed-actor-control"}


def target(surface="message_thread", audience="private", parent=None):
    return {"destination_id": "example-target", "surface": surface, "audience": audience, "reply_parent_id": parent}


def candidate(text="", data=None, attachments=None):
    return {"candidate_id": "choice-a", "text": text, "attachments": attachments or [],
            "action_data": data or {}, "source_ref": "author-candidate-reference", "response_ref": "author-response-reference"}


def mention(start=0, end=4, text="Alex", target_id="recipient-a", target_url="https://social.example/people/recipient-a"):
    return {"start": start, "end": end, "text": text, "target_id": target_id, "target_url": target_url}


def mention_edit(before=None, after=None):
    destination, selected = management_plan("edit_own_content")
    for snapshot in (destination["current_snapshot"], selected["action_data"]["original_snapshot"]):
        snapshot.update(text="Alex, hello.", media_refs=[], mentions=[] if before is None else before)
    selected["text"] = "Alex, hello."
    selected["action_data"]["mentions"] = [mention()] if after is None else after
    return destination, selected


def management_plan(action, acting=None, *, surface=None, deletion_mode="recoverable"):
    acting = acting or actor()
    surfaces = {"unfollow_page": "page", "unfollow_profile": "profile", "hide_post": "post",
                "hide_source": "page", "block": "profile"}
    surface = surface or surfaces.get(action, "comment")
    destination = target(surface, "public", "parent-post" if surface == "comment" else None)
    destination["target_url"] = "https://social.example/objects/exact-target"
    data = {"actor_id": acting["actor_id"], "actor_kind": acting["actor_kind"],
            "target_id": destination["destination_id"], "target_url": destination["target_url"],
            "reason": "Exact author-selected correction or boundary decision."}
    if action in {"edit_own_content", "delete_own_content"}:
        snapshot = {"owner_id": acting["actor_id"], "owner_kind": acting["actor_kind"],
                    "revision_ref": "observed-original-r1", "text": "Original exact text.",
                    "media_refs": ["original-media-one", "original-media-two"]}
        destination["current_snapshot"] = copy.deepcopy(snapshot)
        data["original_snapshot"] = copy.deepcopy(snapshot)
        if action == "edit_own_content":
            data["media_mode"] = "preserve"
        else:
            data["deletion_mode"] = deletion_mode
            if deletion_mode == "recoverable":
                data.update(recovery_description="Restore from observed trash for 30 days.", recovery_ref="observed-trash-help")
    else:
        states = {"unfollow_page": ("page_follow_state", "following"),
                  "unfollow_profile": ("profile_follow_state", "following"),
                  "hide_post": ("post_visibility", "visible"), "hide_source": ("source_visibility", "visible"),
                  "block": ("block_state", "not_blocked")}
        field, value = states[action]
        destination.update({field: value, "state_revision_ref": "observed-state-r1"})
        data["current_state_ref"] = "observed-state-r1"
        if action == "block":
            data.update(abuse_context_ref="scoped-private-abuse-context", block_scope=acting["actor_kind"] + "_block")
    return destination, candidate("Replacement exact text." if action == "edit_own_content" else "", data)


class RelayActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "shared.sqlite"
        self.store = ActionStore(self.db)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def create(self, action_id="a1", action="message", *, selected=None, acting=None, destination=None, **kwargs):
        return self.store.create_action(
            action_id, action, actor() if acting is None else acting, target() if destination is None else destination,
            candidate("Exact selected message.") if selected is None else selected,
            {"session_id": "example-session", "turn_id": "t1", "selection_ref": "actual-author-choice"},
            {"source_ref": "applicable-human-instruction"}, **kwargs)

    def evidence(self, action_id="a1"):
        action = self.store.inspect(action_id)
        evidence = {"actor_id": action["actor"]["actor_id"], "actor_kind": action["actor"]["actor_kind"],
                **{key: action["target"][key] for key in ("destination_id", "surface", "audience", "reply_parent_id")},
                "action": action["action"], "action_sha256": action["action_sha256"],
                "observation": "Synthetic observed result matches intended actor and target.",
                "source_ref": "synthetic-saved-result", "observed_at": "2026-10-22T20:00:00Z",
                "text": action["candidate"].get("text", "")}
        attachments = action["candidate"].get("attachments", [])
        if attachments:
            evidence["attachment_refs"] = [item["ref"] for item in attachments]
            evidence["attachment_alt_texts"] = [item.get("alt_text", item.get("core_media", {}).get("alt_text")) for item in attachments]
            evidence["attachment_observations"] = [
                {"source_ref": f"synthetic-rendered-attachment-{index}", "observation": "Observed the selected rendered image and its alt text."}
                for index in range(len(attachments))]
        return evidence

    def pending_evidence(self, action_id="a1"):
        return self.evidence(action_id) | {"submission_observed": True, "publication_state": "pending",
                                         "observation": "Exact selected comment visibly persists with a Pending label."}

    def create_publication(self, action_id="a1", **kwargs):
        return self.create(action_id, "comment", destination=target("post", "public", "parent-post"), **kwargs)

    def preflight(self, action_id="a1"):
        action = self.store.inspect(action_id)
        data, destination = action["candidate"]["action_data"], action["target"]
        evidence = self.evidence(action_id) | {
            "target_url": destination["target_url"], "control_available": True,
            "control_label": "Observed exact selected UI control", "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        if "current_snapshot" in destination:
            evidence["current_snapshot"] = copy.deepcopy(destination["current_snapshot"])
            if action["action"] == "delete_own_content":
                evidence["deletion_mode"] = data["deletion_mode"]
                if data["deletion_mode"] == "recoverable":
                    evidence["recovery_description"] = data["recovery_description"]
        else:
            for key in ("page_follow_state", "profile_follow_state", "post_visibility", "source_visibility", "block_state", "state_revision_ref"):
                if key in destination:
                    evidence[key] = destination[key]
            if action["action"] == "block":
                evidence["block_scope"] = data["block_scope"]
        return evidence

    def permission(self, action_id="a1", operation_id="op1"):
        action = self.store.inspect(action_id)
        data = action["candidate"]["action_data"]
        value = {"approved": True, "granted_by": "user", "actor_id": action["actor"]["actor_id"],
                 "actor_kind": action["actor"]["actor_kind"], "destination_id": action["target"]["destination_id"],
                 "target_url": action["target"]["target_url"], "action": action["action"],
                 "action_sha256": action["action_sha256"], "operation_id": operation_id,
                 "rationale": "Human confirms this exact operation after inspecting its current context.",
                 "source_ref": "specific-action-time-human-confirmation", "granted_at": datetime.now(timezone.utc).isoformat()}
        if action["action"] == "delete_own_content":
            value.update(deletion_mode="permanent", original_snapshot_sha256=action["original_snapshot_sha256"])
        else:
            value.update(block_scope=data["block_scope"], abuse_context_ref=data["abuse_context_ref"])
        return value

    def management_result(self, action_id="a1"):
        action = self.store.inspect(action_id)
        data, destination = action["candidate"]["action_data"], action["target"]
        evidence = self.evidence(action_id) | {"target_url": destination["target_url"], "action_taken": True,
                                               "observed_at": datetime.now(timezone.utc).isoformat()}
        if "current_snapshot" in destination:
            evidence["original_snapshot_sha256"] = action["original_snapshot_sha256"]
            if action["action"] == "edit_own_content":
                media = list(data["original_snapshot"]["media_refs"]) if data["media_mode"] == "preserve" else [
                    item["ref"] for item in action["candidate"].get("attachments", [])]
                if data.get("platform_media"):
                    media.append(data["platform_media"]["ref"])
                evidence.update(content_state="edited", media_mode=data["media_mode"], result_snapshot={
                    **data["original_snapshot"], "revision_ref": "observed-edited-r2",
                    "text": action["candidate"]["text"], "media_refs": media})
            else:
                recoverable = data["deletion_mode"] == "recoverable"
                evidence.update(deletion_mode=data["deletion_mode"], deletion_observed=True,
                                deletion_state="trashed" if recoverable else "permanently_deleted",
                                restore_available=recoverable, deletion_confirmation_ref="observed-deletion-confirmation")
                if recoverable:
                    evidence["recovery_description"] = data["recovery_description"]
        else:
            states = {"unfollow_page": ("page_follow_state", "not_following"),
                      "unfollow_profile": ("profile_follow_state", "not_following"),
                      "hide_post": ("post_visibility", "hidden"), "hide_source": ("source_visibility", "hidden"),
                      "block": ("block_state", "blocked")}
            field, value = states[action["action"]]
            evidence.update({field: value, "prior_state_revision_ref": destination["state_revision_ref"],
                             "result_state_ref": "observed-result-r2"})
            if action["action"] == "block":
                evidence["block_scope"] = data["block_scope"]
        return evidence

    def core_case(self, core, case="core-case"):
        core.create_case(case, {"author_route": "example-thread", "actor_id": "example-page", "actor_kind": "page",
                               "destination_id": "example-target", "action": "comment", "context_ref": "actual-source"})
        core.add_revision(case, "r1", "Core comment", "author-source")
        core.endorse(case, "r1", "author-endorsement", "Core comment")
        core.stage(case, "r1")
        core.authorize(case, "r1", "human-authority", (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())

    def test_textless_reaction_share_and_native_gif_keep_actual_action_payload(self):
        image_path = Path(self.temp.name) / "meme.png"
        image_path.write_bytes(b"synthetic-image-fixture")
        choices = [
            ("react", candidate(data={"reaction": "LOVE"}), target("post", "public", "parent-post")),
            ("share", candidate(data={"source_ref": "original-post-reference"}), target("profile_timeline", "friends")),
            ("message", candidate(data={"platform_media": {"kind": "gif", "ref": "actual-native-gif", "preview_ref": "observed-preview"}}), target()),
            ("reply", candidate(attachments=[{"ref": str(image_path), "alt_text": "Exact selected alt text"}]),
             target("comment", "public", "parent-comment")),
        ]
        for index, (action, selected, destination) in enumerate(choices):
            with self.subTest(action=action):
                action_id = f"choice-{index}"
                document = self.create(action_id, action, selected=selected, destination=destination)
                self.assertEqual(document["candidate"], selected)
                self.assertEqual(document["candidate"]["text"], "")
                self.assertNotIn("core_media", str(document))
                self.assertEqual(self.store.action_packet(action_id)["content_state"], "selected_action")

    def test_message_is_bound_to_private_conversation_not_timeline(self):
        for destination in (target("profile_timeline", "private"), target("message_thread", "public")):
            with self.assertRaises(ValidationError):
                self.create(destination=destination)
        document = self.create()
        self.assertEqual(document["target"]["surface"], "message_thread")
        self.assertEqual(document["target"]["audience"], "private")
        with self.assertRaises(ValidationError):
            self.create("empty", selected=candidate())

    def test_verified_text_is_actual_exact_readback_not_only_intent_digest(self):
        self.create(selected=candidate("Exact selected message.\nSecond line."))
        self.store.begin_action("a1", "op1")
        evidence = self.evidence()
        missing = dict(evidence)
        del missing["text"]
        for wrong in (missing, evidence | {"text": "Different rendered message."},
                      evidence | {"text": evidence["text"] + "\n"}):
            with self.assertRaises(ValidationError):
                self.store.record_outcome("op1", "verified", wrong)
        self.store.record_outcome("op1", "verified", evidence)

    def test_local_upload_hash_is_captured_and_changed_file_blocks_new_claim(self):
        path = Path(self.temp.name) / "selected.png"
        original = b"original-synthetic-image-bytes"
        path.write_bytes(original)
        selected = candidate(attachments=[{"ref": str(path), "sha256": hashlib.sha256(original).hexdigest(),
                                           "byte_size": len(original), "alt_text": "Selected image description."}])
        created = self.create(selected=selected)
        self.assertEqual(created["candidate"], selected)
        self.assertEqual(created["local_attachments"], [{"ref": str(path), "path": str(path),
                         "sha256": hashlib.sha256(original).hexdigest(), "byte_size": len(original)}])
        path.write_bytes(b"different-synthetic-image-bytes")
        with self.assertRaises((StateError, ValidationError)):
            self.store.begin_action("a1", "op1")
        self.assertEqual(self.store.inspect("a1")["operations"], [])
        path.write_bytes(original)
        self.store.begin_action("a1", "op1")
        path.unlink()
        self.assertFalse(self.store.begin_action("a1", "op1")["newly_claimed"])
        self.assertEqual(self.store.recover(), ["op1"])
        # A later rendered result can still be reconciled after local cleanup.
        self.store.reconcile("op1", "verified", self.evidence(), "observed-saved-attachment")

    def test_local_upload_requires_observed_refs_alt_text_and_rendered_evidence(self):
        path = Path(self.temp.name) / "selected.png"
        path.write_bytes(b"synthetic-image-bytes")
        self.create(selected=candidate("Caption", attachments=[{"ref": "chosen-author-file", "local_path": str(path),
                                                               "alt_text": "Exact selected alt text."}]))
        self.store.begin_action("a1", "op1")
        evidence = self.evidence()
        wrong_values = ({"attachment_refs": ["other-author-file"]},
                        {"attachment_alt_texts": ["Different alt text."]},
                        {"attachment_observations": []},
                        {"attachment_observations": [{"observation": "No actual source reference"}]})
        for wrong in wrong_values:
            with self.assertRaises(ValidationError):
                self.store.record_outcome("op1", "verified", evidence | wrong)
        self.store.record_outcome("op1", "verified", evidence)

    def test_ref_only_upload_is_measured_and_supplied_wrong_hash_is_rejected(self):
        path = Path(self.temp.name) / "selected.png"
        path.write_bytes(b"synthetic-image-bytes")
        created = self.create(selected=candidate(attachments=[{"ref": str(path)}]))
        self.assertEqual(created["local_attachments"][0]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        path.write_bytes(b"changed-synthetic-image-bytes")
        with self.assertRaises(StateError):
            self.store.begin_action("a1", "op1")
        with self.assertRaises(ValidationError):
            self.create("wrong-descriptor", selected=candidate(attachments=[{"ref": str(path), "core_media": {"sha256": "0" * 64}}]))
        with self.assertRaises(ValidationError):
            self.create("unresolved", selected=candidate(attachments=[{"ref": "unresolved-attachment"}]))

    def test_verified_nontext_result_names_the_observed_reaction_share_or_gif(self):
        choices = [
            ("react", candidate(data={"reaction": "LOVE"}), target("post", "public", "parent-post"),
             {"reaction": "LOVE"}),
            ("share", candidate(data={"source_ref": "chosen-source"}), target("profile_timeline", "friends"),
             {"shared_source_ref": "chosen-source"}),
            ("message", candidate(data={"platform_media": {"kind": "gif", "ref": "chosen-gif"}}), target(),
             {"platform_media_ref": "chosen-gif", "platform_media_kind": "gif"}),
        ]
        for index, (action, selected, destination, observed) in enumerate(choices):
            action_id, operation_id = f"nontext-{index}", f"observed-{index}"
            self.create(action_id, action, selected=selected, destination=destination)
            self.store.begin_action(action_id, operation_id)
            evidence = self.evidence(action_id)
            with self.assertRaises(ValidationError):
                self.store.record_outcome(operation_id, "verified", evidence)
            for field in observed:
                with self.subTest(action=action, field=field), self.assertRaises(ValidationError):
                    self.store.record_outcome(operation_id, "verified", evidence | observed | {field: "different-observed-item"})
            self.store.record_outcome(operation_id, "verified", evidence | observed)
            self.assertEqual(self.store.inspect(action_id)["execution_state"], "verified")

    def test_distinct_actors_and_bound_intent_survive_external_object_mutation(self):
        acting = actor()
        selected = candidate("Exact words")
        self.create(acting=acting, selected=selected, account_policy={"allowed_actor_ids": ["example-profile", "example-page"]})
        acting["actor_id"] = "example-page"
        selected["text"] = "Changed words"
        record = self.store.inspect("a1")
        self.assertEqual(record["actor"]["actor_id"], "example-profile")
        self.assertEqual(record["candidate"]["text"], "Exact words")
        self.store.begin_action("a1", "op1")
        wrong = self.evidence() | {"actor_id": "example-page", "actor_kind": "page"}
        with self.assertRaises(ValidationError):
            self.store.record_outcome("op1", "verified", wrong)
        self.assertEqual(self.store.inspect("a1")["execution_state"], "in_flight")
        self.store.record_outcome("op1", "verified", self.evidence())
        with self.assertRaises(ConflictError):
            self.create()
        # Extra target metadata cannot overwrite the actor used by policy.
        with self.assertRaises(ValidationError):
            self.create("bad-policy", destination=target() | {"actor_id": "example-page"},
                        account_policy={"allowed_actor_ids": ["example-page"]})

    def test_friend_request_requires_rationale_and_specific_user_permission(self):
        permission = {"approved": True, "granted_by": "user", "actor_id": "example-profile",
                      "destination_id": "example-target", "rationale": "The intended correspondent invited connection.",
                      "source_ref": "specific-user-permission"}
        invalid = [None, {}, permission | {"approved": False}, permission | {"granted_by": "interlocutor"},
                   permission | {"actor_id": "other-profile"}, permission | {"destination_id": "other-target"},
                   permission | {"rationale": " "}, permission | {"source_ref": ""}]
        for value in invalid:
            with self.subTest(permission=value), self.assertRaises(ValidationError):
                self.create(action="friend_request", selected=candidate(), destination=target("profile", "recipient"),
                            friend_request_permission=value)
        record = self.create(action="friend_request", selected=candidate(), destination=target("profile", "recipient"),
                             friend_request_permission=permission)
        self.assertEqual(record["friend_request_permission"], permission)
        self.assertTrue(self.store.begin_action("a1", "op-friend")["newly_claimed"])
        with self.assertRaises(ValidationError):
            self.create("page-friend", "friend_request", selected=candidate(), acting=actor("page"),
                        destination=target("profile", "recipient"), friend_request_permission=permission)

    def test_page_like_requires_exact_page_and_unliked_state_without_other_actions(self):
        page_url = "https://social.example/pages/science"
        selected = candidate(data={"page_url": page_url})
        destination = target("page", "public") | {"page_url": page_url, "page_like_state": "not_liked"}
        invalid = [destination | {"surface": "profile"}, destination | {"surface": "post"},
                   destination | {"reply_parent_id": "content-item"}, destination | {"page_url": None},
                   destination | {"page_url": "science"}, destination | {"page_url": "http://social.example/page"},
                   destination | {"page_url": "https://social.example/"},
                   destination | {"page_url": "https://user:password@social.example/page"},
                   destination | {"page_url": "https://[broken/page"},
                   destination | {"page_like_state": "liked"}, destination | {"page_like_state": "unknown"}]
        for wrong in invalid:
            with self.subTest(target=wrong), self.assertRaises(ValidationError):
                self.create(action="like_page", selected=selected, destination=wrong)
        for wrong in (candidate("Public caption", {"page_url": page_url}),
                      candidate(data={"page_url": "https://social.example/pages/other"}),
                      candidate(data={"page_url": page_url, "reaction": "LIKE"}),
                      candidate(data={"page_url": page_url, "follow": True}),
                      candidate(data={"page_url": page_url, "platform_media": {"kind": "gif", "ref": "asset"}})):
            with self.subTest(candidate=wrong), self.assertRaises(ValidationError):
                self.create(action="like_page", selected=wrong, destination=destination)
        for kind in ("page", "profile"):
            saved = self.create(kind, "like_page", acting=actor(kind), selected=selected, destination=destination)
            self.assertEqual(saved["actor"]["actor_kind"], kind)
            self.assertEqual(saved["candidate"], selected)

    def test_page_like_result_cannot_substitute_follow_reaction_or_another_page(self):
        page_url = "https://social.example/pages/science"
        self.create(action="like_page", selected=candidate(data={"page_url": page_url}),
                    destination=target("page", "public") | {"page_url": page_url, "page_like_state": "not_liked"})
        self.store.begin_action("a1", "op1")
        evidence = self.evidence() | {"page_url": page_url, "page_like_state": "liked", "action_taken": True}
        for wrong in (self.evidence() | {"reaction": "LIKE"},
                      evidence | {"page_like_state": "following"}, evidence | {"action_taken": False},
                      evidence | {"page_url": "https://social.example/pages/other"},
                      evidence | {"actor_id": "other-actor"}):
            with self.subTest(evidence=wrong), self.assertRaises(ValidationError):
                self.store.record_outcome("op1", "verified", wrong)
        with self.assertRaises(ValidationError):
            self.store.record_outcome("op1", "submitted_pending", evidence | {"submission_observed": True, "publication_state": "pending"})
        self.store.record_outcome("op1", "verified", evidence)
        self.assertFalse(self.store.begin_action("a1", "op1")["newly_claimed"])
        with self.assertRaises(StateError):
            self.store.begin_action("a1", "op2")

    def test_page_follow_requires_unfollowed_page_and_separate_selected_action(self):
        page_url = "https://social.example/pages/science"
        selected = candidate(data={"page_url": page_url})
        destination = target("page", "public") | {"page_url": page_url, "page_follow_state": "not_following"}
        for wrong in (destination | {"surface": "profile"}, destination | {"surface": "feed_item"},
                      destination | {"reply_parent_id": "content-item"},
                      destination | {"page_follow_state": "following"}, destination | {"page_follow_state": "unknown"},
                      target("page", "public") | {"page_url": page_url, "page_like_state": "not_liked"},
                      destination | {"page_url": "https://social.example/pages/other"}):
            with self.subTest(target=wrong), self.assertRaises(ValidationError):
                self.create(action="follow_page", selected=selected, destination=wrong)
        for wrong in (candidate("Caption", {"page_url": page_url}),
                      candidate(data={"page_url": page_url, "reaction": "LIKE"}),
                      candidate(data={"page_url": page_url, "like": True})):
            with self.subTest(candidate=wrong), self.assertRaises(ValidationError):
                self.create(action="follow_page", selected=wrong, destination=destination)
        for kind in ("page", "profile"):
            saved = self.create(kind, "follow_page", acting=actor(kind), selected=selected, destination=destination)
            self.assertEqual(saved["action"], "follow_page")
            self.assertEqual(saved["actor"]["actor_kind"], kind)

    def test_page_follow_verifies_following_not_liked_and_never_repeats_claim(self):
        page_url = "https://social.example/pages/science"
        self.create(action="follow_page", selected=candidate(data={"page_url": page_url}),
                    destination=target("page", "public") | {"page_url": page_url, "page_follow_state": "not_following"})
        self.store.begin_action("a1", "op1")
        evidence = self.evidence() | {"page_url": page_url, "page_follow_state": "following", "action_taken": True}
        for wrong in (self.evidence() | {"page_url": page_url, "page_like_state": "liked", "action_taken": True},
                      evidence | {"page_follow_state": "not_following"}, evidence | {"action_taken": False},
                      evidence | {"action": "like_page"}, evidence | {"actor_id": "other-actor"},
                      evidence | {"page_url": "https://social.example/pages/other"}):
            with self.subTest(evidence=wrong), self.assertRaises(ValidationError):
                self.store.record_outcome("op1", "verified", wrong)
        self.store.record_outcome("op1", "verified", evidence)
        self.assertFalse(self.store.begin_action("a1", "op1")["newly_claimed"])
        with self.assertRaises(StateError):
            self.store.begin_action("a1", "op2")

    def test_block_can_be_described_but_cannot_dispatch_as_live_test(self):
        destination, selected = management_plan("block")
        self.create(action="block", selected=selected, destination=destination, scope="live_test")
        packet = self.store.action_packet("a1")
        self.assertEqual(packet["host_step"], "excluded_from_live_testing")
        self.assertFalse(packet["executes"])
        with self.assertRaisesRegex(StateError, "excluded from live testing"):
            self.store.begin_action("a1", "block-test")
        self.assertEqual(self.store.inspect("a1")["operations"], [])
        destination, selected = management_plan("block", surface="page")
        self.create("page-block", "block", selected=selected, destination=destination, scope="live_test")
        with self.assertRaisesRegex(StateError, "excluded from live testing"):
            self.store.begin_action("page-block", "page-block-test")
        self.create("other")
        self.assertTrue(self.store.begin_action("other", "message-test")["newly_claimed"])

    def test_interrupted_claim_preserves_uncertainty_and_disallows_blind_resend(self):
        self.create()
        first = self.store.begin_action("a1", "op1")
        self.assertTrue(first["newly_claimed"])
        self.store.close()
        self.store = ActionStore(self.db)
        duplicate = self.store.begin_action("a1", "op1")
        self.assertFalse(duplicate["newly_claimed"])
        self.assertEqual(duplicate["host_step"], "inspect_existing_operation")
        self.assertEqual(self.store.recover(), ["op1"])
        self.assertEqual(self.store.recover(), [])
        with self.assertRaises(StateError):
            self.store.begin_action("a1", "op2")
        self.store.reconcile("op1", "verified", self.evidence(), "actual-history-check")
        record = self.store.inspect("a1")
        self.assertEqual(record["execution_state"], "verified")
        self.assertEqual([item["outcome"] for item in record["operations"][0]["observations"]], ["uncertain", "verified"])
        with self.assertRaises(StateError):
            self.store.begin_action("a1", "op3")

    def test_positive_no_submit_allows_new_claim_with_old_evidence_preserved(self):
        self.create()
        self.store.begin_action("a1", "op1")
        observation = {"observation": "Synthetic composer remained unsent.", "source_ref": "actual-composer-check"}
        with self.assertRaises(ValidationError):
            self.store.record_outcome("op1", "failed_before_submit", observation)
        self.store.record_outcome("op1", "uncertain", observation)
        self.store.reconcile("op1", "failed_before_submit", observation | {"positive_no_submit": True}, "actual-history-check")
        self.assertFalse(self.store.begin_action("a1", "op1")["newly_claimed"])
        self.assertTrue(self.store.begin_action("a1", "op2")["newly_claimed"])
        self.assertEqual(len(self.store.inspect("a1")["operations"]), 2)

    def test_core_and_action_dispatch_share_guard_in_both_directions(self):
        core = Store(self.db)
        try:
            self.core_case(core)
            old_case = core.inspect("core-case")
            self.create()
            self.store.begin_action("a1", "action-first")
            with self.assertRaises(StateError):
                core.begin_delivery("core-case", "r1", "core-op")
            self.assertEqual(core.recover(), [])
            self.store.record_outcome("action-first", "uncertain", {"observation": "Unknown action result", "source_ref": "timeout"})
            with self.assertRaises(StateError):
                core.begin_delivery("core-case", "r1", "core-op")
            self.store.reconcile("action-first", "verified", self.evidence(), "observed-persisted-result")
            self.assertEqual(core.inspect("core-case"), old_case)
            self.assertTrue(core.begin_delivery("core-case", "r1", "core-op")["newly_claimed"])
            self.create("a2")
            with self.assertRaises(StateError):
                self.store.begin_action("a2", "action-next")
            self.assertEqual(self.store.recover(), [])
            core.record_outcome("core-op", "failed_before_submit", {"observation": "No submission occurred", "positive_no_submit": True})
            self.assertTrue(self.store.begin_action("a2", "action-next")["newly_claimed"])
        finally:
            core.close()

    def test_separate_action_connections_cannot_both_claim_and_wrong_target_cannot_verify(self):
        self.create()
        self.create("a2")
        other = ActionStore(self.db)
        try:
            self.store.begin_action("a1", "op1")
            self.assertFalse(other.begin_action("a1", "op1")["newly_claimed"])
            with self.assertRaises(StateError):
                other.begin_action("a2", "op2")
            with self.assertRaises(ValidationError):
                other.record_outcome("op1", "verified", self.evidence() | {"destination_id": "other-target"})
            with self.assertRaises(ValidationError):
                other.record_outcome("op1", "verified", self.evidence() | {"action_sha256": "0" * 64})
            other.record_outcome("op1", "verified", self.evidence())
            self.assertTrue(self.store.begin_action("a2", "op2")["newly_claimed"])
        finally:
            other.close()

    def test_action_dispatcher_excludes_inherited_core_mutation_methods(self):
        self.create()
        packet = apply_action_request(self.store, {"method": "action_packet", "arguments": {"action_id": "a1"}})
        self.assertFalse(packet["executes"])
        for method in ("create_case", "authorize", "begin_delivery", "close", "_db", ["inspect"]):
            with self.subTest(method=method), self.assertRaises(ValidationError):
                apply_action_request(self.store, {"method": method})

    def test_pending_survives_restart_and_releases_guard_without_allowing_repeat(self):
        self.create_publication()
        self.store.begin_action("a1", "op1")
        self.store.close()
        self.store = ActionStore(self.db)
        self.assertEqual(self.store.recover(), ["op1"])
        self.store.reconcile("op1", "submitted_pending", self.pending_evidence(), "reopened-exact-comment")
        self.store.close()
        self.store = ActionStore(self.db)
        self.assertEqual(self.store.recover(), [])
        packet = self.store.action_packet("a1")
        self.assertEqual(packet["execution_state"], "submitted_pending")
        self.assertEqual(packet["host_step"], "observe_pending_publication")
        self.assertFalse(packet["executes"])
        self.assertFalse(self.store.begin_action("a1", "op1")["newly_claimed"])
        with self.assertRaises(StateError):
            self.store.begin_action("a1", "repeat")
        self.create("unrelated-message")
        self.assertTrue(self.store.begin_action("unrelated-message", "message-op")["newly_claimed"])
        observations = packet["record"]["operations"][0]["observations"]
        self.assertEqual([item["outcome"] for item in observations], ["uncertain", "submitted_pending"])

    def test_pending_requires_positive_exact_evidence_and_malformed_evidence_keeps_guard(self):
        self.create_publication(selected=candidate("Exact paragraph.\nSecond line."))
        self.store.begin_action("a1", "op1")
        self.create("other")
        evidence = self.pending_evidence()
        for field in ("actor_id", "actor_kind", "destination_id", "surface", "audience", "reply_parent_id",
                      "action", "action_sha256", "text", "observed_at", "source_ref", "observation",
                      "submission_observed", "publication_state"):
            missing = dict(evidence)
            del missing[field]
            with self.subTest(missing=field), self.assertRaises(ValidationError):
                self.store.record_outcome("op1", "submitted_pending", missing)
        for wrong in ({"actor_id": "other-actor"}, {"destination_id": "other-post"},
                      {"reply_parent_id": "other-parent"}, {"action_sha256": "0" * 64},
                      {"text": evidence["text"] + "\n"}, {"publication_state": "published"},
                      {"submission_observed": False}, {"submission_observed": 1},
                      {"positive_no_submit": True}, {"observed_at": "2026-10-23T12:00:00"}):
            with self.subTest(wrong=wrong), self.assertRaises(ValidationError):
                self.store.record_outcome("op1", "submitted_pending", evidence | wrong)
        self.assertEqual(self.store.inspect("a1")["operations"][0]["observations"], [])
        with self.assertRaises(StateError):
            self.store.begin_action("other", "op2")
        self.store.record_outcome("op1", "uncertain", {"source_ref": "interrupted-view", "observation": "Result not yet inspected."})
        with self.assertRaises(ValidationError):
            self.store.reconcile("op1", "submitted_pending", evidence | {"text": "wrong"}, "new-view")
        with self.assertRaises(StateError):
            self.store.begin_action("other", "op2")
        self.assertEqual(self.store.inspect("a1")["execution_state"], "uncertain")
        self.store.reconcile("op1", "submitted_pending", evidence, "new-view")
        self.assertTrue(self.store.begin_action("other", "op2")["newly_claimed"])

    def test_pending_media_requires_actual_selected_uploads_and_native_asset(self):
        path = Path(self.temp.name) / "selected.png"
        path.write_bytes(b"synthetic-selected-upload")
        self.create_publication(selected=candidate("Caption", attachments=[{"ref": str(path), "alt_text": "Exact alt text."}],
                                                  data={"platform_media": {"kind": "gif", "ref": "native-chosen-gif"}}))
        self.store.begin_action("a1", "op1")
        evidence = self.pending_evidence() | {"platform_media_ref": "native-chosen-gif", "platform_media_kind": "gif"}
        for wrong in ({"attachment_refs": []}, {"attachment_alt_texts": ["other text"]},
                      {"attachment_observations": []}, {"platform_media_ref": "other-gif"},
                      {"platform_media_kind": "image"}):
            with self.subTest(wrong=wrong), self.assertRaises(ValidationError):
                self.store.record_outcome("op1", "submitted_pending", evidence | wrong)
        self.store.record_outcome("op1", "submitted_pending", evidence)

    def test_pending_applies_only_to_publications_and_share_source_is_bound(self):
        publications = ("post", "original_post", "comment", "reply", "share", "share_with_commentary")
        for action in publications:
            with self.subTest(action=action):
                action_id, operation_id = f"pending-{action}", f"op-{action}"
                data = {"source_ref": "selected-shared-post"} if action.startswith("share") else {}
                destination = target("post", "public", "parent-post") if action in {"comment", "reply"} else target("profile_timeline", "public")
                selected = candidate("" if action == "share" else f"Content for {action}", data=data)
                selected["candidate_id"] = action_id
                self.create(action_id, action, selected=selected, destination=destination)
                self.store.begin_action(action_id, operation_id)
                evidence = self.pending_evidence(action_id)
                if action.startswith("share"):
                    with self.assertRaises(ValidationError):
                        self.store.record_outcome(operation_id, "submitted_pending", evidence)
                    evidence["shared_source_ref"] = "selected-shared-post"
                self.store.record_outcome(operation_id, "submitted_pending", evidence)
        nonpublications = [("message", candidate("Exact message"), target(), {}),
                           ("react", candidate(data={"reaction": "LOVE"}), target("post", "public"), {}),
                           ("friend_request", candidate(), target("profile", "recipient"),
                            {"friend_request_permission": {"approved": True, "granted_by": "user",
                             "actor_id": "example-profile", "destination_id": "example-target",
                             "rationale": "Specific invited test", "source_ref": "specific-user-permission"}})]
        for action, selected, destination, kwargs in nonpublications:
            with self.subTest(action=action):
                action_id, operation_id = f"not-publication-{action}", f"op-{action}"
                self.create(action_id, action, selected=selected, destination=destination, **kwargs)
                self.store.begin_action(action_id, operation_id)
                for outcome, extra in (("submitted_pending", {}),
                                       ("not_published", {"publication_state": "rejected", "terminal_observed": True})):
                    with self.assertRaises(ValidationError):
                        self.store.record_outcome(operation_id, outcome, self.pending_evidence(action_id) | extra)
                self.store.record_outcome(operation_id, "failed_before_submit",
                                          {"source_ref": "synthetic-no-dispatch", "observation": "No UI was operated.", "positive_no_submit": True})

    def test_pending_observations_and_resolution_preserve_another_actions_guard(self):
        self.create_publication()
        self.store.begin_action("a1", "op1")
        self.store.record_outcome("op1", "submitted_pending", self.pending_evidence())
        core = Store(self.db)
        try:
            self.core_case(core)
            self.assertTrue(core.begin_delivery("core-case", "r1", "core-op")["newly_claimed"])
            other = ActionStore(self.db)
            try:
                other.reconcile("op1", "submitted_pending", self.pending_evidence() | {"source_ref": "second-pending-view"}, "second-check")
                for invalid in ("failed_before_submit", "uncertain"):
                    with self.subTest(outcome=invalid), self.assertRaises(StateError):
                        other.reconcile("op1", invalid, self.pending_evidence() | {"positive_no_submit": True}, "invalid-change")
                for contradictory in (self.pending_evidence(), self.evidence(),
                                      self.evidence() | {"publication_state": "rejected"},
                                      self.evidence() | {"submission_observed": False},
                                      self.evidence() | {"publication_state": "published"}):
                    with self.assertRaises(ValidationError):
                        other.reconcile("op1", "verified", contradictory, "not-published-proof")
                other.reconcile("op1", "verified", self.evidence() | {"publication_state": "published", "submission_observed": True}, "visible-published-result")
                self.create("other")
                with self.assertRaises(StateError):
                    other.begin_action("other", "other-op")
                guard = other._db.execute("SELECT operation_id FROM delivery_guard WHERE singleton=1").fetchone()
                self.assertEqual(guard["operation_id"], "core-op")
                outcomes = [item["outcome"] for item in other.inspect("a1")["operations"][0]["observations"]]
                self.assertEqual(outcomes, ["submitted_pending", "submitted_pending", "verified"])
            finally:
                other.close()
        finally:
            core.close()

    def test_visible_rejection_is_terminal_not_a_failed_submission_or_retry(self):
        self.create_publication()
        self.store.begin_action("a1", "op1")
        self.store.record_outcome("op1", "submitted_pending", self.pending_evidence())
        rejected = self.pending_evidence() | {"publication_state": "rejected", "terminal_observed": True,
                                             "source_ref": "visible-rejection", "observation": "Exact submitted comment is explicitly rejected."}
        for wrong in ({"publication_state": "missing"}, {"publication_state": "pending"},
                      {"publication_state": ["rejected"]},
                      {"terminal_observed": False}, {"submission_observed": False}, {"text": "different"}):
            with self.subTest(wrong=wrong), self.assertRaises(ValidationError):
                self.store.reconcile("op1", "not_published", rejected | wrong, "rejection-check")
        operation = self.store.reconcile("op1", "not_published", rejected, "rejection-check")
        self.assertEqual(operation["state"], "not_published")
        self.assertEqual(self.store.action_packet("a1")["host_step"], "return_not_published_result")
        with self.assertRaises(StateError):
            self.store.begin_action("a1", "op2")
        self.assertFalse(self.store.begin_action("a1", "op1")["newly_claimed"])
        with self.assertRaises(StateError):
            self.store.reconcile("op1", "failed_before_submit", rejected | {"positive_no_submit": True}, "contradictory-retry")
        self.create_publication("same-candidate-new-id")
        with self.assertRaises(StateError):
            self.store.begin_action("same-candidate-new-id", "cloned-op")
        new_choice = candidate("Exact selected message.") | {"candidate_id": "new-author-choice", "response_ref": "new-author-response"}
        self.create_publication("new-selection", selected=new_choice)
        self.assertTrue(self.store.begin_action("new-selection", "new-selection-op")["newly_claimed"])

    def test_pending_clones_cannot_escape_by_changing_provenance_but_distinct_actions_work(self):
        self.create_publication()
        self.store.begin_action("a1", "op1")
        self.store.record_outcome("op1", "submitted_pending", self.pending_evidence())
        clone = candidate("Exact selected message.") | {"candidate_id": "renamed", "source_ref": "different-source",
                                                         "response_ref": "different-response"}
        self.create_publication("cloned-id", selected=clone, acting=actor() | {"observed_at": "2026-10-23T20:00:00Z"})
        with self.assertRaisesRegex(StateError, "duplicates submitted operation"):
            self.store.begin_action("cloned-id", "cloned-op")
        for action_id, selected, acting, destination in (
            ("other-content", clone | {"text": "A different intended comment."}, actor(), target("post", "public", "parent-post")),
            ("other-actor", clone, actor("page"), target("post", "public", "parent-post")),
            ("other-target", clone, actor(), target("post", "public", "different-parent") | {"destination_id": "other-post"}),
        ):
            with self.subTest(action_id=action_id):
                self.create(action_id, "comment", selected=selected, acting=acting, destination=destination)
                self.assertTrue(self.store.begin_action(action_id, action_id + "-op")["newly_claimed"])
                self.store.record_outcome(action_id + "-op", "failed_before_submit",
                                          {"source_ref": "synthetic-only", "observation": "No real dispatch occurred.", "positive_no_submit": True})

    def test_pending_image_clone_matches_bytes_not_file_name_or_candidate_id(self):
        first = Path(self.temp.name) / "first.png"
        second = Path(self.temp.name) / "renamed.png"
        first.write_bytes(b"same-selected-media")
        second.write_bytes(first.read_bytes())
        self.create_publication(selected=candidate(attachments=[{"ref": str(first)}]))
        self.store.begin_action("a1", "op1")
        self.store.record_outcome("op1", "submitted_pending", self.pending_evidence())
        selected = candidate(attachments=[{"ref": str(second)}]) | {"candidate_id": "fresh-id", "source_ref": "fresh-source"}
        self.create_publication("renamed-clone", selected=selected)
        with self.assertRaises(StateError):
            self.store.begin_action("renamed-clone", "repeat-op")

    def test_pending_aliases_cannot_repeat_but_outcome_surface_stays_exact(self):
        aliases = [
            ("comment", "post", "comment", "feed_item", "parent-post", "profile"),
            ("comment", "post", "reply", "comment", "parent-comment", "profile"),
            ("post", "profile_timeline", "original_post", "profile", None, "profile"),
            ("original_post", "page_timeline", "post", "page", None, "page"),
            ("share", "profile_timeline", "share_with_commentary", "profile", None, "profile"),
            ("share_with_commentary", "page", "share", "page_timeline", None, "page"),
        ]
        for index, (original_action, original_surface, clone_action, clone_surface, parent, kind) in enumerate(aliases):
            with self.subTest(original=original_action, clone=clone_action, surface=clone_surface):
                original_id, clone_id = f"original-{index}", f"clone-{index}"
                destination = target(original_surface, "public", parent) | {"destination_id": f"stable-target-{index}"}
                data = {"source_ref": "exact-shared-source"} if original_action.startswith("share") else {}
                selected = candidate("Exact selected content.", data=data) | {"candidate_id": original_id}
                self.create(original_id, original_action, acting=actor(kind), destination=destination, selected=selected)
                # A clone made before dispatch must also be rejected at claim time.
                cloned = selected | {"candidate_id": clone_id, "source_ref": "fresh-source", "response_ref": "fresh-response"}
                self.create(clone_id, clone_action, acting=actor(kind), destination=destination | {"surface": clone_surface}, selected=cloned)
                operation_id = original_id + "-op"
                self.store.begin_action(original_id, operation_id)
                evidence = self.pending_evidence(original_id)
                if data:
                    evidence["shared_source_ref"] = data["source_ref"]
                with self.assertRaises(ValidationError):
                    self.store.record_outcome(operation_id, "submitted_pending", evidence | {"surface": clone_surface})
                self.store.record_outcome(operation_id, "submitted_pending", evidence)
                with self.assertRaisesRegex(StateError, "duplicates submitted operation"):
                    self.store.begin_action(clone_id, clone_id + "-op")
                self.assertEqual(self.store.inspect(clone_id)["operations"], [])
                self.assertEqual(self.store.inspect(original_id)["target"]["surface"], original_surface)

    def test_resolved_original_candidate_stays_blocked_through_surface_alias(self):
        self.create_publication()
        self.store.begin_action("a1", "op1")
        self.store.record_outcome("op1", "submitted_pending", self.pending_evidence())
        self.store.reconcile("op1", "verified", self.evidence() | {"publication_state": "published", "submission_observed": True},
                             "observed-publication")
        self.create("alias", "reply", destination=target("feed_item", "public", "parent-post"))
        with self.assertRaisesRegex(StateError, "duplicates submitted operation"):
            self.store.begin_action("alias", "alias-op")

    def test_pending_share_does_not_block_a_distinct_selected_source_or_commentary(self):
        selected = candidate("Exact commentary.", data={"source_ref": "original-shared-source"})
        self.create("original", "share_with_commentary", destination=target("profile_timeline", "public"), selected=selected)
        self.store.begin_action("original", "original-op")
        self.store.record_outcome("original-op", "submitted_pending",
                                  self.pending_evidence("original") | {"shared_source_ref": "original-shared-source"})
        for action_id, text, source in (("new-source", "Exact commentary.", "different-shared-source"),
                                       ("new-commentary", "Different selected commentary.", "original-shared-source")):
            with self.subTest(action_id=action_id):
                choice = candidate(text, data={"source_ref": source}) | {"candidate_id": action_id, "response_ref": "new-author-response"}
                self.create(action_id, "share_with_commentary", destination=target("profile", "public"), selected=choice)
                self.assertTrue(self.store.begin_action(action_id, action_id + "-op")["newly_claimed"])
                self.store.record_outcome(action_id + "-op", "failed_before_submit",
                                          {"source_ref": "synthetic-only", "observation": "No dispatch occurred.", "positive_no_submit": True})


    def test_edit_binds_selected_owner_object_and_exact_original_revision(self):
        destination, selected = management_plan("edit_own_content")
        mutations = [
            ("owner", lambda d, s: d["current_snapshot"].update(owner_id="someone-else")),
            ("selected-owner", lambda d, s: (d["current_snapshot"].update(owner_id="someone-else"),
                                              s["action_data"]["original_snapshot"].update(owner_id="someone-else"))),
            ("text", lambda d, s: d["current_snapshot"].update(text="New external edit.")),
            ("media-order", lambda d, s: d["current_snapshot"]["media_refs"].reverse()),
            ("revision", lambda d, s: d["current_snapshot"].update(revision_ref="r2")),
            ("target", lambda d, s: s["action_data"].update(target_id="another-target")),
            ("actor", lambda d, s: s["action_data"].update(actor_kind="page")),
            ("surface", lambda d, s: d.update(surface="profile")),
            ("parent", lambda d, s: d.update(reply_parent_id=None)),
        ]
        for name, change in mutations:
            d, s = copy.deepcopy(destination), copy.deepcopy(selected)
            change(d, s)
            with self.subTest(name=name), self.assertRaises(ValidationError):
                self.create(action="edit_own_content", selected=s, destination=d)
        saved = self.create(action="edit_own_content", selected=selected, destination=destination)
        selected["action_data"]["original_snapshot"]["text"] = "Caller changed source."
        destination["current_snapshot"]["media_refs"].clear()
        self.assertEqual(self.store.inspect("a1")["original_snapshot_sha256"], saved["original_snapshot_sha256"])
        self.assertEqual(self.store.inspect("a1")["target"]["current_snapshot"]["text"], "Original exact text.")

    def test_edit_preflight_rejects_missing_stale_changed_or_wrong_actor_without_claim(self):
        destination, selected = management_plan("edit_own_content")
        self.create(action="edit_own_content", selected=selected, destination=destination)
        evidence = self.preflight()
        changed = copy.deepcopy(evidence)
        changed["current_snapshot"]["text"] = "Changed after selection."
        wrong = [None, {}, changed, evidence | {"actor_id": "other"}, evidence | {"action_sha256": "wrong"},
                 evidence | {"control_available": False}, evidence | {"control_available": 1},
                 evidence | {"observed_at": (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()},
                 evidence | {"observed_at": (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()}]
        for item in wrong:
            with self.subTest(evidence=item), self.assertRaises(ValidationError):
                self.store.begin_action("a1", "op1", preflight_evidence=item)
        self.assertEqual(self.store.inspect("a1")["operations"], [])
        claim = self.store.begin_action("a1", "op1", preflight_evidence=self.preflight())
        self.assertTrue(claim["newly_claimed"])
        self.assertEqual(claim["preflight_evidence"]["current_snapshot"], destination["current_snapshot"])

    def test_verified_edit_preserves_original_and_requires_exact_new_revision(self):
        destination, selected = management_plan("edit_own_content", actor("page"), surface="post")
        saved = self.create(action="edit_own_content", selected=selected, acting=actor("page"), destination=destination)
        self.store.begin_action("a1", "op1", preflight_evidence=self.preflight())
        evidence = self.management_result()
        for field, value in (("text", "Wrong replacement."), ("owner_id", "another-owner"),
                             ("revision_ref", "observed-original-r1"), ("media_refs", [])):
            wrong = copy.deepcopy(evidence)
            wrong["result_snapshot"][field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.store.record_outcome("op1", "verified", wrong)
        for wrong in (evidence | {"original_snapshot_sha256": "wrong"}, evidence | {"action_taken": False},
                      evidence | {"observed_at": "2026-01-01T00:00:00Z"}):
            with self.assertRaises(ValidationError):
                self.store.record_outcome("op1", "verified", wrong)
        self.store.record_outcome("op1", "verified", evidence)
        restored = self.store.inspect("a1")
        self.assertEqual(restored["candidate"], saved["candidate"])
        self.assertEqual(restored["target"]["current_snapshot"]["text"], "Original exact text.")
        self.assertEqual(restored["operations"][0]["observations"][-1]["evidence"]["result_snapshot"]["text"], selected["text"])

    def test_edit_media_preserve_replace_remove_and_noop_are_explicit(self):
        destination, selected = management_plan("edit_own_content")
        wrong = copy.deepcopy(selected)
        wrong["text"] = destination["current_snapshot"]["text"]
        with self.assertRaisesRegex(ValidationError, "no-op"):
            self.create(action="edit_own_content", selected=wrong, destination=destination)
        wrong = copy.deepcopy(selected)
        wrong["action_data"]["media_mode"] = "replace"
        with self.assertRaises(ValidationError):
            self.create(action="edit_own_content", selected=wrong, destination=destination)
        selected["action_data"]["media_mode"] = "remove"
        self.create(action="edit_own_content", selected=selected, destination=destination)
        self.store.begin_action("a1", "op1", preflight_evidence=self.preflight())
        self.store.record_outcome("op1", "verified", self.management_result())
        image = Path(self.temp.name) / "replacement.png"
        image.write_bytes(b"synthetic replacement media")
        destination, selected = management_plan("edit_own_content")
        destination["destination_id"] = "second-content"
        selected["action_data"].update(target_id="second-content", media_mode="replace")
        selected["attachments"] = [{"ref": str(image)}]
        self.create("replace", "edit_own_content", selected=selected, destination=destination)
        image.write_bytes(b"different bytes")
        with self.assertRaisesRegex(StateError, "attachment bytes changed"):
            self.store.begin_action("replace", "replace-op", preflight_evidence=self.preflight("replace"))

    def test_permanent_delete_needs_specific_action_time_human_confirmation(self):
        destination, selected = management_plan("delete_own_content", deletion_mode="permanent")
        self.create(action="delete_own_content", selected=selected, destination=destination)
        preflight, permission = self.preflight(), self.permission()
        invalid = [None, {}, permission | {"approved": False}, permission | {"approved": 1},
                   permission | {"granted_by": "interlocutor"}, permission | {"actor_id": "other"},
                   permission | {"destination_id": "other"}, permission | {"operation_id": "other-op"},
                   permission | {"original_snapshot_sha256": "other"}, permission | {"action_sha256": "other"},
                   permission | {"deletion_mode": "recoverable"}, permission | {"rationale": ""},
                   permission | {"granted_at": "2026-01-01T00:00:00Z"}]
        for value in invalid:
            with self.subTest(permission=value), self.assertRaises(ValidationError):
                self.store.begin_action("a1", "op1", preflight_evidence=preflight, permanent_delete_confirmation=value)
        self.assertEqual(self.store.inspect("a1")["operations"], [])
        self.store.begin_action("a1", "op1", preflight_evidence=self.preflight(), permanent_delete_confirmation=permission)
        evidence = self.management_result()
        for wrong in (evidence | {"deletion_observed": False}, evidence | {"deletion_state": "missing_from_feed"},
                      evidence | {"restore_available": True}, evidence | {"deletion_confirmation_ref": ""}):
            with self.assertRaises(ValidationError):
                self.store.record_outcome("op1", "verified", wrong)
        self.store.record_outcome("op1", "verified", evidence)
        self.assertFalse(self.store.begin_action("a1", "op1")["newly_claimed"])

    def test_specific_pending_confirmation_survives_fresh_unchanged_inspection(self):
        destination, selected = management_plan("delete_own_content", deletion_mode="permanent")
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        with patch("relay_actions._now", return_value=old.isoformat()):
            self.create(action="delete_own_content", selected=selected, destination=destination)
        permission = self.permission() | {"granted_at": (old + timedelta(minutes=10)).isoformat()}
        claim = self.store.begin_action("a1", "op1", preflight_evidence=self.preflight(), permanent_delete_confirmation=permission)
        self.assertTrue(claim["newly_claimed"])
        self.assertEqual(claim["permanent_delete_confirmation"], permission)

    def test_recoverable_delete_requires_observed_recovery_and_no_live_delete_tests(self):
        destination, selected = management_plan("delete_own_content")
        wrong = copy.deepcopy(selected)
        del wrong["action_data"]["recovery_ref"]
        with self.assertRaises(ValidationError):
            self.create(action="delete_own_content", selected=wrong, destination=destination)
        self.create("test", "delete_own_content", selected=selected, destination=destination, scope="live_test")
        self.assertEqual(self.store.action_packet("test")["host_step"], "excluded_from_live_testing")
        with self.assertRaises(StateError):
            self.store.begin_action("test", "test-op", preflight_evidence=self.preflight("test"))
        self.create(action="delete_own_content", selected=selected, destination=destination)
        self.store.begin_action("a1", "op1", preflight_evidence=self.preflight())
        evidence = self.management_result()
        with self.assertRaises(ValidationError):
            self.store.record_outcome("op1", "verified", evidence | {"deletion_state": "permanently_deleted"})
        self.store.record_outcome("op1", "verified", evidence)

    def test_unfollow_and_hide_keep_surface_state_and_action_distinct(self):
        for action in ("unfollow_page", "unfollow_profile", "hide_post", "hide_source"):
            with self.subTest(action=action):
                destination, selected = management_plan(action)
                wrong = copy.deepcopy(selected)
                wrong["action_data"]["like"] = False
                with self.assertRaises(ValidationError):
                    self.create(action, action, selected=wrong, destination=destination)
                wrong = copy.deepcopy(destination)
                wrong["surface"] = "comment"
                with self.assertRaises(ValidationError):
                    self.create(action, action, selected=selected, destination=wrong)
                self.create(action, action, selected=selected, destination=destination)
                self.store.begin_action(action, action + "-op", preflight_evidence=self.preflight(action))
                evidence = self.management_result(action)
                with self.assertRaises(ValidationError):
                    self.store.record_outcome(action + "-op", "verified", evidence | {"publication_state": "published"})
                for wrong in (evidence | {"action": "like_page"}, evidence | {"action_taken": False},
                              evidence | {"result_state_ref": "observed-state-r1"},
                              evidence | {"prior_state_revision_ref": "other"}):
                    with self.assertRaises(ValidationError):
                        self.store.record_outcome(action + "-op", "verified", wrong)
                self.store.record_outcome(action + "-op", "verified", evidence)

    def test_block_requires_abuse_context_actor_scope_specific_permission_and_blocked_result(self):
        destination, selected = management_plan("block", actor("page"))
        for replacement in ({"abuse_context_ref": ""}, {"block_scope": "page_ban"}, {"block_scope": "profile_block"}):
            wrong = copy.deepcopy(selected)
            wrong["action_data"].update(replacement)
            with self.assertRaises(ValidationError):
                self.create(action="block", selected=wrong, destination=destination, acting=actor("page"))
        self.create(action="block", selected=selected, destination=destination, acting=actor("page"))
        preflight, permission = self.preflight(), self.permission()
        for wrong in (None, permission | {"granted_by": "interlocutor"},
                      permission | {"abuse_context_ref": "other-context"}, permission | {"actor_kind": "profile"}):
            with self.assertRaises(ValidationError):
                self.store.begin_action("a1", "op1", preflight_evidence=preflight, block_permission=wrong)
        self.store.begin_action("a1", "op1", preflight_evidence=preflight, block_permission=permission)
        evidence = self.management_result()
        for wrong in (evidence | {"block_state": "not_following"}, evidence | {"block_scope": "profile_block"}):
            with self.assertRaises(ValidationError):
                self.store.record_outcome("op1", "verified", wrong)
        self.store.record_outcome("op1", "verified", evidence)

    def test_new_management_outcomes_are_not_pending_or_unpublished_publications(self):
        for action in ("edit_own_content", "delete_own_content", "unfollow_page", "unfollow_profile", "hide_post", "hide_source", "block"):
            with self.subTest(action=action):
                destination, selected = management_plan(action)
                self.create(action, action, selected=selected, destination=destination)
                kwargs = {"preflight_evidence": self.preflight(action)}
                if action == "block":
                    kwargs["block_permission"] = self.permission(action, action + "-op")
                self.store.begin_action(action, action + "-op", **kwargs)
                for outcome in ("submitted_pending", "not_published"):
                    with self.assertRaises(ValidationError):
                        self.store.record_outcome(action + "-op", outcome, self.management_result(action))
                self.store.record_outcome(action + "-op", "failed_before_submit", {
                    "source_ref": "synthetic-only", "observation": "No external action was taken.", "positive_no_submit": True})

    def test_completed_mutation_cannot_be_cloned_with_new_local_ids_or_provenance(self):
        destination, selected = management_plan("edit_own_content")
        self.create(action="edit_own_content", selected=selected, destination=destination)
        self.store.begin_action("a1", "op1", preflight_evidence=self.preflight())
        self.store.record_outcome("op1", "verified", self.management_result())
        clone = copy.deepcopy(selected)
        clone.update(candidate_id="new-id", source_ref="new-source", response_ref="new-response")
        self.create("clone", "edit_own_content", selected=clone, destination=destination)
        with self.assertRaisesRegex(StateError, "consumed snapshot"):
            self.store.begin_action("clone", "clone-op", preflight_evidence=self.preflight("clone"))
        destination, selected = management_plan("unfollow_page")
        self.create("follow", "unfollow_page", selected=selected, destination=destination)
        self.store.begin_action("follow", "follow-op", preflight_evidence=self.preflight("follow"))
        self.store.record_outcome("follow-op", "verified", self.management_result("follow"))
        selected.update(candidate_id="different", source_ref="different", response_ref="different")
        self.create("follow-clone", "unfollow_page", selected=selected, destination=destination)
        with self.assertRaisesRegex(StateError, "consumed snapshot"):
            self.store.begin_action("follow-clone", "follow-clone-op", preflight_evidence=self.preflight("follow-clone"))

    def test_interrupted_mutation_preserves_claim_and_confirmation_without_blind_retry(self):
        destination, selected = management_plan("delete_own_content", deletion_mode="permanent")
        self.create(action="delete_own_content", selected=selected, destination=destination)
        self.store.begin_action("a1", "op1", preflight_evidence=self.preflight(), permanent_delete_confirmation=self.permission())
        self.store.close()
        self.store = ActionStore(self.db)
        self.assertEqual(self.store.recover(), ["op1"])
        with self.assertRaises(StateError):
            self.store.begin_action("a1", "another", preflight_evidence=self.preflight(), permanent_delete_confirmation=self.permission())
        saved = self.store.inspect("a1")["operations"][0]
        self.assertEqual(saved["permanent_delete_confirmation"]["operation_id"], "op1")
        self.store.reconcile("op1", "verified", self.management_result(), "actual-new-observation")
        self.assertEqual(len(self.store.inspect("a1")["operations"]), 1)


    def test_unexpected_pending_edit_preserves_known_submission_without_retry_or_false_success(self):
        destination, selected = management_plan("edit_own_content")
        action = self.create(action="edit_own_content", selected=selected, destination=destination)
        self.store.begin_action("a1", "op1", preflight_evidence=self.preflight())
        evidence = self.evidence() | {"target_url": destination["target_url"],
                    "original_snapshot_sha256": action["original_snapshot_sha256"],
                    "edit_revision_state": "pending", "submission_observed": True,
                    "pending_revision_ref": "actual-visible-pending-edit", "observed_at": datetime.now(timezone.utc).isoformat()}
        for outcome in ("verified", "failed_before_submit", "submitted_pending"):
            with self.subTest(outcome=outcome), self.assertRaises(ValidationError):
                self.store.record_outcome("op1", outcome, evidence)
        self.store.record_outcome("op1", "uncertain", evidence)
        self.create("unrelated")
        with self.assertRaises(StateError):
            self.store.begin_action("unrelated", "unrelated-op")
        with self.assertRaises(StateError):
            self.store.reconcile("op1", "failed_before_submit", {
                "source_ref": "wrong", "observation": "Cannot erase observed submission.", "positive_no_submit": True}, "wrong")
        self.store.reconcile("op1", "verified", self.management_result(), "actual-later-revised-content")
        saved = self.store.inspect("a1")
        self.assertEqual(saved["operations"][0]["observations"][0]["evidence"]["edit_revision_state"], "pending")
        self.assertEqual(saved["target"]["current_snapshot"]["text"], "Original exact text.")
        self.assertEqual(saved["execution_state"], "verified")


    def test_mentions_validate_exact_unicode_spans_and_identity(self):
        selected = candidate("🌙 Alex, hello.", {"mentions": [mention(2, 6)]})
        destination = target("comment", "public", "parent-comment")
        destination["target_url"] = "https://social.example/comments/parent-comment"
        document = self.create("valid-mention", "reply", selected=selected, destination=destination)
        self.assertEqual(document["candidate"]["action_data"]["mentions"], [mention(2, 6)])
        invalid = [None, {}, [mention(True, 6)], [mention(2, True)], [mention(-1, 4)],
                   [mention(2, 20)], [mention(2, 2, "")], [mention(2, 6, "Alec")],
                   [mention(3, 7)], [mention(2, 6, target_id="")],
                   [mention(2, 6, target_url="http://social.example/people/a")],
                   [mention(2, 6) | {"notification_sent": True}], [mention(2, 6), mention(2, 6)]]
        for index, mentions in enumerate(invalid):
            with self.subTest(mentions=mentions), self.assertRaises(ValidationError):
                self.create(f"bad-mention-{index}", "reply", destination=destination,
                            selected=candidate("🌙 Alex, hello.", {"mentions": mentions}))
        with self.assertRaises(ValidationError):
            self.create("noncontent", "react", destination=destination,
                        selected=candidate(data={"reaction": "LOVE", "mentions": []}))

    def test_same_text_native_mention_edit_and_removal_are_real_revisions(self):
        for index, (before, after) in enumerate((([], [mention()]), ([mention()], []),
                ([mention()], [mention(target_id="recipient-b", target_url="https://social.example/people/recipient-b")]))):
            destination, selected = mention_edit(before, after)
            action_id, operation_id = f"rich-edit-{index}", f"rich-op-{index}"
            destination["destination_id"] = f"rich-object-{index}"
            selected["action_data"]["target_id"] = destination["destination_id"]
            try:
                original = self.create(action_id, "edit_own_content", destination=destination, selected=selected)
            except ValidationError as exc:
                self.fail(f"A selected native-mention change is not a text/media no-op: {exc}")
            preflight = self.preflight(action_id) | {"rendered_mentions": after}
            self.store.begin_action(action_id, operation_id, preflight_evidence=preflight)
            result = self.management_result(action_id) | {"rendered_mentions": after}
            result["result_snapshot"]["mentions"] = after
            self.store.record_outcome(operation_id, "verified", result)
            saved = self.store.inspect(action_id)
            self.assertEqual(saved["target"]["current_snapshot"]["mentions"], before)
            self.assertEqual(saved["original_snapshot_sha256"], original["original_snapshot_sha256"])
            self.assertEqual(saved["execution_state"], "verified")
        destination, selected = mention_edit([mention()], [mention()])
        with self.assertRaisesRegex(ValidationError, "no-op"):
            self.create("unchanged-rich", "edit_own_content", destination=destination, selected=selected)

    def test_rich_edit_requires_explicit_original_and_replacement_mentions(self):
        destination, selected = mention_edit()
        for snapshot in (destination["current_snapshot"], selected["action_data"]["original_snapshot"]):
            del snapshot["mentions"]
        selected["text"] = "Alex, changed."
        with self.assertRaises(ValidationError):
            self.create("unobserved-original", "edit_own_content", destination=destination, selected=selected)
        for index, before in enumerate(([], [mention()])):
            destination, selected = mention_edit(before)
            del selected["action_data"]["mentions"]
            selected["text"] = "Alex, changed."
            with self.assertRaises(ValidationError):
                self.create(f"implicit-replacement-{index}", "edit_own_content", destination=destination, selected=selected)

    def test_mention_preflight_binds_actual_parent_and_rendered_link_before_claim(self):
        destination = target("comment", "public", "parent-comment")
        destination["target_url"] = "https://social.example/comments/parent-comment"
        self.create("a1", "reply", destination=destination, selected=candidate("Alex, hello.", {"mentions": [mention()]}))
        evidence = self.preflight() | {"rendered_mentions": [mention()]}
        invalid = [None, self.preflight(), evidence | {"reply_parent_id": "different-comment"},
                   evidence | {"actor_id": "other-actor"}, evidence | {"rendered_mentions": []},
                   evidence | {"rendered_mentions": [mention(target_id="other-person")]},
                   evidence | {"observed_at": (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()}]
        for item in invalid:
            with self.subTest(evidence=item), self.assertRaises(ValidationError):
                self.store.begin_action("a1", "op1", preflight_evidence=item)
            self.assertEqual(self.store.inspect("a1")["execution_state"], "not_started")
        self.assertTrue(self.store.begin_action("a1", "op1", preflight_evidence=evidence)["newly_claimed"])

    def test_mention_result_requires_persisted_link_and_keeps_guard_on_rejected_evidence(self):
        destination = target("comment", "public", "parent-comment")
        destination["target_url"] = "https://social.example/comments/parent-comment"
        self.create("a1", "reply", destination=destination, selected=candidate("Alex, hello.", {"mentions": [mention()]}))
        self.store.begin_action("a1", "op1", preflight_evidence=self.preflight() | {"rendered_mentions": [mention()]})
        evidence = self.evidence() | {"target_url": destination["target_url"], "rendered_mentions": [mention()]}
        for outcome in ("verified", "submitted_pending"):
            base = evidence | ({"submission_observed": True, "publication_state": "pending"} if outcome == "submitted_pending" else {})
            invalid = [{key: value for key, value in base.items() if key != "rendered_mentions"},
                       base | {"rendered_mentions": []}, base | {"rendered_mentions": [mention(target_id="other-person")]},
                       base | {"rendered_mentions": [mention(target_url="https://social.example/people/other")]},
                       base | {"rendered_mentions": [mention(1, 5)]}, base | {"reply_parent_id": "wrong-parent"}]
            for item in invalid:
                with self.subTest(outcome=outcome, evidence=item), self.assertRaises(ValidationError):
                    self.store.record_outcome("op1", outcome, item)
                self.assertEqual(self.store.inspect("a1")["execution_state"], "in_flight")
        self.create("unrelated")
        with self.assertRaisesRegex(StateError, "shared delivery"):
            self.store.begin_action("unrelated", "unrelated-op")
        self.store.record_outcome("op1", "verified", evidence)
        self.assertTrue(self.store.begin_action("unrelated", "unrelated-op")["newly_claimed"])

    def test_rich_edit_result_snapshot_cannot_omit_or_change_selected_mentions(self):
        destination, selected = mention_edit([mention()], [])
        self.create("a1", "edit_own_content", destination=destination, selected=selected)
        self.store.begin_action("a1", "op1", preflight_evidence=self.preflight() | {"rendered_mentions": []})
        evidence = self.management_result() | {"rendered_mentions": []}
        for before in ([mention()], None):
            wrong = copy.deepcopy(evidence)
            if before is None:
                del wrong["result_snapshot"]["mentions"]
            else:
                wrong["result_snapshot"]["mentions"] = before
            with self.assertRaises(ValidationError):
                self.store.record_outcome("op1", "verified", wrong)
        evidence["result_snapshot"]["mentions"] = []
        self.store.record_outcome("op1", "verified", evidence)

    def test_legacy_snapshot_shape_and_digest_do_not_gain_unobserved_mentions(self):
        destination, selected = management_plan("edit_own_content")
        original = copy.deepcopy(destination["current_snapshot"])
        expected_digest = hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True,
                                                    separators=(",", ":")).encode("utf-8")).hexdigest()
        self.create("a1", "edit_own_content", destination=destination, selected=selected)
        self.store.begin_action("a1", "op1", preflight_evidence=self.preflight())
        self.store.record_outcome("op1", "verified", self.management_result())
        stored = self.store.inspect("a1")
        self.assertEqual(stored["original_snapshot_sha256"], expected_digest)
        self.assertEqual(stored["target"]["current_snapshot"], original)
        self.assertNotIn("mentions", stored["target"]["current_snapshot"])
        self.assertNotIn("rendered_mentions", stored["operations"][0]["preflight_evidence"])

    def test_pending_publication_duplicate_uses_mention_semantics_not_only_visible_text(self):
        destination = target("comment", "public", "parent-comment")
        destination["target_url"] = "https://social.example/comments/parent-comment"
        self.create("a1", "reply", destination=destination, selected=candidate("Alex, hello.", {"mentions": [mention()]}))
        self.store.begin_action("a1", "op1", preflight_evidence=self.preflight() | {"rendered_mentions": [mention()]})
        self.store.record_outcome("op1", "submitted_pending", self.pending_evidence() | {
            "target_url": destination["target_url"], "rendered_mentions": [mention()]})
        clone = candidate("Alex, hello.", {"mentions": [mention()]})
        clone.update(candidate_id="new-local-id", source_ref="different-evidence")
        self.create("clone", "reply", destination=destination, selected=clone)
        with self.assertRaisesRegex(StateError, "duplicates submitted"):
            self.store.begin_action("clone", "clone-op", preflight_evidence=self.preflight("clone") | {"rendered_mentions": [mention()]})
        different = [mention(target_id="recipient-b", target_url="https://social.example/people/recipient-b")]
        clone["action_data"]["mentions"] = different
        self.create("distinct", "reply", destination=destination, selected=clone)
        self.assertTrue(self.store.begin_action("distinct", "distinct-op",
            preflight_evidence=self.preflight("distinct") | {"rendered_mentions": different})["newly_claimed"])

    def test_legacy_edit_cannot_verify_unselected_native_mentions(self):
        destination, selected = management_plan("edit_own_content")
        self.create("a1", "edit_own_content", destination=destination, selected=selected)
        self.store.begin_action("a1", "op1", preflight_evidence=self.preflight())
        result = self.management_result()
        unselected = [mention(0, 11, "Replacement")]
        unexpected = copy.deepcopy(result)
        unexpected["result_snapshot"]["mentions"] = unselected
        with self.assertRaises(ValidationError):
            self.store.record_outcome("op1", "verified", unexpected)
        with self.assertRaises(ValidationError):
            self.store.record_outcome("op1", "verified", result | {"rendered_mentions": unselected})
        self.assertEqual(self.store.inspect("a1")["execution_state"], "in_flight")
        self.create("unrelated")
        with self.assertRaisesRegex(StateError, "shared delivery"):
            self.store.begin_action("unrelated", "unrelated-op")
        self.store.record_outcome("op1", "verified", result)
        self.assertEqual(self.store.inspect("a1")["execution_state"], "verified")

    def test_rich_snapshot_decoration_cannot_revive_consumed_owned_revision(self):
        destination, selected = management_plan("edit_own_content")
        self.create("a1", "edit_own_content", destination=destination, selected=selected)
        self.store.begin_action("a1", "op1", preflight_evidence=self.preflight())
        self.store.record_outcome("op1", "verified", self.management_result())
        destination["current_snapshot"]["mentions"] = []
        selected["action_data"]["original_snapshot"]["mentions"] = []
        selected["action_data"]["mentions"] = []
        selected.update(candidate_id="new-local-candidate", source_ref="new-evidence")
        self.create("decorated", "edit_own_content", destination=destination, selected=selected)
        with self.assertRaisesRegex(StateError, "consumed snapshot"):
            self.store.begin_action("decorated", "decorated-op",
                                    preflight_evidence=self.preflight("decorated") | {"rendered_mentions": []})
        self.assertEqual(self.store.inspect("decorated")["execution_state"], "not_started")
        for snapshot in (destination["current_snapshot"], selected["action_data"]["original_snapshot"]):
            snapshot["revision_ref"] = "actually-observed-new-revision"
        selected.update(candidate_id="new-revision-selection", source_ref="new-author-choice")
        self.create("new-revision", "edit_own_content", destination=destination, selected=selected)
        self.assertTrue(self.store.begin_action("new-revision", "new-revision-op",
            preflight_evidence=self.preflight("new-revision") | {"rendered_mentions": []})["newly_claimed"])
        self.assertNotIn("mentions", self.store.inspect("a1")["target"]["current_snapshot"])

    def test_pending_native_mention_url_alias_cannot_repeat_same_submission(self):
        destination = target("comment", "public", "parent-comment")
        destination["target_url"] = "https://social.example/comments/parent-comment"
        self.create("a1", "reply", destination=destination, selected=candidate("Alex, hello.", {"mentions": [mention()]}))
        self.store.begin_action("a1", "op1", preflight_evidence=self.preflight() | {"rendered_mentions": [mention()]})
        self.store.record_outcome("op1", "submitted_pending", self.pending_evidence() | {
            "target_url": destination["target_url"], "rendered_mentions": [mention()]})
        alias = [mention(target_url="https://social.example/people/recipient-a?ref=reply")]
        clone = candidate("Alex, hello.", {"mentions": alias})
        clone.update(candidate_id="alias-candidate", source_ref="alias-source", response_ref="alias-response")
        self.create("alias", "reply", destination=destination, selected=clone)
        with self.assertRaisesRegex(StateError, "duplicates submitted"):
            self.store.begin_action("alias", "alias-op",
                                    preflight_evidence=self.preflight("alias") | {"rendered_mentions": alias})
        self.assertEqual(self.store.inspect("alias")["execution_state"], "not_started")
        self.assertEqual(self.store.inspect("a1")["execution_state"], "submitted_pending")
        self.create("unrelated")
        self.assertTrue(self.store.begin_action("unrelated", "unrelated-op")["newly_claimed"])


if __name__ == "__main__":
    unittest.main()
