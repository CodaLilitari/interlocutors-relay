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

    def pending_evidence(self, action_id="a1"):
        return self.evidence(action_id) | {"submission_observed": True, "publication_state": "pending",
                                         "observation": "Exact selected comment visibly persists with a Pending label."}

    def create_publication(self, action_id="a1", **kwargs):
        return self.create(action_id, "comment", destination=target("post", "public", "parent-post"), **kwargs)

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
                           ("block", candidate(), target("profile", "account"), {}),
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


if __name__ == "__main__":
    unittest.main()
