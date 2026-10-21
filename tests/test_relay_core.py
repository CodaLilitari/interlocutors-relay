import tempfile
import threading
import unittest
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from relay_core import ConflictError, NotFoundError, StateError, Store, ValidationError


def future(minutes=10):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def observed_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def binding(destination="dest-1", actor_kind="page"):
    return {"author_route": "manual", "actor_id": "actor-1", "actor_kind": actor_kind,
            "destination_id": destination, "action": "comment", "context_ref": "local-context"}


def verified_evidence(store, case_id="case-1", revision_id="r1"):
    state = store.inspect(case_id)
    revision = next(item for item in state["revisions"] if item["revision_id"] == revision_id)
    return {
        "actor_id": state["binding"]["actor_id"],
        "destination_id": state["binding"]["destination_id"],
        "content_sha256": revision["content_sha256"],
        "observed_at": observed_now(),
        "observation": "operator observed the rendered result",
    }


def media_item(file_id, sha_character, alt_text):
    return {
        "file_id": file_id,
        "sha256": sha_character * 64,
        "byte_size": 123,
        "mime_type": "image/png",
        "alt_text": alt_text,
    }


class RelayCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "relay.sqlite")
        self.store.create_case("case-1", binding())

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add_and_stage(self, revision="r1", text="Hello"):
        self.store.add_revision("case-1", revision, text, "source", None if revision == "r1" else "r1")
        self.store.endorse("case-1", revision, "endorsement", text)
        self.store.stage("case-1", revision)

    def test_corrected_name_new_revision_invalidates_old_authority(self):
        self.add_and_stage()
        self.store.authorize("case-1", "r1", "auth-1", future())
        self.store.add_revision("case-1", "r2", "Hello Ada", "source-2", "r1")
        with self.assertRaises(StateError):
            self.store.begin_delivery("case-1", "r1", "op-1")
        with self.assertRaises(StateError):
            self.store.stage("case-1", "r1")
        state = self.store.inspect("case-1")
        self.assertIsNone(state["selected_revision"])
        self.assertIsNotNone(state["authorities"][0]["invalidated_at"])

    def test_branch_candidates_require_explicit_staging_selection(self):
        self.store.add_revision("case-1", "r1", "Original", "source")
        self.store.endorse("case-1", "r1", "endorsement", "Original")
        self.store.add_revision("case-1", "r2-a", "Candidate A", "source-a", "r1")
        self.store.add_revision("case-1", "r2-b", "Candidate B", "source-b", "r1")
        self.store.endorse("case-1", "r2-a", "endorse-a", "Candidate A")
        self.store.endorse("case-1", "r2-b", "endorse-b", "Candidate B")
        self.store.stage("case-1", "r2-b")
        self.store.authorize("case-1", "r2-b", "auth-b", future())
        with self.assertRaises(StateError):
            self.store.begin_delivery("case-1", "r2-a", "op-a")
        self.assertEqual(self.store.begin_delivery("case-1", "r2-b", "op-b")["revision_id"], "r2-b")

    def test_exact_text_and_transport_hashes(self):
        self.store.add_revision("case-1", "r1", "Hello\nWorld", "source")
        with self.assertRaises(ValidationError):
            self.store.endorse("case-1", "r1", "endorsement", "Hello\nWorld\n")
        self.store.endorse("case-1", "r1", "endorsement", "Hello\nWorld")
        item = self.store.inspect("case-1")["revisions"][0]
        self.assertNotEqual(item["content_sha256"], item["transport_lf_sha256"])
        with self.assertRaises(ValidationError):
            self.store.add_revision("case-1", "r2", "CRLF\r\n", "source", "r1")

    def test_viewpoint_edit_requires_new_endorsement(self):
        self.store.add_revision("case-1", "r1", "A viewpoint", "source")
        self.store.endorse("case-1", "r1", "endorsement", "A viewpoint")
        self.store.add_revision("case-1", "r2", "A revised viewpoint", "source-2", "r1")
        with self.assertRaises(StateError):
            self.store.stage("case-1", "r2")
        self.store.endorse("case-1", "r2", "endorsement-2", "A revised viewpoint")
        self.store.stage("case-1", "r2")

    def test_protected_destination_requires_page_actor(self):
        other = Store(Path(self.temp.name) / "policy.sqlite")
        try:
            policy = {"protected_destinations": {"protected": "page"}}
            with self.assertRaises(ValidationError):
                other.create_case("bad", binding("protected", "profile"), policy)
            other.create_case("good", binding("protected", "page"), policy)
        finally:
            other.close()

    def test_generic_actor_allow_and_forbid_policy_is_bound_at_case_creation(self):
        other = Store(Path(self.temp.name) / "actor-policy.sqlite")
        try:
            with self.assertRaises(ValidationError):
                other.create_case("blocked", binding(), {"forbidden_actor_ids": ["actor-1"]})
            with self.assertRaises(ValidationError):
                other.create_case("not-allowed", binding(), {"allowed_actor_ids": ["actor-2"]})
            policy = {"allowed_actor_ids": ["actor-1"]}
            other.create_case("allowed", binding(), policy)
            policy["allowed_actor_ids"].append("actor-2")
            self.assertEqual(other.inspect("allowed")["account_policy"]["allowed_actor_ids"], ["actor-1"])
        finally:
            other.close()

    def test_account_wide_singlewriter_across_two_stores(self):
        self.add_and_stage()
        self.store.authorize("case-1", "r1", "auth-1", future())
        first = self.store.begin_delivery("case-1", "r1", "op-1")
        second = Store(self.store.db_path)
        try:
            second.create_case("case-2", binding("dest-2"))
            second.add_revision("case-2", "r1", "Other", "source")
            second.endorse("case-2", "r1", "endorsement", "Other")
            second.stage("case-2", "r1")
            second.authorize("case-2", "r1", "auth-2", future())
            with self.assertRaises(StateError):
                second.begin_delivery("case-2", "r1", "op-2")
            replay = self.store.begin_delivery("case-1", "r1", "op-1")
            self.assertTrue(first["newly_claimed"])
            self.assertFalse(replay["newly_claimed"])
            self.assertEqual(replay["operation_id"], first["operation_id"])
        finally:
            second.close()

    def test_recovery_makes_crash_uncertain_and_blocks(self):
        self.add_and_stage()
        self.store.authorize("case-1", "r1", "auth-1", future())
        self.store.begin_delivery("case-1", "r1", "op-1")
        self.assertEqual(self.store.recover(), ["op-1"])
        self.assertEqual(self.store.inspect("case-1")["deliveries"][0]["state"], "uncertain")
        self.store.add_revision("case-1", "r2", "Another", "source-2", "r1")
        self.store.endorse("case-1", "r2", "endorsement-2", "Another")
        with self.assertRaises(StateError):
            self.store.stage("case-1", "r2")

    def test_authority_replay_and_expiry(self):
        self.add_and_stage()
        self.store.authorize("case-1", "r1", "auth-1", future())
        with self.assertRaises(StateError):
            self.store.authorize("case-1", "r1", "auth-2", future())
        self.store.begin_delivery("case-1", "r1", "op-1")
        self.store.record_outcome("op-1", "failed_before_submit", {"positive_no_submit": True, "observation": "operator confirmed no submit"})
        with self.assertRaises(StateError):
            self.store.begin_delivery("case-1", "r1", "op-2")
        with self.assertRaises(ValidationError):
            self.store.authorize("case-1", "r1", "auth-expired", future(-1))
        self.store.authorize("case-1", "r1", "auth-2", future())
        with self.assertRaises(ConflictError):
            self.store.authorize("case-1", "r1", "auth-2", future())

    def test_outcome_evidence_and_wrong_identifiers(self):
        with self.assertRaises(NotFoundError):
            self.store.add_revision("wrong", "r1", "x", "source")
        self.store.add_revision("case-1", "r1", "Text", "source")
        with self.assertRaises(NotFoundError):
            self.store.endorse("case-1", "wrong", "e", "Text")
        self.store.endorse("case-1", "r1", "e", "Text")
        self.store.stage("case-1", "r1")
        self.store.authorize("case-1", "r1", "auth", future())
        self.store.begin_delivery("case-1", "r1", "op")
        with self.assertRaises(ValidationError):
            self.store.record_outcome("op", "failed_before_submit", {"positive_no_submit": False})
        with self.assertRaises(ValidationError):
            self.store.record_outcome("op", "verified", {"observation": "missing delivery binding"})
        result = self.store.record_outcome("op", "verified", verified_evidence(self.store))
        self.assertEqual(result["outcome"], "verified")

    def test_mutation_after_approval_blocks_old_revision(self):
        self.add_and_stage()
        self.store.authorize("case-1", "r1", "auth", future())
        self.store.add_revision("case-1", "r2", "Mutated", "source-2", "r1")
        with self.assertRaises(StateError):
            self.store.begin_delivery("case-1", "r1", "op")

    def test_simultaneous_begin_only_creates_one_attempt(self):
        self.add_and_stage()
        self.store.authorize("case-1", "r1", "auth", future())
        peer = Store(self.store.db_path)
        outcomes = []
        errors = []
        def begin(store, operation):
            try:
                outcomes.append(store.begin_delivery("case-1", "r1", operation)["operation_id"])
            except StateError:
                outcomes.append("blocked")
            except Exception as exc:
                errors.append(exc)
        try:
            a = threading.Thread(target=begin, args=(self.store, "op-a"))
            b = threading.Thread(target=begin, args=(peer, "op-b"))
            a.start(); b.start(); a.join(); b.join()
            self.assertEqual(errors, [])
            self.assertEqual(len([item for item in outcomes if item != "blocked"]), 1)
        finally:
            peer.close()

    def test_same_operation_id_has_one_new_claimant_and_one_replay(self):
        self.add_and_stage()
        self.store.authorize("case-1", "r1", "auth", future())
        peer = Store(self.store.db_path)
        outcomes = []
        errors = []
        def begin(store):
            try:
                outcomes.append(store.begin_delivery("case-1", "r1", "shared-op")["newly_claimed"])
            except Exception as exc:
                errors.append(exc)
        try:
            a = threading.Thread(target=begin, args=(self.store,))
            b = threading.Thread(target=begin, args=(peer,))
            a.start(); b.start(); a.join(); b.join()
            self.assertEqual(errors, [])
            self.assertEqual(sorted(outcomes), [False, True])
        finally:
            peer.close()

    def test_same_store_concurrent_transactions_are_serialized_without_raw_sqlite_errors(self):
        self.add_and_stage()
        self.store.authorize("case-1", "r1", "auth", future())
        outcomes, errors = [], []
        def begin(operation):
            try:
                outcomes.append(self.store.begin_delivery("case-1", "r1", operation)["newly_claimed"])
            except StateError:
                outcomes.append(False)
            except Exception as exc:
                errors.append(exc)
        first = threading.Thread(target=begin, args=("op-a",))
        second = threading.Thread(target=begin, args=("op-b",))
        first.start(); second.start(); first.join(); second.join()
        self.assertEqual(errors, [])
        self.assertEqual(outcomes.count(True), 1)

    def test_strict_policy_schema_and_empty_allowlist(self):
        other = Store(Path(self.temp.name) / "strict-policy.sqlite")
        try:
            with self.assertRaises(ValidationError):
                other.create_case("typo", binding(), {"allowed_actor_id": ["actor-1"]})
            with self.assertRaises(ValidationError):
                other.create_case("bad-protected", binding(), {"protected_destinations": "dest-1"})
            with self.assertRaises(ValidationError):
                other.create_case("bad-ids", binding(), {"protected_destinations": {"dest-1": {"actor_ids": "actor-1"}}})
            with self.assertRaises(ValidationError):
                other.create_case("empty-deny", binding(), {"allowed_actor_ids": []})
            other.create_case("no-rule", binding(), {})
        finally:
            other.close()

    def test_revoke_authority_and_advisory_for_active_attempt(self):
        self.add_and_stage()
        self.store.authorize("case-1", "r1", "auth", future())
        self.assertEqual(self.store.revoke_authority("case-1", "operator withdrew"),
                         {"revoked": 1, "advisory_operations": []})
        with self.assertRaises(StateError):
            self.store.begin_delivery("case-1", "r1", "op")
        self.store.authorize("case-1", "r1", "auth-2", future())
        self.store.begin_delivery("case-1", "r1", "op")
        advisory = self.store.revoke_authority("case-1", {"source": "later instruction"})
        self.assertEqual(advisory["revoked"], 0)
        self.assertEqual(advisory["advisory_operations"], ["op"])
        self.assertEqual(self.store.inspect("case-1")["deliveries"][0]["state"], "in_flight")

    def test_reconcile_uncertain_with_new_observation_never_reuses_authority(self):
        self.add_and_stage()
        self.store.authorize("case-1", "r1", "auth", future())
        self.store.begin_delivery("case-1", "r1", "op")
        self.store.record_outcome("op", "uncertain", {"observation": "result not confirmed"})
        with self.assertRaises(ValidationError):
            self.store.reconcile("op", "failed_before_submit", {"positive_no_submit": True}, "audit-1")
        result = self.store.reconcile(
            "op", "failed_before_submit",
            {"positive_no_submit": True, "observation": "local audit found no submission"},
            {"source": "audit-1"},
        )
        self.assertEqual(result["outcome"], "failed_before_submit")
        with self.assertRaises(StateError):
            self.store.begin_delivery("case-1", "r1", "op-2")

    def test_invalid_scalar_source_refs_and_surrogates_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.store.add_revision("case-1", "r1", "Text", False)
        with self.assertRaises(ValidationError):
            self.store.add_revision("case-1", "r1", "Text", 0)
        with self.assertRaises(ValidationError):
            self.store.add_revision("case-1", "r1", "bad\ud800", "source")

    def test_media_payload_is_ordered_immutable_and_verified_as_a_full_payload(self):
        media = [media_item("asset-a", "a", "First image"), media_item("asset-b", "b", "Second image")]
        self.store.add_revision("case-1", "r1", "Caption", "source", media=media)
        payload_digest = self.store.inspect("case-1")["revisions"][0]["full_payload_sha256"]
        self.store.endorse("case-1", "r1", "endorsement", "Caption", payload_digest)
        self.store.stage("case-1", "r1")
        self.store.authorize("case-1", "r1", "auth", future())
        self.store.begin_delivery("case-1", "r1", "op")
        evidence = verified_evidence(self.store)
        with self.assertRaises(ValidationError):
            self.store.record_outcome("op", "verified", evidence)
        evidence["full_payload_sha256"] = self.store.inspect("case-1")["revisions"][0]["full_payload_sha256"]
        self.store.record_outcome("op", "verified", evidence)

        changed_alt = [media_item("asset-a", "a", "Changed accessibility text"), media_item("asset-b", "b", "Second image")]
        reordered = [media_item("asset-b", "b", "Second image"), media_item("asset-a", "a", "First image")]
        self.store.add_revision("case-1", "r2-alt", "Caption", "source-alt", "r1", changed_alt)
        self.store.add_revision("case-1", "r2-order", "Caption", "source-order", "r1", reordered)
        revisions = {item["revision_id"]: item for item in self.store.inspect("case-1")["revisions"]}
        self.assertNotEqual(revisions["r1"]["full_payload_sha256"], revisions["r2-alt"]["full_payload_sha256"])
        self.assertNotEqual(revisions["r1"]["full_payload_sha256"], revisions["r2-order"]["full_payload_sha256"])
        self.assertEqual(revisions["r1"]["media"], media)

    def test_empty_caption_requires_media_and_manifest_fields_are_strict(self):
        with self.assertRaises(ValidationError):
            self.store.add_revision("case-1", "r1", "", "source")
        media = [media_item("asset-a", "a", "An image")]
        self.store.add_revision("case-1", "r1", "", "source", media=media)
        self.store.endorse("case-1", "r1", "endorsement", "", self.store.inspect("case-1")["revisions"][0]["full_payload_sha256"])
        self.store.stage("case-1", "r1")
        with self.assertRaises(ValidationError):
            self.store.add_revision("case-1", "r2", "Caption", "source", "r1", media + [media_item("asset-a", "b", "Duplicate")])
        bad_path = media_item("asset-b", "b", "Other image")
        bad_path["path"] = "C:\\private\\image.png"
        with self.assertRaises(ValidationError):
            self.store.add_revision("case-1", "r2", "Caption", "source", "r1", [bad_path])
        with self.assertRaises(ValidationError):
            self.store.add_revision("case-1", "r2", "Caption", "source", "r1", [media_item("C:\\private", "b", "Other")])

    def test_caption_only_endorsement_cannot_approve_media_payload(self):
        media = [media_item("asset-a", "a", "An image")]
        self.store.add_revision("case-1", "r1", "Caption", "source", media=media)
        with self.assertRaises(ValidationError):
            self.store.endorse("case-1", "r1", "endorsement", "Caption")
        with self.assertRaises(ValidationError):
            self.store.endorse("case-1", "r1", "endorsement", "Caption", "0" * 64)
        digest = self.store.inspect("case-1")["revisions"][0]["full_payload_sha256"]
        self.store.endorse("case-1", "r1", "endorsement", "Caption", digest)
        self.store.stage("case-1", "r1")

    def test_text_only_payloads_remain_compatible_without_full_payload_outcome_field(self):
        self.add_and_stage()
        self.store.authorize("case-1", "r1", "auth", future())
        self.store.begin_delivery("case-1", "r1", "op")
        self.assertNotIn("full_payload_sha256", verified_evidence(self.store))
        self.assertEqual(self.store.record_outcome("op", "verified", verified_evidence(self.store))["outcome"], "verified")

    def test_optional_reply_parent_and_audience_bind_verified_evidence_when_present(self):
        reply_binding = binding("dest-2")
        reply_binding.update({"reply_parent_id": "parent-42", "audience": "group-members"})
        self.store.create_case("case-2", reply_binding)
        self.store.add_revision("case-2", "r1", "Reply", "source")
        self.store.endorse("case-2", "r1", "endorsement", "Reply")
        self.store.stage("case-2", "r1")
        self.store.authorize("case-2", "r1", "auth-2", future())
        self.store.begin_delivery("case-2", "r1", "op-2")
        evidence = verified_evidence(self.store, "case-2")
        with self.assertRaises(ValidationError):
            self.store.record_outcome("op-2", "verified", evidence)
        evidence.update({"reply_parent_id": "parent-42", "audience": "group-members"})
        self.assertEqual(self.store.record_outcome("op-2", "verified", evidence)["outcome"], "verified")

    def test_v1_migration_backfills_revision_digest_without_upgrading_old_attestations(self):
        self.store.close()
        legacy_path = Path(self.temp.name) / "legacy.sqlite"
        legacy = sqlite3.connect(legacy_path)
        try:
            legacy.executescript("""
                CREATE TABLE cases (case_id TEXT PRIMARY KEY, binding_json TEXT NOT NULL, policy_json TEXT NOT NULL, selected_revision TEXT, current_authority_ref TEXT, created_at TEXT NOT NULL);
                CREATE TABLE revisions (case_id TEXT NOT NULL, revision_id TEXT NOT NULL, parent_revision_id TEXT, text TEXT NOT NULL, source_ref_json TEXT NOT NULL, content_sha256 TEXT NOT NULL, transport_lf_sha256 TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(case_id,revision_id));
                CREATE TABLE endorsements (id INTEGER PRIMARY KEY, case_id TEXT NOT NULL, revision_id TEXT NOT NULL, source_ref_json TEXT NOT NULL, text_sha256 TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE authorities (authority_ref TEXT PRIMARY KEY, case_id TEXT NOT NULL, revision_id TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL, used_at TEXT, invalidated_at TEXT);
                CREATE TABLE deliveries (operation_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, revision_id TEXT NOT NULL, authority_ref TEXT NOT NULL, state TEXT NOT NULL, outcome TEXT, evidence_json TEXT, content_sha256 TEXT NOT NULL, transport_lf_sha256 TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT);
            """)
            legacy.execute("INSERT INTO cases VALUES(?,?,?,?,?,?)", ("legacy", '{"actor_id":"actor-1","destination_id":"dest-1"}', '{}', "r1", "legacy-auth", "2026-01-01T00:00:00Z"))
            legacy.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)", ("legacy", "r1", None, "Legacy", '"source"', "a" * 64, "b" * 64, "2026-01-01T00:00:00Z"))
            legacy.execute("INSERT INTO endorsements VALUES(?,?,?,?,?,?)", (1, "legacy", "r1", '"endorsement"', "a" * 64, "2026-01-01T00:00:00Z"))
            legacy.execute("INSERT INTO authorities VALUES(?,?,?,?,?,?,?)", ("legacy-auth", "legacy", "r1", "2027-01-01T00:00:00Z", "2026-01-01T00:00:00Z", None, None))
            legacy.execute("INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("legacy-op", "legacy", "r1", "legacy-auth", "verified", "verified", '{}', "a" * 64, "b" * 64, "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"))
            legacy.commit()
        finally:
            legacy.close()
        self.store = Store(legacy_path)
        state = self.store.inspect("legacy")
        self.assertEqual(state["revisions"][0]["media"], [])
        self.assertTrue(state["revisions"][0]["full_payload_sha256"])
        self.assertIsNone(state["endorsements"][0]["full_payload_sha256"])
        self.assertIsNone(state["authorities"][0]["full_payload_sha256"])
        self.assertIsNone(state["deliveries"][0]["full_payload_sha256"])
        with self.assertRaises(StateError):
            self.store.stage("legacy", "r1")
        migrated = sqlite3.connect(legacy_path)
        try:
            self.assertEqual(migrated.execute("PRAGMA user_version").fetchone()[0], 2)
        finally:
            migrated.close()


if __name__ == "__main__":
    unittest.main()
