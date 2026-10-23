import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from relay_cli import main
from relay_core import NotFoundError, StateError, ValidationError
from relay_session import SessionStore
from relay_sources import SourceStore, apply_source_request


class RelaySourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "session.sqlite"
        self.store = SourceStore(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def observe(self, text="Question?", *, actor="example-profile", kind="profile", stream="messages", unread=False, minute=0):
        return self.store.observe_source(actor, kind, stream, "observed-item", "observed-source-ref",
                                         {"text": text}, f"2026-10-23T15:{minute:02d}:00Z", unread)

    def progress(self, stage, revision=1, minute=1, **fields):
        return self.store.record_source_progress("example-profile", "messages", "observed-item", revision,
                                                  stage, "actual-progress-evidence", f"2026-10-23T15:{minute:02d}:00Z", **fields)

    def pending(self):
        return self.store.pending_sources("example-profile", "messages")

    def test_ui_read_item_is_new_and_unread_toggle_never_consumes_or_repeats_work(self):
        first = self.observe(unread=False)
        self.assertTrue(first["new_revision"])
        self.assertEqual(self.pending()[0]["host_step"], "inspect_source")
        self.progress("inspected")
        self.progress("relayed", session_ref={"session_id": "s", "turn_id": "t"}, minute=2)
        second = self.observe(unread=True, minute=3)
        self.assertFalse(second["new_revision"])
        self.assertEqual(second["revision"], 1)
        self.assertEqual(self.pending()[0]["host_step"], "retrieve_existing_author_response")
        self.progress("disposition", disposition="QUIET", minute=4)
        self.observe(unread=False, minute=5)
        self.assertEqual(self.pending(), [])

    def test_content_change_and_change_back_are_new_revisions_with_independent_progress(self):
        self.observe()
        self.progress("disposition", disposition="NO_ACTION")
        changed = self.observe("Edited question?", minute=2)
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(changed["host_step"], "inspect_source")
        self.progress("disposition", revision=2, disposition="QUIET", minute=3)
        reverted = self.observe("Question?", minute=4)
        self.assertEqual(reverted["revision"], 3)
        self.assertEqual(reverted["record"]["revisions"][0]["fingerprint"], reverted["record"]["revisions"][2]["fingerprint"])
        self.assertEqual(reverted["host_step"], "inspect_source")

    def test_actor_and_stream_keys_are_independent(self):
        self.observe()
        self.observe(stream="notifications")
        self.observe(actor="example-page", kind="page")
        self.progress("disposition", disposition="NO_ACTION")
        self.assertEqual(self.pending(), [])
        self.assertEqual(len(self.store.pending_sources("example-profile", "notifications")), 1)
        self.assertEqual(len(self.store.pending_sources("example-page", "messages")), 1)

    def test_pending_author_reply_survives_source_edit_and_old_completion_does_not_consume_edit(self):
        self.observe()
        self.progress("relayed", session_ref={"session_id": "s", "turn_id": "t"})
        self.observe("An edit while the author is answering", minute=2)
        pending = self.pending()
        self.assertEqual([item["revision"]["revision"] for item in pending], [1, 2])
        self.assertEqual([item["host_step"] for item in pending], ["retrieve_existing_author_response", "inspect_source"])
        self.progress("disposition", revision=1, disposition="QUIET", minute=3)
        self.assertEqual([item["revision"]["revision"] for item in self.pending()], [2])

    def test_unrelayed_old_context_is_superseded_without_duplicating_pending_items(self):
        self.observe()
        self.progress("inspected")
        self.observe("Edited before intake", minute=2)
        self.assertEqual([item["revision"]["revision"] for item in self.pending()], [2])
        self.assertIn("inspected", self.store.inspect_source("example-profile", "messages", "observed-item")["revisions"][0]["progress"])

    def test_hold_pending_action_result_and_return_remain_distinct(self):
        self.observe()
        self.progress("inspected")
        self.progress("relayed", session_ref="observed-session-turn", minute=2)
        self.progress("disposition", disposition="HOLD", minute=3)
        self.assertEqual(self.pending()[0]["host_step"], "respect_hold")
        self.progress("disposition", disposition="REPLY", minute=4)
        self.assertEqual(self.pending()[0]["host_step"], "resume_selected_work")
        self.progress("result", result_ref="observed-session-result", outcome="uncertain", minute=5)
        self.progress("returned", minute=6)
        self.assertEqual(self.pending()[0]["host_step"], "reconcile_before_retry")
        self.progress("result", result_ref="reconciled-session-result", outcome="verified", minute=7)
        self.assertEqual(self.pending()[0]["host_step"], "return_observed_result")
        self.progress("returned", minute=8)
        self.assertEqual(self.pending(), [])
        before = self.store.inspect_source("example-profile", "messages", "observed-item")
        self.progress("result", result_ref="reconciled-session-result", outcome="verified", minute=7)
        after = self.store.inspect_source("example-profile", "messages", "observed-item")
        self.assertEqual(before, after)
        self.assertIn("inspected", after["revisions"][0]["progress"])
        self.assertIn("relayed", after["revisions"][0]["progress"])

    def test_reopen_persists_tracking_in_the_existing_session_database(self):
        sessions = SessionStore(self.path)
        sessions.create_session("existing", "synthetic-route", "originated", "A private question")
        self.observe()
        self.progress("relayed", session_ref={"session_id": "existing", "turn_id": "t"})
        self.store.close()
        self.store = SourceStore(self.path)
        self.assertEqual(self.pending()[0]["host_step"], "retrieve_existing_author_response")
        self.assertEqual(sessions.inspect("existing")["request"], "A private question")
        sessions.close()

    def test_new_author_turn_resumes_response_but_same_turn_reobservation_retains_disposition(self):
        self.observe()
        self.progress("relayed", session_ref={"session_id": "s", "turn_id": "first"})
        self.progress("disposition", disposition="NEED_CONTEXT", minute=2)
        self.progress("relayed", session_ref={"session_id": "s", "turn_id": "first"}, minute=3)
        self.assertEqual(self.pending()[0]["host_step"], "gather_requested_context")
        self.progress("relayed", session_ref={"session_id": "s", "turn_id": "with-context"}, minute=4)
        item = self.pending()[0]
        self.assertEqual(item["host_step"], "retrieve_existing_author_response")
        self.assertNotIn("disposition", item["revision"]["progress"])
        self.assertTrue(any(event.get("disposition") == "NEED_CONTEXT" for event in item["revision"]["progress_history"]))

    def test_new_author_turn_cannot_hide_unresolved_or_unreturned_result(self):
        self.observe()
        self.progress("relayed", session_ref="first-turn")
        self.progress("result", result_ref="uncertain-result", outcome="uncertain", minute=2)
        self.progress("returned", minute=3)
        with self.assertRaises(StateError):
            self.progress("relayed", session_ref="new-turn", minute=4)
        self.assertEqual(self.pending()[0]["host_step"], "reconcile_before_retry")
        self.progress("result", result_ref="verified-result", outcome="verified", minute=5)
        with self.assertRaises(StateError):
            self.progress("relayed", session_ref="new-turn", minute=6)
        self.progress("returned", minute=7)
        self.progress("relayed", session_ref="new-turn", minute=8)
        item = self.pending()[0]
        self.assertEqual(item["host_step"], "retrieve_existing_author_response")
        self.assertNotIn("result", item["revision"]["progress"])
        self.assertTrue(any(event.get("outcome") == "uncertain" for event in item["revision"]["progress_history"]))

    def test_old_return_cannot_close_a_later_reconciled_result(self):
        self.observe()
        self.progress("result", result_ref="uncertain-result", outcome="uncertain", minute=5)
        self.progress("returned", minute=6)
        self.progress("result", result_ref="reconciled-result", outcome="verified", minute=7)
        with self.assertRaises(StateError):
            self.progress("returned", minute=6)
        self.assertEqual(self.pending()[0]["host_step"], "return_observed_result")
        self.progress("returned", minute=8)
        self.assertEqual(self.pending(), [])

    def test_returned_pending_publication_stays_pending_across_reopen_and_unread_changes(self):
        self.observe()
        self.progress("relayed", session_ref="selected-author-turn")
        self.progress("result", result_ref="visible-pending-result", outcome="submitted_pending", minute=2)
        self.assertEqual(self.pending()[0]["host_step"], "return_observed_result")
        self.progress("returned", minute=3)
        self.assertEqual(self.pending()[0]["host_step"], "observe_pending_publication")
        before = self.store.inspect_source("example-profile", "messages", "observed-item")
        self.progress("result", result_ref="visible-pending-result", outcome="submitted_pending", minute=2)
        self.assertEqual(self.store.inspect_source("example-profile", "messages", "observed-item"), before)
        self.observe(unread=True, minute=4)
        self.store.close()
        self.store = SourceStore(self.path)
        packet = self.store.source_packet("example-profile", "messages")
        self.assertFalse(packet["executes"])
        self.assertEqual(len(packet["items"]), 1)
        self.assertEqual(packet["items"][0]["host_step"], "observe_pending_publication")
        self.assertEqual(packet["items"][0]["revision"]["progress"]["result"]["outcome"], "submitted_pending")

    def test_new_author_turn_cannot_erase_returned_pending_publication(self):
        self.observe()
        self.progress("relayed", session_ref="selected-author-turn")
        self.progress("result", result_ref="visible-pending-result", outcome="submitted_pending", minute=2)
        self.progress("returned", minute=3)
        before = self.store.inspect_source("example-profile", "messages", "observed-item")
        with self.assertRaises(StateError):
            self.progress("relayed", session_ref="new-turn", minute=4)
        self.assertEqual(self.store.inspect_source("example-profile", "messages", "observed-item"), before)
        self.progress("relayed", session_ref="selected-author-turn", minute=4)
        self.assertEqual(self.pending()[0]["host_step"], "observe_pending_publication")
        self.progress("disposition", disposition="NO_ACTION", minute=5)
        self.assertEqual(self.pending()[0]["host_step"], "observe_pending_publication")

    def test_pending_publication_cannot_regress_to_unsubmitted_or_uncertain(self):
        self.observe()
        self.progress("result", result_ref="visible-pending-result", outcome="submitted_pending", minute=2)
        self.progress("returned", minute=3)
        before = self.store.inspect_source("example-profile", "messages", "observed-item")
        for outcome in ("failed_before_submit", "uncertain"):
            with self.subTest(outcome=outcome), self.assertRaises(StateError):
                self.progress("result", result_ref="regressive-result", outcome=outcome, minute=4)
            self.assertEqual(self.store.inspect_source("example-profile", "messages", "observed-item"), before)

    def test_pending_reobservation_needs_new_return_without_losing_earlier_receipt(self):
        self.observe()
        self.progress("result", result_ref="first-pending-observation", outcome="submitted_pending", minute=2)
        self.progress("returned", minute=3)
        self.progress("result", result_ref="later-pending-observation", outcome="submitted_pending", minute=4)
        self.assertEqual(self.pending()[0]["host_step"], "return_observed_result")
        self.progress("returned", minute=5)
        revision = self.pending()[0]["revision"]
        self.assertEqual(self.pending()[0]["host_step"], "observe_pending_publication")
        self.assertEqual([event["result_ref"] for event in revision["progress_history"] if event["stage"] == "result"],
                         ["first-pending-observation", "later-pending-observation"])
        self.assertEqual(len([event for event in revision["progress_history"] if event["stage"] == "returned"]), 2)

    def test_resolved_pending_publication_requires_fresh_return_and_preserves_new_material_context(self):
        for terminal in ("verified", "not_published"):
            with self.subTest(terminal=terminal):
                source = SourceStore(Path(self.temp.name) / f"{terminal}.sqlite")
                try:
                    source.observe_source("example-profile", "profile", "messages", "item", "source", "first", "2026-10-23T15:00:00Z")
                    def progress(stage, minute, **fields):
                        return source.record_source_progress("example-profile", "messages", "item", 1, stage,
                                                             "observed-evidence", f"2026-10-23T15:{minute:02d}:00Z", **fields)
                    progress("relayed", 1, session_ref="selected-turn")
                    progress("result", 2, result_ref="pending-observation", outcome="submitted_pending")
                    progress("returned", 3)
                    source.observe_source("example-profile", "profile", "messages", "item", "new-source", "new material reply", "2026-10-23T15:04:00Z")
                    pending = source.pending_sources("example-profile", "messages")
                    self.assertEqual([item["host_step"] for item in pending], ["observe_pending_publication", "inspect_source"])
                    progress("result", 5, result_ref="terminal-observation", outcome=terminal)
                    pending = source.pending_sources("example-profile", "messages")
                    self.assertEqual([item["host_step"] for item in pending], ["return_observed_result", "inspect_source"])
                    with self.assertRaises(StateError):
                        progress("returned", 3)
                    with self.assertRaises(StateError):
                        progress("relayed", 6, session_ref="new-author-turn")
                    progress("returned", 7)
                    pending = source.pending_sources("example-profile", "messages")
                    self.assertEqual([item["revision"]["revision"] for item in pending], [2])
                    self.assertEqual(pending[0]["revision"]["content"], "new material reply")
                    saved = source.inspect_source("example-profile", "messages", "item")["revisions"]
                    self.assertEqual(saved[0]["progress"]["result"]["outcome"], terminal)
                    self.assertEqual([event["outcome"] for event in saved[0]["progress_history"] if event["stage"] == "result"],
                                     ["submitted_pending", terminal])
                    self.assertEqual(saved[1]["progress"], {})
                finally:
                    source.close()

    def test_terminal_publication_result_cannot_reopen_without_new_author_turn(self):
        self.observe()
        self.progress("relayed", session_ref="first-turn")
        self.progress("result", result_ref="visible-rejection", outcome="not_published", minute=2)
        self.progress("returned", minute=3)
        self.assertEqual(self.pending(), [])
        before = self.store.inspect_source("example-profile", "messages", "observed-item")
        for outcome in ("submitted_pending", "uncertain", "failed_before_submit", "verified"):
            with self.subTest(outcome=outcome), self.assertRaises(StateError):
                self.progress("result", result_ref="reopened-result", outcome=outcome, minute=4)
            self.assertEqual(self.store.inspect_source("example-profile", "messages", "observed-item"), before)
        self.progress("relayed", session_ref="explicit-new-author-turn", minute=5)
        self.progress("result", result_ref="new-turn-pending-result", outcome="submitted_pending", minute=6)
        self.progress("returned", minute=7)
        self.assertEqual(self.pending()[0]["host_step"], "observe_pending_publication")
        history = self.pending()[0]["revision"]["progress_history"]
        self.assertEqual([event["outcome"] for event in history if event["stage"] == "result"],
                         ["not_published", "submitted_pending"])

    def test_fingerprint_uses_content_not_object_order_or_source_reference(self):
        first = self.store.observe_source("example-profile", "profile", "messages", "item", "ref-a",
                                         {"text": "Hello", "media": []}, "2026-10-23T15:00:00Z")
        second = self.store.observe_source("example-profile", "profile", "messages", "item", "ref-b",
                                          {"media": [], "text": "Hello"}, "2026-10-23T15:01:00Z")
        self.assertTrue(first["new_revision"])
        self.assertFalse(second["new_revision"])

    def test_invalid_or_stale_progress_does_not_change_records(self):
        self.observe(minute=2)
        with self.assertRaises(StateError):
            self.observe("Old observation", minute=1)
        with self.assertRaises(ValidationError):
            self.observe(kind="page", minute=3)
        with self.assertRaises(StateError):
            self.progress("inspected", minute=1)
        with self.assertRaises(NotFoundError):
            self.progress("inspected", revision=2, minute=3)
        with self.assertRaises(ValidationError):
            self.progress("relayed", minute=3)
        with self.assertRaises(StateError):
            self.progress("returned", minute=3)
        self.progress("disposition", disposition="HOLD", minute=5)
        with self.assertRaises(StateError):
            self.progress("disposition", disposition="QUIET", minute=4)
        self.assertEqual(self.pending()[0]["host_step"], "respect_hold")
        for method in ("close", "_db", ["observe_source"]):
            with self.subTest(method=method), self.assertRaises(ValidationError):
                apply_source_request(self.store, {"method": method})

    def test_source_cli_packet_is_scoped_and_reports_only_observed_coverage(self):
        request = Path(self.temp.name) / "source.json"
        request.write_text(json.dumps({"method": "observe_source", "arguments": {
            "actor_id": "example-page", "actor_kind": "page", "stream": "notifications",
            "source_id": "observed-notification", "source_ref": "observed-control",
            "content": {"text": "A comment appeared"}, "observed_at": "2026-10-23T15:00:00Z", "unread": False,
        }}), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["source", "--db", str(self.path), "--request", str(request)]), 0)
        self.assertTrue(json.loads(output.getvalue())["result"]["new_revision"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["source-packet", "--db", str(self.path), "--actor", "example-page", "--stream", "notifications"]), 0)
        packet = json.loads(output.getvalue())["result"]
        self.assertFalse(packet["executes"])
        self.assertEqual(packet["items"][0]["host_step"], "inspect_source")
        self.assertIn("previously observed", packet["coverage"])


if __name__ == "__main__":
    unittest.main()
