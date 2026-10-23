import tempfile
import unittest
from pathlib import Path

from relay_core import ConflictError, NotFoundError, StateError, Store, ValidationError
from relay_session import DISPOSITIONS, SessionStore, apply_session_request


class RelaySessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "sessions.sqlite"
        self.store = SessionStore(self.path)
        self.store.create_session("private", "example-thread", "originated",
                                  "Explore this thought privately.")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def send(self, turn="t1", purpose="author_intake", result_id=None):
        self.store.prepare_turn("private", turn, "Coda: actual outbound message.",
                                purpose=purpose, result_id=result_id)
        self.store.begin_dispatch("private", turn, f"attempt-{turn}")
        self.store.record_sent("private", turn, f"saved-user-message-{turn}",
                               {"observation": "Reopened exact conversation and read the sent message."})

    def answer(self, disposition="REPLY", candidates=None, turn="t1"):
        if candidates is None and disposition == "REPLY":
            candidates = [self.candidate("a", "A shorter reply."),
                          self.candidate("b", "A longer alternative, not selected by length.")]
        self.store.record_response("private", turn, f"saved-author-message-{turn}",
                                   "Exact response\r\nwith original formatting.\n",
                                   disposition, candidates)

    @staticmethod
    def candidate(candidate_id, text):
        return {"candidate_id": candidate_id, "text": text,
                "source_ref": {"message": "saved-author-message-t1", "candidate": candidate_id}}

    def selected(self):
        self.send()
        self.answer()
        self.store.select_candidate("private", "t1", "a", "author explicitly selected A")

    def test_private_and_observed_intakes_preserve_context_without_fake_targets(self):
        private = self.store.author_intake_packet("private")
        self.assertIsNone(private["source"])
        self.assertIsNone(private["audience"])
        self.assertNotIn("actor_id", private)
        self.assertNotIn("destination_id", private)
        self.assertFalse(private["executes"])
        source = {"ref": "platform/source/123", "exact_text": "A source quotation.",
                  "observed_at": "2026-10-22T19:00:00Z"}
        attachments = [{"ref": "C:/explicit-allowlist/image.png", "sha256": "a" * 64}]
        self.store.create_session("observed", "example-thread", "observed", "Your disposition?",
                                  source=source, context="The earlier question is unresolved.",
                                  addressee="original author", audience="private review", attachments=attachments)
        packet = self.store.author_intake_packet("observed")
        self.assertEqual(packet["source"], source)
        self.assertEqual(packet["attachments"], attachments)
        self.assertEqual(packet["context"], "The earlier question is unresolved.")
        self.assertEqual(set(packet["available_dispositions"]), DISPOSITIONS)
        with self.assertRaises(ValidationError):
            self.store.create_session("missing", "route", "observed", "Review this.")

    def test_crash_after_claim_requires_reconciliation_and_preserves_exact_message(self):
        message = "Coda: two lines\r\nwith exact original text.\n"
        attachment = {"ref": "C:/explicit-allowlist/pilot.png"}
        self.store.prepare_turn("private", "t1", message, [attachment])
        dispatch = self.store.begin_dispatch("private", "t1", "first-attempt")
        self.assertEqual(dispatch["exact_message"], message)
        self.store.close()
        self.store = SessionStore(self.path)
        step = self.store.next_step_packet("private")
        self.assertEqual(step["host_step"], "reconcile_before_retry")
        self.assertEqual(step["turn"]["attachments"], [attachment])
        with self.assertRaises(StateError):
            self.store.begin_dispatch("private", "t1", "second-attempt")
        with self.assertRaises(StateError):
            self.store.prepare_turn("private", "replacement", "A duplicate attempt.")
        self.store.reconcile_dispatch("private", "t1", "sent", "Reopened saved history; exact message exists.",
                                       "actual-sent-message-id")
        self.assertEqual(self.store.next_step_packet("private")["host_step"], "retrieve_author_response")
        self.answer()
        response = self.store.inspect("private")["turns"][0]["response"]
        self.assertEqual(response["exact_response"], "Exact response\r\nwith original formatting.\n")
        self.assertEqual(response["response_ref"], "saved-author-message-t1")

    def test_uncertain_send_cannot_retry_until_actual_not_sent_observation(self):
        self.store.prepare_turn("private", "t1", "Exact unsent message")
        self.store.begin_dispatch("private", "t1", "attempt1")
        self.store.mark_uncertain("private", "t1", "Tool timed out after click.")
        with self.assertRaises(StateError):
            self.store.begin_dispatch("private", "t1", "attempt2")
        self.store.reconcile_dispatch("private", "t1", "not_sent",
                                       {"ref": "history-check", "observation": "Composer remains unsent; history has no message."})
        with self.assertRaises(ConflictError):
            self.store.begin_dispatch("private", "t1", "attempt1")
        self.store.begin_dispatch("private", "t1", "attempt2")
        self.store.record_sent("private", "t1", "sent-2", "Observed persisted message.")
        turn = self.store.inspect("private")["turns"][0]
        self.assertEqual([item["state"] for item in turn["attempts"]], ["not_sent", "sent"])
        self.assertEqual([item["outcome"] for item in turn["attempts"][0]["observations"]],
                         ["uncertain", "not_sent"])

    def test_second_store_cannot_claim_a_dispatch_already_owned(self):
        self.store.prepare_turn("private", "t1", "One message only")
        other = SessionStore(self.path)
        try:
            self.store.begin_dispatch("private", "t1", "one")
            with self.assertRaises(StateError):
                other.begin_dispatch("private", "t1", "two")
            with self.assertRaises(StateError):
                other.record_response("private", "t1", "response", "Observed words", "QUIET")
        finally:
            other.close()

    def test_one_way_receipt_closes_on_observed_send_without_invented_response(self):
        self.store.prepare_turn("private", "thanks", "Coda: Thank you. No response needed.",
                                expects_response=False)
        self.store.begin_dispatch("private", "thanks", "attempt-thanks")
        self.store.reconcile_dispatch("private", "thanks", "sent", "Exact message visible in saved history.",
                                       "actual-user-only-message")
        turn = self.store.inspect("private")["turns"][0]
        self.assertEqual(turn["state"], "closed")
        self.assertEqual(turn["attempts"][0]["state"], "sent")
        self.assertIsNone(turn["response"])
        self.assertEqual(self.store.next_step_packet("private")["host_step"], "complete_without_response")
        self.store.prepare_turn("private", "next", "A later independent continuation.", purpose="continuation")

    def test_result_reconciliation_survives_return_and_hold_without_resubmission(self):
        self.selected()
        self.store.record_result("private", "r1", "uncertain", "Submit timed out.", "timeout-receipt",
                                  action_authority="original-action-instruction")
        self.send("return-1", purpose="return_result", result_id="r1")
        self.answer("HOLD", turn="return-1")
        reconciled = self.store.reconcile_result("private", "r1", "r2", "verified",
                                                  "Original comment is now visible under correct actor.",
                                                  "actual-persisted-comment-reference")
        self.assertEqual(reconciled["turn_id"], "t1")
        self.assertEqual(reconciled["candidate"]["text"], "A shorter reply.")
        self.assertEqual(reconciled["action_authority"], "original-action-instruction")
        self.assertEqual(reconciled["reconciles_result_id"], "r1")
        self.assertEqual([item["outcome"] for item in self.store.inspect("private")["results"]],
                         ["uncertain", "verified"])
        self.assertEqual(self.store.next_step_packet("private")["result_id"], "r2")
        with self.assertRaises(StateError):
            self.store.reconcile_result("private", "r1", "duplicate-resolution", "verified", "Again", "ref")
        self.store.prepare_turn("private", "return-2", "Coda: The original comment is verified. No reply needed.",
                                purpose="return_result", result_id="r2", expects_response=False)
        self.store.begin_dispatch("private", "return-2", "return-attempt-2")
        self.store.record_sent("private", "return-2", "actual-result-return-message", "Saved history checked.")
        self.assertEqual(self.store.next_step_packet("private")["host_step"], "complete_without_response")
        self.assertEqual(len(self.store.inspect("private")["turns"]), 3)

    def test_author_route_requires_canonical_saved_thread_string_before_intake(self):
        with self.assertRaises(ValidationError):
            self.store.create_session("ambiguous", {"thread_id": "example-thread"}, "originated", "Request")

    def test_pending_publication_survives_return_hold_and_restart_without_resend(self):
        self.selected()
        self.store.record_result("private", "unknown", "uncertain", "Submission result unclear.", "timeout")
        self.send("return-unknown", purpose="return_result", result_id="unknown")
        self.answer("HOLD", turn="return-unknown")
        pending = self.store.reconcile_result("private", "unknown", "pending", "submitted_pending",
                                              "Reopened exact submitted comment marked Pending.", "pending-action-receipt")
        self.assertEqual(pending["candidate"]["candidate_id"], "a")
        self.assertEqual(pending["turn_id"], "t1")
        self.assertEqual(self.store.return_result_packet("private", "pending")["execution_state"], "submitted_pending")
        self.store.prepare_turn("private", "return-pending", "Coda: Submitted, Pending; not published.",
                                purpose="return_result", result_id="pending", expects_response=False)
        self.store.begin_dispatch("private", "return-pending", "pending-return-attempt")
        self.store.record_sent("private", "return-pending", "saved-pending-return", "Actual receipt saved.")
        self.store.close()
        self.store = SessionStore(self.path)
        self.assertEqual(self.store.next_step_packet("private")["host_step"], "observe_pending_publication")
        step = self.store.next_step_packet("private", turn_id="t1")
        self.assertEqual(step["host_step"], "observe_pending_publication")
        self.assertFalse(step["output_required"])
        with self.assertRaisesRegex(StateError, "reconcile its latest result"):
            self.store.record_result("private", "reset", "verified", "Unlinked replacement.", "bad-ref", turn_id="t1")
        for bridge in (
            lambda: self.store.export_to_action_request("private", "duplicate", "comment", {}, {}, {}, turn_id="t1"),
            lambda: self.store.export_to_core_request("private", "duplicate", "r1", {}, turn_id="t1"),
        ):
            with self.assertRaisesRegex(StateError, "already submitted"):
                bridge()
        for outcome in ("uncertain", "failed_before_submit"):
            with self.assertRaises(StateError):
                self.store.reconcile_result("private", "pending", "invalid", outcome, "Cannot undo a known send.", "bad-ref")
        # A different selected candidate can still be carried under its own authority.
        self.store.select_candidate("private", "t1", "b", "Distinct selected action")
        self.assertEqual(self.store.next_step_packet("private", turn_id="t1")["host_step"],
                         "carry_selected_candidate_under_applicable_authority")
        terminal = self.store.reconcile_result("private", "pending", "rejected", "not_published",
                                               "Platform explicitly rejected the original comment.", "rejection-receipt")
        self.assertEqual(terminal["candidate"]["candidate_id"], "a")
        self.assertEqual([r["outcome"] for r in self.store.inspect("private")["results"]],
                         ["uncertain", "submitted_pending", "not_published"])
        self.assertEqual(self.store.next_step_packet("private")["result_id"], "rejected")
        self.send("return-rejected", purpose="return_result", result_id="rejected")
        self.answer("HOLD", turn="return-rejected")
        self.store.select_candidate("private", "t1", "a", "Original choice remains held")
        self.assertEqual(self.store.next_step_packet("private", turn_id="t1")["host_step"], "respect_not_published")
        with self.assertRaisesRegex(StateError, "already submitted"):
            self.store.export_to_action_request("private", "duplicate-terminal", "comment", {}, {}, {}, turn_id="t1")
        with self.assertRaises(StateError):
            self.store.reconcile_result("private", "rejected", "retry", "submitted_pending", "No new submission.", "bad-ref")

    def test_pending_observation_chain_requires_latest_result_and_keeps_original_authority(self):
        self.selected()
        self.store.record_result("private", "p1", "submitted_pending", "Pending on first reopen.", "first-ref",
                                  action_authority="original-user-scope")
        result = self.store.reconcile_result("private", "p1", "p2", "submitted_pending",
                                            "Still pending on a later inspection.", "second-ref")
        self.assertEqual(result["action_authority"], "original-user-scope")
        with self.assertRaises(StateError):
            self.store.reconcile_result("private", "p1", "stale", "verified", "Stale chain.", "bad-ref")
        self.store.reconcile_result("private", "p2", "published", "verified", "Visible exact published result.", "publication-ref")
        self.store.prepare_turn("private", "published-return", "Coda: The selected comment is published.",
                                purpose="return_result", result_id="published", expects_response=False)
        self.store.begin_dispatch("private", "published-return", "published-return-attempt")
        self.store.record_sent("private", "published-return", "saved-published-return", "Observed saved receipt.")
        step = self.store.next_step_packet("private", turn_id="t1")
        self.assertEqual(step["host_step"], "respect_verified_publication")
        self.assertFalse(step["output_required"])
        for bridge in (lambda: self.store.export_to_action_request("private", "repeat", "comment", {}, {}, {}, turn_id="t1"),
                       lambda: self.store.export_to_core_request("private", "repeat", "r1", {}, turn_id="t1")):
            with self.assertRaises(StateError):
                bridge()
        self.assertEqual(self.store.return_result_packet("private", "published")["result"]["candidate"]["text"],
                         "A shorter reply.")

    def test_publication_outcome_cannot_label_a_reaction_pending(self):
        self.send()
        self.answer("REACT", [{"candidate_id": "reaction", "source_ref": "choice", "action_data": {"reaction": "WOW"}}])
        self.store.select_candidate("private", "t1", "reaction", "selected-reaction")
        with self.assertRaises(ValidationError):
            self.store.record_result("private", "invalid", "submitted_pending", "Not a publication.", "ref")

    def test_quiet_hold_disagreement_and_requests_are_first_class(self):
        for index, disposition in enumerate(("QUIET", "HOLD", "DISAGREE", "NEED_CONTEXT", "NEED_RESEARCH")):
            turn = f"t{index}"
            self.send(turn)
            self.answer(disposition, turn=turn)
            packet = self.store.next_step_packet("private")
            self.assertFalse(packet["output_required"])
            self.assertEqual(packet["disposition"], disposition)
            self.assertEqual(packet["turn"]["response"]["candidates"], [])
            with self.assertRaises(StateError):
                self.store.export_to_core_request("private", "case", "rev", {})
        self.assertEqual(len(self.store.inspect("private")["turns"]), 5)

    def test_selection_is_explicit_and_core_bridge_does_not_grant_authority(self):
        self.send()
        self.answer()
        self.assertEqual(self.store.next_step_packet("private")["host_step"], "select_candidate_explicitly")
        with self.assertRaises(StateError):
            self.store.export_to_core_request("private", "case", "rev", {})
        self.store.select_candidate("private", "t1", "a", "saved-author-selection")
        binding = {"author_route": "example-thread", "actor_id": "example-page", "actor_kind": "page",
                   "destination_id": "example-target", "action": "reply", "context_ref": "actual-source-id",
                   "reply_parent_id": "actual-parent-id", "audience": "public"}
        packet = self.store.export_to_core_request("private", "case", "rev", binding)
        self.assertFalse(packet["executes"])
        self.assertEqual(packet["action_authority"], "not_recorded")
        core = Store(":memory:")
        try:
            for request in packet["requests"]:
                getattr(core, request["method"])(**request["arguments"])
            state = core.inspect("case")
            self.assertEqual(state["revisions"][0]["text"], "A shorter reply.")
            self.assertIsNone(state["selected_revision"])
            self.assertEqual(state["authorities"], [])
        finally:
            core.close()
        with self.assertRaises(ValidationError):
            self.store.export_to_core_request("private", "case", "rev", binding | {"actor_id": ""})
        self.store.prepare_turn("private", "t2", "Please revise the choice.", purpose="continuation")
        with self.assertRaises(StateError):
            self.store.export_to_core_request("private", "case", "rev", binding)

    def test_image_selection_preserves_attachment_and_exports_only_existing_metadata(self):
        self.send()
        media = {"file_id": "pilot", "sha256": "a" * 64, "byte_size": 123,
                 "mime_type": "image/png", "alt_text": "Author-written alt text"}
        candidate = {"candidate_id": "image1", "text": "Caption", "source_ref": "saved-image-bundle",
                     "attachments": [{"ref": "C:/exact-allowlist/pilot.png", "core_media": media}]}
        self.answer("IMAGE", [candidate])
        self.store.select_candidate("private", "t1", "image1", "saved-bundle-selection")
        binding = {"author_route": "example-thread", "actor_id": "page", "actor_kind": "page",
                   "destination_id": "destination", "action": "post", "context_ref": "source"}
        packet = self.store.export_to_core_request("private", "case", "rev", binding)
        self.assertEqual(packet["requests"][1]["arguments"]["media"], [media])
        self.assertNotIn("C:/exact-allowlist", str(packet))

    def test_actual_result_return_and_social_followup_remain_linked(self):
        self.selected()
        result = self.store.record_result("private", "r1", "uncertain", "UI timed out after submit.",
                                          {"ref": "receipt-unknown", "observed_at": "2026-10-22T20:00:00Z"},
                                          action_authority={"ref": "actual-user-instruction"})
        self.assertEqual(result["candidate"]["text"], "A shorter reply.")
        self.assertEqual(self.store.next_step_packet("private")["host_step"], "prepare_return_result")
        packet = self.store.return_result_packet("private", "r1")
        self.assertEqual(packet["author_route"], "example-thread")
        self.assertEqual(packet["execution_state"], "uncertain")
        self.assertEqual(packet["result"]["observation"], "UI timed out after submit.")
        self.send("return-1", purpose="return_result", result_id="r1")
        self.answer("HOLD", turn="return-1")
        self.assertEqual(self.store.return_result_packet("private", "r1")["return_turn"]["state"], "responded")
        with self.assertRaises(StateError):
            self.store.prepare_turn("private", "duplicate-return", "Send same result again.",
                                    purpose="return_result", result_id="r1")
        followup = self.store.observe_followup(
            "private", "r1", "social-reply", source={"ref": "actual-platform-reply", "exact_text": "What did you mean?"},
            context="A reply now appears under the originally uncertain submission.",
            request="Here is the response. Would you like to continue?", addressee="social correspondent",
            audience="public if a later reply is selected")
        self.assertEqual(followup["parent"]["result_outcome"], "uncertain")
        self.assertEqual(followup["parent"]["candidate"]["text"], "A shorter reply.")
        intake = self.store.author_intake_packet("social-reply")
        self.assertEqual(intake["source"]["exact_text"], "What did you mean?")
        self.assertEqual(intake["author_route"], "example-thread")
        with self.assertRaises(NotFoundError):
            self.store.observe_followup("private", "nonexistent", "invalid", "source", "context", "request")

    def test_actions_can_be_selected_without_invented_text_payload(self):
        for index, disposition in enumerate(("REACT", "SHARE")):
            turn = f"t{index}"
            self.send(turn)
            candidate = {"candidate_id": "action", "source_ref": "actual-author-selection",
                         "action_data": {"reaction": "LOVE"} if disposition == "REACT" else {"source_ref": "actual-post"}}
            self.answer(disposition, [candidate], turn)
            self.store.select_candidate("private", turn, "action", "actual-selection-message")
            packet = self.store.next_step_packet("private")
            self.assertEqual(packet["candidate"]["text"], "")
            self.assertEqual(packet["candidate"]["action_data"], candidate["action_data"])
            with self.assertRaises(StateError):
                self.store.export_to_core_request("private", "case", "rev", {})

    def test_cli_dispatcher_cannot_call_unlisted_methods_or_record_empty_evidence(self):
        result = apply_session_request(self.store, {"method": "next_step_packet", "arguments": {"session_id": "private"}})
        self.assertEqual(result["host_step"], "prepare_author_intake")
        for method in ("close", "_db", None, ["inspect"]):
            with self.assertRaises(ValidationError):
                apply_session_request(self.store, {"method": method})
        self.store.prepare_turn("private", "t1", "Exact message")
        self.store.begin_dispatch("private", "t1", "attempt")
        with self.assertRaises(ValidationError):
            self.store.record_sent("private", "t1", "message", {})
        self.assertEqual(self.store.inspect("private")["turns"][0]["state"], "in_flight")


    def test_action_bridge_preserves_selected_multimodal_payload_and_source(self):
        from relay_actions import ActionStore

        examples = [
            ("MESSAGE", "message", "A short message", {"platform_media": {"kind": "gif", "ref": "selected-gif"}}),
            ("IMAGE", "message", "", {"platform_media": {"kind": "gif", "ref": "chosen-animation"}}),
            ("REPLY", "message", "Reply in the same conversation", {}),
            ("REACT", "react", "", {"reaction": "LOVE"}),
            ("SHARE", "share", "", {"source_ref": "selected-source-post"}),
            ("SHARE_WITH_COMMENTARY", "share_with_commentary", "Exact commentary", {"source_ref": "selected-source-post"}),
            ("BLOCK", "block", "", {"rationale": "Synthetic offline test only"}),
            ("FRIEND_REQUEST", "friend_request", "", {"rationale": "Specific synthetic recipient"}),
        ]
        for index, (disposition, action, text, action_data) in enumerate(examples):
            with self.subTest(action=action, disposition=disposition):
                turn = f"choice-{index}"
                self.send(turn)
                candidate = {"candidate_id": "chosen", "text": text, "source_ref": "author-choice",
                             "action_data": action_data}
                self.answer(disposition, [candidate], turn)
                self.store.select_candidate("private", turn, "chosen", "explicit-author-selection")
                actor = {"actor_id": "example-actor", "actor_kind": "profile" if action == "friend_request" else "page",
                         "source_ref": "active-actor"}
                target = {"destination_id": "example-target", "surface": "message_thread" if action == "message" else
                          "profile" if action in {"block", "friend_request"} else "feed_item",
                          "audience": "private" if action == "message" else "public"}
                permission = {"approved": True, "granted_by": "user", "actor_id": actor["actor_id"],
                              "destination_id": target["destination_id"], "rationale": "A specific synthetic contact",
                              "source_ref": "specific-user-permission"} if action == "friend_request" else None
                packet = apply_session_request(self.store, {
                    "method": "export_to_action_request",
                    "arguments": {"session_id": "private", "action_id": f"action-{index}", "action": action,
                                  "actor": actor, "target": target,
                                  "authority": {"source_ref": "actual-user-instruction"}, "scope": "live_test",
                                  "friend_request_permission": permission},
                })
                self.assertFalse(packet["executes"])
                self.assertEqual(packet["execution_state"], "not_executed")
                request = packet["requests"][0]
                self.assertEqual(request["method"], "create_action")
                arguments = request["arguments"]
                self.assertEqual(arguments["candidate"]["action_data"], action_data)
                self.assertEqual(arguments["candidate"]["text"], text)
                self.assertEqual(arguments["candidate"]["candidate_id"], "chosen")
                self.assertEqual(arguments["selection_ref"]["turn_id"], turn)
                self.assertEqual(arguments["selection_ref"]["author_route"], "example-thread")
                self.assertEqual(arguments["scope"], "live_test")
                self.assertEqual(arguments["friend_request_permission"], permission)
                actions = ActionStore(":memory:")
                try:
                    created = actions.create_action(**arguments)
                    self.assertEqual(created["candidate"], arguments["candidate"])
                    self.assertEqual(created["selection_ref"], arguments["selection_ref"])
                    self.assertEqual(created["action"], action)
                    self.assertEqual(actions.inspect(f"action-{index}")["operations"], [])
                finally:
                    actions.close()
                with self.assertRaises(ValidationError):
                    self.store.export_to_action_request("private", "wrong", "unrelated-action", {}, {}, {})

    def test_legacy_bridge_cannot_discard_share_or_native_media_action_data(self):
        self.send()
        self.answer("SHARE_WITH_COMMENTARY", [{
            "candidate_id": "share", "text": "Exact commentary", "source_ref": "saved-choice",
            "action_data": {"source_ref": "selected-source-post"},
        }])
        self.store.select_candidate("private", "t1", "share", "explicit-selection")
        with self.assertRaisesRegex(StateError, "export_to_action_request"):
            self.store.export_to_core_request("private", "case", "rev", {})
        self.send("gif")
        self.answer("REPLY", [{
            "candidate_id": "gif", "text": "Same exact caption", "source_ref": "saved-gif-choice",
            "action_data": {"platform_media": {"kind": "gif", "ref": "selected-gif"}},
        }], "gif")
        self.store.select_candidate("private", "gif", "gif", "explicit-gif-selection")
        with self.assertRaisesRegex(StateError, "cannot discard"):
            self.store.export_to_core_request("private", "case", "rev", {})

    def test_action_bridge_requires_selection_and_preserves_exact_friend_permission(self):
        with self.assertRaises(StateError):
            self.store.export_to_action_request("private", "action", "friend_request", {}, {}, {})
        self.send()
        self.answer("FRIEND_REQUEST", [{
            "candidate_id": "request", "source_ref": "author-choice",
            "action_data": {"rationale": "Shared interest and known contact"},
        }])
        self.store.select_candidate("private", "t1", "request", "explicit-selection")
        permission = {"approved": True, "granted_by": "user", "actor_id": "example-user",
                      "destination_id": "example-target", "rationale": "Shared interest and known contact",
                      "source_ref": "specific-human-permission"}
        packet = self.store.export_to_action_request(
            "private", "request", "friend_request", {}, {}, {"source_ref": "scope"},
            scope="live_test", friend_request_permission=permission)
        self.assertEqual(packet["requests"][0]["arguments"]["friend_request_permission"], permission)
        self.store.prepare_turn("private", "later", "Please reconsider that choice")
        with self.assertRaises(StateError):
            self.store.export_to_action_request("private", "stale", "friend_request", {}, {}, {})

    def test_mixed_choices_keep_each_action_type_and_selection_provenance(self):
        from relay_actions import ActionStore

        self.send()
        candidates = [
            {"candidate_id": "haha", "disposition": "REACT", "action_data": {"reaction": "HAHA"},
             "source_ref": "author-reaction-choice"},
            {"candidate_id": "comment", "disposition": "REPLY", "text": "Hello.",
             "source_ref": "author-comment-choice"},
        ]
        self.answer("MULTIPLE", candidates)
        actor = {"actor_id": "page", "actor_kind": "page", "source_ref": "fresh-page-check"}
        actions = ActionStore(":memory:")
        try:
            for candidate, action in zip(candidates, ("react", "comment")):
                choice = candidate["candidate_id"]
                self.store.select_candidate("private", "t1", choice, f"selected-{choice}")
                packet = self.store.export_to_action_request(
                    "private", choice, action, actor,
                    {"destination_id": f"target-{choice}", "reply_parent_id": f"parent-{choice}",
                     "surface": "feed_item", "audience": "public"},
                    {"source_ref": "human-scoped-permission"})
                arguments = packet["requests"][0]["arguments"]
                created = actions.create_action(**arguments)
                self.assertEqual(created["action"], action)
                self.assertEqual(created["selection_ref"]["selection_ref"], f"selected-{choice}")
                self.assertEqual(created["selection_ref"]["candidate_source_ref"], candidate["source_ref"])
                result = self.store.record_result("private", f"result-{choice}", "verified",
                                                  "Synthetic reopened result.", f"receipt-{choice}")
                self.assertEqual(result["disposition"], candidate["disposition"])
                self.assertEqual(result["candidate"]["candidate_id"], choice)
                self.assertEqual(result["selection_ref"], f"selected-{choice}")
        finally:
            actions.close()
        self.assertEqual(self.store.inspect("private")["turns"][0]["response"]["disposition"], "MULTIPLE")

    def test_earlier_choice_survives_one_way_return_without_latest_turn_rebinding(self):
        self.selected()
        self.store.record_result("private", "first", "verified", "First choice observed.", "first-receipt")
        self.store.prepare_turn("private", "receipt", "Coda: first result.", purpose="return_result",
                                result_id="first", expects_response=False)
        self.store.begin_dispatch("private", "receipt", "receipt-attempt")
        self.store.record_sent("private", "receipt", "saved-receipt", "Reopened receipt.")
        self.store.select_candidate("private", "t1", "b", "author explicitly selected B too")
        self.store.close()
        self.store = SessionStore(self.path)

        self.assertEqual(self.store.next_step_packet("private")["host_step"], "complete_without_response")
        step = self.store.next_step_packet("private", turn_id="t1")
        self.assertEqual(step["candidate"]["candidate_id"], "b")
        self.assertEqual(step["turn"]["turn_id"], "t1")
        with self.assertRaises(StateError):
            self.store.export_to_action_request("private", "implicit", "comment", {}, {}, {})
        packet = self.store.export_to_action_request("private", "explicit", "comment", {}, {}, {}, turn_id="t1")
        provenance = packet["requests"][0]["arguments"]["selection_ref"]
        self.assertEqual(provenance["turn_id"], "t1")
        self.assertEqual(provenance["response_ref"], "saved-author-message-t1")
        self.assertEqual(provenance["selection_ref"], "author explicitly selected B too")
        result = self.store.record_result("private", "second", "verified", "Second choice observed.",
                                          "second-receipt", turn_id="t1")
        self.assertEqual(result["turn_id"], "t1")
        self.assertEqual(result["candidate"]["candidate_id"], "b")
        self.assertEqual(self.store.inspect("private")["results"][0]["candidate"]["candidate_id"], "a")

    def test_explicit_older_choice_cannot_bypass_pending_author_dispatch(self):
        self.selected()
        self.store.prepare_turn("private", "pending", "Coda: more context.")
        with self.assertRaises(StateError):
            self.store.export_to_action_request("private", "old", "comment", {}, {}, {}, turn_id="t1")
        with self.assertRaises(StateError):
            self.store.record_result("private", "wrong-turn", "verified", "Observed.", "receipt", turn_id="t1")
        self.assertEqual(self.store.next_step_packet("private", turn_id="t1")["turn"]["turn_id"], "pending")

    def test_mixed_response_requires_valid_per_candidate_dispositions(self):
        self.send()
        for disposition, candidates in (
            ("MULTIPLE", []),
            ("MULTIPLE", [self.candidate("a", "No per-candidate type")]),
            ("MULTIPLE", [self.candidate("a", "Invalid type") | {"disposition": "QUIET"}]),
            ("MULTIPLE", [self.candidate("a", "Nested type") | {"disposition": "MULTIPLE"}]),
            ("MULTIPLE", [self.candidate("a", "Malformed type") | {"disposition": []}]),
            ("REPLY", [self.candidate("a", "Conflicting type") | {"disposition": "REACT"}]),
        ):
            with self.subTest(disposition=disposition, candidates=candidates):
                with self.assertRaises(ValidationError):
                    self.answer(disposition, candidates)
        self.answer("REPLY", [self.candidate("a", "Legacy candidate.")])
        saved = self.store.inspect("private")["turns"][0]["response"]["candidates"][0]
        self.assertNotIn("disposition", saved)

    def test_mixed_core_bridge_uses_selected_type_and_explicit_original_turn(self):
        self.send()
        self.answer("MULTIPLE", [
            self.candidate("text", "Exact chosen comment.") | {"disposition": "REPLY"},
            {"candidate_id": "reaction", "disposition": "REACT", "action_data": {"reaction": "LOVE"},
             "source_ref": "author-reaction-choice"},
        ])
        self.store.select_candidate("private", "t1", "reaction", "selected-reaction")
        with self.assertRaises(StateError):
            self.store.export_to_core_request("private", "case", "r1", {})
        self.send("later")
        self.answer("REPLY", [self.candidate("text", "Different newer text.")], "later")
        self.store.select_candidate("private", "later", "text", "newer-selection")
        self.store.select_candidate("private", "t1", "text", "original-selection")
        binding = {"author_route": "example-thread", "actor_id": "example-page", "actor_kind": "page",
                   "destination_id": "example-target", "action": "reply", "context_ref": "source",
                   "reply_parent_id": "parent", "audience": "public"}
        packet = apply_session_request(self.store, {
            "method": "export_to_core_request", "arguments": {
                "session_id": "private", "case_id": "case", "revision_id": "r1",
                "binding": binding, "turn_id": "t1"}})
        revision = packet["requests"][1]["arguments"]
        self.assertEqual(revision["text"], "Exact chosen comment.")
        self.assertEqual(revision["source_ref"]["turn_id"], "t1")
        self.assertEqual(revision["source_ref"]["response_ref"], "saved-author-message-t1")
        self.assertEqual(revision["source_ref"]["selection_ref"], "original-selection")
        with self.assertRaises(NotFoundError):
            self.store.export_to_core_request("private", "case", "r1", binding, turn_id="missing")


if __name__ == "__main__":
    unittest.main()
