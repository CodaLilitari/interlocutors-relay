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


if __name__ == "__main__":
    unittest.main()
