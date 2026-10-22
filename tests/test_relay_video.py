import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from relay_core import StateError, ValidationError
from relay_session import SessionStore, apply_session_request
from relay_video import (
    apply_video_request, inspect_video_intake, main, plan_video, record_delivery,
    record_review, record_transcript, record_video, submission_packet,
)


class RelayVideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.video = self.root / "source.mp4"
        self.transcript = self.root / "transcript.txt"
        # These are byte-continuity fixtures, not asserted decodable media.
        self.video.write_bytes(b"synthetic-source-video-bytes")
        self.transcript.write_text("[0.0-2.0] A first sentence.\n[3.0-4.5] A second sentence.\n", encoding="utf-8")
        self.segments = [{"start_seconds": 0, "end_seconds": 2, "text": "A first sentence."},
                         {"start_seconds": 3, "end_seconds": 4.5, "text": "A second sentence.",
                          "uncertainty": ["A quiet word is uncertain."]}]

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, status="not_ad"):
        return plan_video("video-1", "actual-source-post", "Please consider this clip.", status,
                          "observed-source-labels", "Synthetic source/ad classification observation.",
                          "2026-10-22T20:00:00Z")

    def captured(self):
        return record_video(self.plan(), str(self.video), "actual-download-receipt", 5.0, "actual-probe-receipt")

    def ready(self):
        return record_transcript(self.captured(), str(self.transcript), self.segments,
                                 "actual-adapter-output", "external_adapter", coverage="partial", language="en",
                                 uncertainty=["Transcript was not manually verified throughout."])

    def delivered(self):
        record = self.ready()
        record = record_delivery(record, "accepted", ["video", "transcript"], "actual-attachment-staging")
        return record_delivery(record, "submitted", ["video", "transcript"], "actual-saved-history", "actual-user-message")

    def test_known_ads_are_excluded_before_file_reads_or_processing(self):
        ad = self.plan("ad")
        self.assertEqual(ad["next_step"], "skip_ad")
        with patch("relay_video._file", side_effect=AssertionError("ad file must not be read")):
            with self.assertRaisesRegex(StateError, "known ad"):
                record_video(ad, str(self.video), "download", 5, "probe")
            with self.assertRaisesRegex(StateError, "known ad"):
                record_transcript(ad, str(self.transcript), self.segments, "adapter", "external_adapter")
            with self.assertRaisesRegex(StateError, "known ad"):
                submission_packet(ad, "s1", "t1", "example-thread")
        self.assertIsNone(ad["video"])

    def test_unknown_ad_status_requires_source_review_not_a_guessed_non_ad(self):
        unknown = self.plan("unknown")
        self.assertEqual(unknown["next_step"], "inspect_ad_status")
        with patch("relay_video._file", side_effect=AssertionError("unknown item must be reviewed first")):
            with self.assertRaises(StateError):
                record_video(unknown, str(self.video), "download", 5, "probe")

    def test_video_and_timestamped_transcript_preserve_actual_provenance_and_uncertainty(self):
        record = self.ready()
        self.assertEqual(record["video"]["file"]["sha256"], hashlib.sha256(self.video.read_bytes()).hexdigest())
        self.assertEqual(record["video"]["duration_seconds"], 5)
        self.assertEqual(record["video"]["duration_source_ref"], "actual-probe-receipt")
        self.assertEqual(record["transcript"]["segments"], self.segments)
        self.assertEqual(record["transcript"]["coverage"], "partial")
        self.assertEqual(record["transcript"]["timestamp_basis"], "captured_video_seconds")
        self.assertEqual(record["transcript"]["source_ref"], "actual-adapter-output")
        self.assertEqual(record["transcript"]["video_sha256"], record["video"]["file"]["sha256"])
        self.assertEqual(record["transcript"]["file"]["sha256"], hashlib.sha256(self.transcript.read_bytes()).hexdigest())
        self.assertIsNone(record["review"])

    def test_packet_feeds_existing_session_without_claiming_video_support_or_dispatch(self):
        record = self.ready()
        packet = submission_packet(record, "s1", "t1", "example-thread", "Relevant source context.")
        self.assertFalse(packet["executes"])
        self.assertEqual(packet["route_video_support"], "unverified_until_observed")
        self.assertEqual([item["role"] for item in packet["attachments"]], ["video", "transcript"])
        self.assertIn("Transcript coverage: partial", packet["exact_message"])
        self.assertIn("Source: actual-source-post", packet["exact_message"])
        self.assertIn("Relevant source context.", packet["exact_message"])
        store = SessionStore(":memory:")
        try:
            for request in packet["requests"]:
                apply_session_request(store, request)
            state = store.inspect("s1")
            self.assertEqual(state["source"]["video_intake"]["source_ref"], "actual-source-post")
            self.assertEqual(state["turns"][0]["state"], "prepared")
            self.assertEqual(state["turns"][0]["attachments"], packet["attachments"])
            self.assertEqual(store.next_step_packet("s1")["host_step"], "begin_dispatch")
        finally:
            store.close()

    def test_excerpt_retains_original_source_timeline_offset(self):
        record = record_video(self.plan(), str(self.video), "excerpt-acquisition", 5, "probe", source_start_seconds=60)
        record = record_transcript(record, str(self.transcript), self.segments, "adapter", "external_adapter")
        self.assertEqual(record["transcript"]["source_start_seconds"], 60)
        self.assertEqual(record["transcript"]["segments"][0]["start_seconds"], 0)
        packet = submission_packet(record, "s1", "t1", "example-thread")
        self.assertIn("add 60 seconds for the original source timeline", packet["exact_message"])

    def test_changed_video_or_transcript_blocks_packet_and_preserves_prior_record(self):
        ready = self.ready()
        old = self.video.read_bytes()
        self.video.write_bytes(b"different-video")
        with self.assertRaisesRegex(StateError, "video bytes changed"):
            submission_packet(ready, "s1", "t1", "example-thread")
        self.video.write_bytes(old)
        self.transcript.write_text("Changed transcript", encoding="utf-8")
        with self.assertRaisesRegex(StateError, "transcript bytes changed"):
            submission_packet(ready, "s1", "t1", "example-thread")
        self.assertEqual(ready["delivery_observations"], [])

    def test_duration_and_transcript_timestamps_cannot_be_fabricated_from_invalid_numbers(self):
        for duration in (-1, 0, float("nan"), float("inf"), True):
            with self.subTest(duration=duration), self.assertRaises(ValidationError):
                record_video(self.plan(), str(self.video), "download", duration, "probe")
        unknown = record_video(self.plan(), str(self.video), "capture", None, "failed-duration-probe",
                               acquisition="playback_capture", uncertainty=["Duration probe was unavailable."])
        self.assertIsNone(unknown["video"]["duration_seconds"])
        self.assertEqual(unknown["video"]["acquisition"], "playback_capture")
        invalid = [[{"start_seconds": 0, "end_seconds": 6, "text": "Outside duration"}],
                   [{"start_seconds": 2, "end_seconds": 1, "text": "Reversed"}],
                   [{"start_seconds": float("nan"), "end_seconds": 1, "text": "Unknown time"}],
                   list(reversed(self.segments)), []]
        for segments in invalid:
            with self.subTest(segments=segments), self.assertRaises(ValidationError):
                record_transcript(self.captured(), str(self.transcript), segments, "adapter", "external_adapter")

    def test_no_speech_is_explicit_observation_not_empty_invented_dialogue(self):
        self.transcript.write_text("No intelligible speech was detected in the inspected interval.\n", encoding="utf-8")
        record = record_transcript(self.captured(), str(self.transcript), [], "actual-audio-inspection",
                                   "manual_audio_review", coverage="no_speech",
                                   uncertainty=["Background sound is not transcribed as speech."])
        self.assertEqual(record["transcript"]["segments"], [])
        self.assertEqual(record["transcript"]["coverage"], "no_speech")

    def test_empty_asr_result_preserves_uncertainty_without_claiming_no_speech(self):
        self.transcript.write_text("ASR returned no text. Audio is present; speech remains unresolved.\n", encoding="utf-8")
        record = record_transcript(self.captured(), str(self.transcript), [], "actual-empty-adapter-result",
                                   "external_adapter", coverage="partial",
                                   uncertainty=["Audio is nonzero but ASR returned no text; speech presence is unresolved."])
        self.assertEqual(record["transcript"]["coverage"], "partial")
        self.assertEqual(record["transcript"]["segments"], [])
        packet = submission_packet(record, "s1", "t1", "example-thread")
        self.assertIn("speech presence is unresolved", packet["exact_message"])
        with self.assertRaises(ValidationError):
            record_transcript(self.captured(), str(self.transcript), [], "adapter", "external_adapter", coverage="partial")

    def test_transcript_attachment_must_be_actual_readable_text(self):
        self.transcript.write_bytes(b"\x80\x81\xffbinary")
        with self.assertRaisesRegex(ValidationError, "UTF-8"):
            record_transcript(self.captured(), str(self.transcript), self.segments, "adapter", "external_adapter")

    def test_submitted_accepted_and_inspected_are_independent_observations(self):
        record = record_delivery(self.ready(), "accepted", ["video", "transcript"], "actual-chips")
        status = inspect_video_intake(record)
        self.assertFalse(status["delivery"]["video"]["submitted"])
        self.assertEqual(status["delivery"]["video"]["acceptance"], "accepted")
        self.assertIsNone(status["review"])
        with self.assertRaises(StateError):
            record_review(record, "author-response", "I saw it.", "inspected", "unknown", "partial",
                          [{"time_seconds": 1, "observation": "An actual visual detail."}], "actual-review-evidence")
        record = record_delivery(record, "submitted", ["video", "transcript"], "saved-history", "user-message")
        with self.assertRaisesRegex(StateError, "do not resend"):
            submission_packet(record, "duplicate", "duplicate-turn", "example-thread")
        with self.assertRaises(ValidationError):
            record_review(record, "author-response", "I saw it.", "inspected", "inspected", "full", [], "review")
        reviewed = record_review(record, "author-response", "At 1 second the object moves left.",
                                  "inspected", "inspected", "partial",
                                  [{"time_seconds": 1, "observation": "The object moves left."}], "actual-image-specific-response",
                                  uncertainty=["Only the cited interval was specifically described."])
        self.assertEqual(reviewed["review"]["video_access"], "inspected")
        self.assertEqual(reviewed["review"]["inspection_scope"], "partial")
        self.assertIsNone(record["review"])

    def test_video_rejection_can_preserve_transcript_review_without_claiming_visual_access(self):
        record = record_delivery(self.ready(), "rejected", ["video"], "actual-unsupported-format-message")
        record = record_delivery(record, "accepted", ["transcript"], "actual-transcript-chip")
        record = record_delivery(record, "submitted", ["transcript"], "actual-saved-transcript", "actual-user-message")
        record = record_review(record, "actual-author-response", "I can read the transcript but cannot inspect this video.",
                                "unavailable", "inspected", "unknown", [], "actual-limit-response")
        status = inspect_video_intake(record)
        self.assertFalse(status["delivery"]["video"]["submitted"])
        self.assertEqual(status["delivery"]["video"]["acceptance"], "rejected")
        self.assertEqual(status["review"]["video_access"], "unavailable")
        self.assertEqual(status["next_step"], "report_video_inspection_limit")

    def test_json_cli_is_runnable_and_uses_explicit_method_allowlist(self):
        request = self.root / "request.json"
        request.write_text(json.dumps({"method": "plan_video", "arguments": {
            "intake_id": "example-video", "source_ref": "source-post", "request": "Inspect the clip",
            "ad_status": "ad", "ad_evidence": "sponsored-label", "ad_observation": "Ad label is visible.",
            "observed_at": "2026-10-22T20:00:00Z"}}), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--request", str(request)]), 0)
        self.assertEqual(json.loads(output.getvalue())["result"]["next_step"], "skip_ad")
        for method in ("_file", "open", ["plan_video"]):
            with self.assertRaises(ValidationError):
                apply_video_request({"method": method, "arguments": {}})


if __name__ == "__main__":
    unittest.main()
