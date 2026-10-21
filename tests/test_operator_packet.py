import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from operator_packet import (
    AUTHOR_REVIEW,
    OPERATOR_PREFLIGHT,
    PACKET_SCHEMA,
    render_operator_packet,
)
from relay_core import StateError, Store


def future(minutes=10):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def binding(**overrides):
    result = {
        "author_route": "private-author-route-secret",
        "actor_id": "page-77",
        "actor_kind": "page",
        "destination_id": "thread-88",
        "reply_parent_id": "comment-99",
        "audience": "thread-participants",
        "action": "comment",
        "context_ref": "private-context-secret",
    }
    result.update(overrides)
    return result


class OperatorPacketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "relay.sqlite")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _case(self, case_id="case-1", **binding_overrides):
        self.store.create_case(case_id, binding(**binding_overrides))
        return case_id

    def _revision(self, case_id="case-1", revision_id="r1", text="Current public payload", parent=None, media=None):
        self.store.add_revision(case_id, revision_id, text, {"private_source": f"source-{revision_id}"}, parent, media)
        revision = next(r for r in self.store.inspect(case_id)['revisions'] if r['revision_id'] == revision_id)
        self.store.endorse(case_id, revision_id, {"private_endorsement": f"endorsement-{revision_id}"}, text,
                           exact_full_payload_sha256=revision['full_payload_sha256'])

    def _stage(self, case_id="case-1", revision_id="r1"):
        self.store.stage(case_id, revision_id)

    def test_author_review_omits_secrets_and_historical_candidates(self):
        self._case()
        self._revision(text="Historical candidate carrying private-source-word")
        self._revision(revision_id="r2", text="Selected exact caption", parent="r1")
        self._stage(revision_id="r2")

        packet = render_operator_packet(self.store, "case-1", "r2", AUTHOR_REVIEW)
        rendered = json.dumps(packet, sort_keys=True)

        self.assertEqual(packet["schema"], PACKET_SCHEMA)
        self.assertEqual(packet["payload"]["text"], "Selected exact caption")
        self.assertEqual(packet["intent"], {"action": "comment"})
        self.assertNotIn("Historical candidate", rendered)
        self.assertNotIn("private-source-word", rendered)
        self.assertNotIn("private-author-route-secret", rendered)
        self.assertNotIn("private-context-secret", rendered)
        self.assertNotIn("private_source", rendered)
        self.assertNotIn("private_endorsement", rendered)
        self.assertNotIn("actor_id", packet["intent"])
        self.assertNotIn("destination_id", packet["intent"])

    def test_preflight_blocks_bad_or_incomplete_target(self):
        self._case(reply_parent_id=None)
        self._revision()
        self._stage()
        with self.assertRaisesRegex(StateError, "reply_parent_id"):
            render_operator_packet(self.store, "case-1", "r1", OPERATOR_PREFLIGHT)

    def test_preflight_requires_staged_current_revision(self):
        self._case()
        self._revision()
        with self.assertRaisesRegex(StateError, "staged"):
            render_operator_packet(self.store, "case-1", "r1", OPERATOR_PREFLIGHT)

    def test_uncertain_attempt_is_not_operator_ready(self):
        self._case()
        self._revision()
        self._stage()
        self.store.authorize("case-1", "r1", "local-auth", future())
        self.store.begin_delivery("case-1", "r1", "operation-1")
        self.store.record_outcome("operation-1", "uncertain", {"observation": "no visible result yet"})

        with self.assertRaisesRegex(StateError, "uncertain"):
            render_operator_packet(self.store, "case-1", "r1", OPERATOR_PREFLIGHT)

    def test_preflight_never_implies_local_authority_is_external_permission(self):
        self._case()
        self._revision()
        self._stage()
        self.store.authorize("case-1", "r1", "private-local-authority-ref", future())

        packet = render_operator_packet(self.store, "case-1", "r1", OPERATOR_PREFLIGHT)
        rendered = json.dumps(packet, sort_keys=True)

        self.assertTrue(packet["local_only"])
        self.assertFalse(packet["executes"])
        self.assertTrue(packet["requires_fresh_human_authorization"])
        self.assertNotIn("private-local-authority-ref", rendered)
        self.assertNotIn("authority_ref", rendered)
        self.assertEqual(packet["intent"]["reply_parent_id"], "comment-99")
        self.assertEqual(packet["intent"]["audience"], "thread-participants")

    def test_preflight_includes_only_the_public_media_manifest(self):
        media = [{
            "file_id": "asset-1",
            "sha256": "a" * 64,
            "byte_size": 1234,
            "mime_type": "image/png",
            "alt_text": "A deliberately described image",
        }]
        self._case()
        self._revision(media=media)
        self._stage()

        packet = render_operator_packet(self.store, "case-1", "r1", OPERATOR_PREFLIGHT)

        self.assertEqual(packet["payload"]["media"], media)
        self.assertIn("full_payload_sha256", packet["payload"])
        self.assertNotIn("source_ref", json.dumps(packet, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
