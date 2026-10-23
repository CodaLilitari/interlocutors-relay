import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import relay_update
from relay_actions import ActionStore
from relay_session import SessionStore


@unittest.skipUnless(shutil.which("git"), "Git is required for local update fixtures")
class RelayUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="relay-update-test-")
        self.base = Path(self.temp.name).resolve()
        self.origin = self.base / "origin.git"
        self.seed = self.base / "seed"
        self.repo = self.base / "installed"
        self.seed.mkdir()
        self.git(self.base, "init", "--bare", "--quiet", str(self.origin))
        self.git(self.seed, "init", "--quiet", "-b", "main")
        self.git(self.seed, "config", "user.name", "Fixture")
        self.git(self.seed, "config", "user.email", "fixture@example.invalid")
        (self.seed / "tests").mkdir()
        (self.seed / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.seed / ".gitignore").write_text("__pycache__/\n*.sqlite\nignored.txt\n", encoding="utf-8")
        (self.seed / "tests" / "test_runtime.py").write_text(
            "import unittest\nfrom runtime import VALUE\n"
            "class RuntimeTest(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertGreater(VALUE, 0)\n", encoding="utf-8")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "--quiet", "-m", "initial fixture")
        self.git(self.seed, "remote", "add", "origin", str(self.origin))
        self.git(self.seed, "push", "--quiet", "-u", "origin", "main")
        self.git(self.base, "clone", "--quiet", "--branch", "main", str(self.origin), str(self.repo))
        self.git(self.repo, "config", "user.name", "Fixture")
        self.git(self.repo, "config", "user.email", "fixture@example.invalid")
        self.old = self.git(self.repo, "rev-parse", "HEAD")
        self.session_db = self.base / "session.sqlite"
        self.delivery_db = self.base / "delivery.sqlite"
        with contextlib.closing(sqlite3.connect(self.session_db)) as db, db:
            db.execute("CREATE TABLE relay_session_turns(document TEXT)")
        with contextlib.closing(sqlite3.connect(self.delivery_db)) as db, db:
            db.execute("CREATE TABLE delivery_guard(singleton INTEGER, operation_id TEXT, state TEXT)")
        # Only URL syntax is bypassed: all actual Git checks/fetches/merges/tests run
        # against this test's local bare origin. Production CLI has no local bypass.
        self.url_check = patch("relay_update._validate_remote_url")
        self.url_check.start()

    def tearDown(self):
        self.url_check.stop()
        self.temp.cleanup()

    def git(self, where, *args):
        result = subprocess.run(["git", "-c", "core.hooksPath=", "-C", str(where), *args],
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def advance(self, value=2):
        (self.seed / "runtime.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
        self.git(self.seed, "add", "runtime.py")
        self.git(self.seed, "commit", "--quiet", "-m", "candidate fixture")
        self.git(self.seed, "push", "--quiet", "origin", "main")
        return self.git(self.seed, "rev-parse", "HEAD")

    def run_update(self, **kwargs):
        kwargs.setdefault("host_idle", True)
        return relay_update.run_update(self.repo, expected_remote=str(self.origin),
                                       session_db=self.session_db, delivery_db=self.delivery_db, **kwargs)

    def assert_preserved(self, result, status):
        self.assertEqual(result["status"], status, result)
        self.assertFalse(result["installed"])
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.old)
        self.assertEqual((self.repo / "runtime.py").read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_check_only_up_to_date_records_receipt_without_runtime_access(self):
        self.session_db.unlink()
        result = self.run_update()
        self.assertEqual(result["status"], "up_to_date")
        self.assertEqual(result["validation"], "not_run")
        self.assertEqual(result["old_revision"], self.old)
        self.assertEqual(result["new_revision"], self.old)
        self.assertTrue(result["receipt_recorded"])
        receipt = json.loads((self.repo / ".git" / "relay-update-receipts.jsonl").read_text())
        self.assertEqual(receipt["status"], "up_to_date")

    def test_check_only_available_never_fetches_installs_or_runs_candidate(self):
        candidate = self.advance(value=-1)
        result = self.run_update()
        self.assert_preserved(result, "update_available")
        self.assertEqual(result["candidate_revision"], candidate)
        self.assertEqual(result["validation"], "not_run")
        self.assertFalse((self.repo / ".git" / "FETCH_HEAD").exists())

    def test_successful_fast_forward_preserves_private_databases_and_files(self):
        candidate = self.advance()
        private = self.base / "private-note.txt"
        private.write_text("keep private", encoding="utf-8")
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (private, self.session_db, self.delivery_db)}
        result = self.run_update(apply=True)
        self.assertEqual(result["status"], "updated", result)
        self.assertEqual(result["old_revision"], self.old)
        self.assertEqual(result["new_revision"], candidate)
        self.assertEqual(result["validation"], "passed")
        self.assertTrue(result["restart_required"])
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), candidate)
        self.assertEqual({p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}, before)
        self.assertFalse((self.repo / ".git" / "relay-update.lock").exists())

    def test_failed_candidate_validation_keeps_prior_working_runtime(self):
        self.advance(value=-1)
        result = self.run_update(apply=True)
        self.assert_preserved(result, "validation_failed")
        self.assertEqual(result["validation"], "failed")

    def test_dirty_tracked_and_untracked_files_are_preserved_without_fetch(self):
        self.advance()
        (self.repo / "runtime.py").write_text("VALUE = 7\n", encoding="utf-8")
        result = self.run_update(apply=True)
        self.assertEqual(result["status"], "dirty_checkout")
        self.assertEqual((self.repo / "runtime.py").read_text(), "VALUE = 7\n")
        (self.repo / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
        untracked = self.repo / "my-draft.txt"
        untracked.write_text("preserve draft", encoding="utf-8")
        self.assert_preserved(self.run_update(apply=True), "dirty_checkout")
        self.assertEqual(untracked.read_text(), "preserve draft")
        self.assertFalse((self.repo / ".git" / "FETCH_HEAD").exists())

    def test_divergent_history_is_not_reset_or_merged(self):
        self.advance()
        (self.repo / "local.txt").write_text("local commit", encoding="utf-8")
        self.git(self.repo, "add", "local.txt")
        self.git(self.repo, "commit", "--quiet", "-m", "local change")
        self.old = self.git(self.repo, "rev-parse", "HEAD")
        self.assert_preserved(self.run_update(apply=True), "non_fast_forward")
        self.assertEqual((self.repo / "local.txt").read_text(), "local commit")

    def test_author_uncertainty_and_delivery_guard_each_prevent_apply(self):
        self.advance()
        with contextlib.closing(sqlite3.connect(self.session_db)) as db, db:
            db.execute("INSERT INTO relay_session_turns VALUES (?)", (json.dumps({"state": "uncertain"}),))
        self.assert_preserved(self.run_update(apply=True), "runtime_busy")
        with contextlib.closing(sqlite3.connect(self.session_db)) as db, db:
            db.execute("DELETE FROM relay_session_turns")
        with contextlib.closing(sqlite3.connect(self.delivery_db)) as db, db:
            db.execute("INSERT INTO delivery_guard VALUES (1,'fixture-operation','in_flight')")
        self.assert_preserved(self.run_update(apply=True), "runtime_busy")

    def test_apply_requires_existing_private_runtime_databases_outside_checkout(self):
        self.advance()
        missing = relay_update.run_update(self.repo, apply=True, host_idle=True, expected_remote=str(self.origin))
        self.assert_preserved(missing, "runtime_unavailable")
        inside = self.repo / "inside.sqlite"
        shutil.copyfile(self.session_db, inside)
        self.session_db = inside
        self.assert_preserved(self.run_update(apply=True), "runtime_unavailable")

    def test_concurrent_update_lock_is_not_removed(self):
        lock = self.repo / ".git" / "relay-update.lock"
        lock.write_text("another invocation", encoding="utf-8")
        self.assert_preserved(self.run_update(apply=True), "updater_busy")
        self.assertEqual(lock.read_text(), "another invocation")

    def test_apply_requires_explicit_idle_host_before_browser_work(self):
        self.advance()
        self.assert_preserved(self.run_update(apply=True, host_idle=False), "host_busy")
        self.assertFalse((self.repo / ".git" / "FETCH_HEAD").exists())

    def test_actual_pending_author_turn_states_each_defer_update(self):
        self.advance()
        for state in ("prepared", "not_sent", "in_flight", "uncertain", "sent"):
            with self.subTest(state=state):
                self.session_db = self.base / (state + "-session.sqlite")
                store = SessionStore(self.session_db)
                try:
                    store.create_session("fixture-session", "synthetic-author", "originated", "A pending author request.")
                    store.prepare_turn("fixture-session", "fixture-turn", "Coda: Synthetic test request.")
                    if state != "prepared":
                        store.begin_dispatch("fixture-session", "fixture-turn", "fixture-attempt")
                    if state == "not_sent":
                        store.reconcile_dispatch("fixture-session", "fixture-turn", "not_sent", "Synthetic confirmed pre-submit failure.")
                    elif state == "uncertain":
                        store.mark_uncertain("fixture-session", "fixture-turn", "Synthetic interrupted send.")
                    elif state == "sent":
                        store.record_sent("fixture-session", "fixture-turn", "synthetic-saved-message", "Synthetic saved history.")
                    self.assertEqual(store.inspect("fixture-session")["turns"][-1]["state"], state)
                finally:
                    store.close()
                self.assert_preserved(self.run_update(apply=True), "runtime_busy")

    def test_closed_receipts_and_responded_proposals_are_not_pending_sends(self):
        self.advance()
        self.session_db = self.base / "actual-session.sqlite"
        store = SessionStore(self.session_db)
        try:
            store.create_session("fixture-session", "synthetic-author", "originated", "Drafts are not dispatches.")
            store.prepare_turn("fixture-session", "receipt", "Coda: One-way receipt.", expects_response=False)
            store.begin_dispatch("fixture-session", "receipt", "receipt-attempt")
            store.record_sent("fixture-session", "receipt", "saved-receipt", "Synthetic persisted receipt.")
            self.assertEqual(store.inspect("fixture-session")["turns"][-1]["state"], "closed")
            for name, disposition in (("quiet", "QUIET"), ("held", "HOLD"), ("unselected", "REPLY"), ("selected", "REPLY")):
                store.prepare_turn("fixture-session", name, "Coda: A synthetic consultation.")
                store.begin_dispatch("fixture-session", name, name + "-attempt")
                store.record_sent("fixture-session", name, name + "-saved", "Synthetic saved history.")
                candidates = [] if disposition != "REPLY" else [{
                    "candidate_id": name + "-candidate", "text": "An unexecuted proposal.", "source_ref": "synthetic-choice"}]
                store.record_response("fixture-session", name, name + "-response", "Actual synthetic author choice.", disposition, candidates)
                if name == "selected":
                    store.select_candidate("fixture-session", name, name + "-candidate", "synthetic-selection")
        finally:
            store.close()
        self.delivery_db = self.base / "actual-delivery.sqlite"
        actions = ActionStore(self.delivery_db)
        try:
            actions.create_action("unstarted-plan", "message",
                {"actor_id": "example-profile", "actor_kind": "profile", "source_ref": "synthetic-actor"},
                {"destination_id": "example-thread", "surface": "message_thread", "audience": "private"},
                {"candidate_id": "example-candidate", "text": "An unstarted plan.", "source_ref": "synthetic-choice"},
                "synthetic-selection", {"source_ref": "synthetic-authority"})
            self.assertEqual(actions.inspect("unstarted-plan")["execution_state"], "not_started")
        finally:
            actions.close()
        result = self.run_update(apply=True)
        self.assertEqual(result["status"], "updated", result)

    def test_expected_remote_mismatch_does_not_echo_embedded_credentials(self):
        bad_url = "https://fixture-user:do-not-print@gitlab.com/example/wrong.git"
        self.git(self.repo, "remote", "set-url", "origin", bad_url)
        result = self.run_update()
        self.assertEqual(result["status"], "configuration_mismatch")
        self.assertNotIn("do-not-print", json.dumps(result))
        self.assertNotIn("do-not-print", (self.repo / ".git" / "relay-update-receipts.jsonl").read_text())

    def test_changed_checkout_during_staged_validation_is_preserved(self):
        self.advance()
        original = relay_update._candidate
        def add_user_file(*args):
            candidate = original(*args)
            (self.repo / "new-work.txt").write_text("concurrent work", encoding="utf-8")
            return candidate
        with patch("relay_update._candidate", side_effect=add_user_file):
            self.assert_preserved(self.run_update(apply=True), "dirty_checkout")
        self.assertEqual((self.repo / "new-work.txt").read_text(), "concurrent work")

    def test_remote_moving_after_advertisement_does_not_install_another_revision(self):
        self.advance()
        original = relay_update._candidate
        def move_remote(*args):
            self.advance(value=3)
            return original(*args)
        with patch("relay_update._candidate", side_effect=move_remote):
            self.assert_preserved(self.run_update(apply=True), "remote_changed")

    def test_interrupted_install_requires_inspection_not_a_blind_retry(self):
        self.advance()
        original = relay_update._git
        def interrupt_merge(where, *args, **kwargs):
            if Path(where) == self.repo and args[0] == "merge":
                raise relay_update.UpdateStopped("check_failed", "Synthetic interrupted Git operation.")
            return original(where, *args, **kwargs)
        with patch("relay_update._git", side_effect=interrupt_merge):
            result = self.run_update(apply=True)
        self.assert_preserved(result, "installation_uncertain")
        self.assertIsNone(result["new_revision"])
        self.assertTrue(result["restart_required"])

    def test_ignored_user_file_is_not_overwritten_by_candidate(self):
        (self.seed / "ignored.txt").write_text("remote candidate", encoding="utf-8")
        self.git(self.seed, "add", "-f", "ignored.txt")
        self.git(self.seed, "commit", "--quiet", "-m", "add ignored-path fixture")
        self.git(self.seed, "push", "--quiet", "origin", "main")
        (self.repo / "ignored.txt").write_text("keep local", encoding="utf-8")
        self.assert_preserved(self.run_update(apply=True), "installation_uncertain")
        self.assertEqual((self.repo / "ignored.txt").read_text(), "keep local")

    def test_busy_database_writer_blocks_final_install(self):
        self.advance()
        with contextlib.closing(sqlite3.connect(self.session_db, isolation_level=None)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            try:
                self.assert_preserved(self.run_update(apply=True), "runtime_unavailable")
            finally:
                writer.rollback()

    def test_cli_defaults_to_check_only(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = relay_update.main(["--repo", str(self.repo), "--expected-remote", str(self.origin)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["mode"], "check")


class RemoteUrlTests(unittest.TestCase):
    def test_only_credential_free_expected_gitlab_https_url_is_supported(self):
        relay_update._validate_remote_url(relay_update.DEFAULT_REMOTE)
        for url in ("https://user:token@gitlab.com/example/repo.git", "file:///tmp/repo.git",
                    "https://elsewhere.invalid/example/repo.git", "https://gitlab.com/example/repo.git?token=secret"):
            with self.subTest(url=url), self.assertRaises(relay_update.UpdateStopped):
                relay_update._validate_remote_url(url)


if __name__ == "__main__":
    unittest.main()
