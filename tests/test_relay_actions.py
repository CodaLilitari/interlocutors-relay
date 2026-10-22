import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

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

    def test_block_can_be_described_but_cannot_dispatch_as_live_test(self):
        self.create(action="block", selected=candidate(), destination=target("profile", "account"), scope="live_test")
        packet = self.store.action_packet("a1")
        self.assertEqual(packet["host_step"], "excluded_from_live_testing")
        self.assertFalse(packet["executes"])
        with self.assertRaisesRegex(StateError, "excluded from live testing"):
            self.store.begin_action("a1", "block-test")
        self.assertEqual(self.store.inspect("a1")["operations"], [])
        self.create("page-block", "block", selected=candidate(), destination=target("page", "account"), scope="live_test")
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


if __name__ == "__main__":
    unittest.main()
