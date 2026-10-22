"""Local video/transcript intake packets for the existing active-session relay.

The host inspects ad labels, downloads/probes non-ad video, and runs an actual
transcription adapter. This helper hashes those files and retains provenance;
it is not a downloader, decoder, transcriber, or claim of provider video support.
SessionStore remains responsible for durable message dispatch and reconciliation.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from relay_core import StateError, ValidationError, _id, _now, _zoned_timestamp


SCHEMA = "interlocutors-relay/video-intake/v1"
ROLES = {"video", "transcript"}
METHODS = {"plan_video", "record_video", "record_transcript", "submission_packet",
           "record_delivery", "record_review", "inspect_video_intake"}


def _ref(value, label):
    if not isinstance(value, (str, dict)) or not value or (isinstance(value, str) and not value.strip()):
        raise ValidationError(f"{label} must be a nonempty reference string or object")
    return copy.deepcopy(value)


def _notes(value):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValidationError("uncertainty must be a list of nonempty descriptions")
    return list(value)


def _record(intake):
    if not isinstance(intake, dict) or intake.get("schema") != SCHEMA:
        raise ValidationError("expected a video intake record")
    return copy.deepcopy(intake)


def _gate(intake):
    record = _record(intake)
    if record["ad"]["status"] == "ad":
        raise StateError("known ad: do not download, transcribe or submit this item")
    if record["ad"]["status"] != "not_ad":
        raise StateError("inspect the source's ad status before download, transcription or submission")
    return record


def _number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError(f"{label} must be a finite number")
    if value < 0 or (positive and value == 0):
        raise ValidationError(f"{label} is outside its valid range")
    return value


def _file(path):
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ValidationError("artifact path must be an exact absolute local path")
    source = Path(path)
    try:
        before = source.stat()
        if not source.is_file():
            raise ValidationError("artifact must be a file")
        digest, size = hashlib.sha256(), 0
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(128 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        after = source.stat()
    except OSError as exc:
        raise ValidationError("artifact is unavailable") from exc
    if not size or before.st_size != size or after.st_size != size or before.st_mtime_ns != after.st_mtime_ns:
        raise ValidationError("artifact is empty or changed while being read")
    return {"path": str(source), "sha256": digest.hexdigest(), "byte_size": size}


def _verify(record, role):
    artifact = record.get(role)
    if artifact is None:
        raise StateError(f"{role} artifact has not been recorded")
    if _file(artifact["file"]["path"]) != artifact["file"]:
        raise StateError(f"selected {role} bytes changed")


def plan_video(intake_id: str, source_ref: Any, request: str, ad_status: str,
               ad_evidence: Any, ad_observation: str, observed_at: str,
               uncertainty: list[str] | None = None) -> dict[str, Any]:
    """Run before any download/transcription. Ad and unknown plans read no files."""
    _id(intake_id, "intake_id")
    _id(request, "request")
    if ad_status not in {"ad", "not_ad", "unknown"}:
        raise ValidationError("ad_status must be ad, not_ad, or unknown")
    _id(ad_observation, "ad_observation")
    _zoned_timestamp(observed_at, "observed_at")
    return {"schema": SCHEMA, "intake_id": intake_id, "created_at": _now(),
            "source_ref": _ref(source_ref, "source_ref"), "request": request,
            "ad": {"status": ad_status, "evidence": _ref(ad_evidence, "ad_evidence"),
                   "observation": ad_observation, "observed_at": observed_at},
            "uncertainty": _notes(uncertainty), "video": None, "transcript": None,
            "delivery_observations": [], "review": None,
            "next_step": {"ad": "skip_ad", "unknown": "inspect_ad_status", "not_ad": "download_and_probe_video"}[ad_status]}


def record_video(intake: dict[str, Any], path: str, acquisition_ref: Any,
                 duration_seconds: float | None, duration_source_ref: Any,
                 acquisition: str = "download", mime_type: str = "video/mp4",
                 uncertainty: list[str] | None = None, source_start_seconds: float = 0) -> dict[str, Any]:
    """Retain the actual acquired source video and measured duration provenance.

    A playback capture is labeled as such; this helper does not certify an
    original server master, a successful decode, or an audio track.
    """
    record = _gate(intake)  # Deliberately precedes all local file access.
    if record["video"] is not None:
        raise StateError("video already captured; preserve it and start a new intake for changed bytes")
    if acquisition not in {"download", "playback_capture"}:
        raise ValidationError("acquisition must be download or playback_capture")
    if not isinstance(mime_type, str) or not mime_type.startswith("video/") or not mime_type[6:]:
        raise ValidationError("mime_type must identify the probed video format")
    _number(source_start_seconds, "source_start_seconds")
    notes = _notes(uncertainty)
    if duration_seconds is None:
        if not notes:
            raise ValidationError("unknown duration requires an explicit uncertainty description")
    else:
        _number(duration_seconds, "duration_seconds", positive=True)
    record["video"] = {"file": _file(path), "acquisition": acquisition,
                       "acquisition_ref": _ref(acquisition_ref, "acquisition_ref"), "mime_type": mime_type,
                       "duration_seconds": duration_seconds, "duration_source_ref": _ref(duration_source_ref, "duration_source_ref"),
                       "source_start_seconds": source_start_seconds, "uncertainty": notes, "recorded_at": _now()}
    record["next_step"] = "transcribe_with_available_adapter"
    return record


def record_transcript(intake: dict[str, Any], path: str, segments: list[dict[str, Any]],
                      source_ref: Any, method: str, coverage: str = "full", language: str | None = None,
                      uncertainty: list[str] | None = None) -> dict[str, Any]:
    """Record actual timestamped adapter/caption/manual output, never generate it.

    Segment times refer to the captured video's timeline in seconds. A gap or
    partial transcript remains visible; a silent clip needs an actual no-speech
    observation, not fabricated dialogue. The transcript file can be TXT/VTT/SRT
    or the adapter's JSON; segment text is retained separately without rewriting.
    """
    record = _gate(intake)
    _verify(record, "video")
    if record["transcript"] is not None:
        raise StateError("transcript already captured; preserve it and create a new intake for revisions")
    _id(method, "method")
    if coverage not in {"full", "partial", "no_speech"}:
        raise ValidationError("coverage must be full, partial, or no_speech")
    notes = _notes(uncertainty)
    if not isinstance(segments, list) or (not segments and coverage != "no_speech" and not (coverage == "partial" and notes)):
        raise ValidationError("transcript requires timestamped segments, observed no_speech, or partial coverage with explicit empty-output uncertainty")
    if coverage == "no_speech" and segments:
        raise ValidationError("no_speech transcript cannot contain speech segments")
    checked, previous_start = [], -1
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValidationError("each transcript segment must be an object")
        start = _number(segment.get("start_seconds"), "segment.start_seconds")
        end = _number(segment.get("end_seconds"), "segment.end_seconds")
        if start > end or start < previous_start:
            raise ValidationError("transcript segments must have ordered, non-reversed timestamps")
        duration = record["video"]["duration_seconds"]
        if duration is not None and end > duration:
            raise ValidationError("transcript timestamp exceeds the recorded video duration")
        _id(segment.get("text"), "segment.text")
        if "uncertainty" in segment:
            _notes(segment["uncertainty"])
        checked.append(copy.deepcopy(segment))
        previous_start = start
    transcript_file = _file(path)
    try:
        transcript_bytes = Path(path).read_bytes()
        transcript_text = transcript_bytes.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError("transcript artifact must be readable UTF-8 text") from exc
    if not transcript_text.strip():
        raise ValidationError("transcript artifact must contain actual text or an explicit no-text result note")
    if hashlib.sha256(transcript_bytes).hexdigest() != transcript_file["sha256"]:
        raise StateError("transcript bytes changed during capture")
    record["transcript"] = {"file": transcript_file, "segments": checked,
                            "source_ref": _ref(source_ref, "transcript.source_ref"), "method": method,
                            "coverage": coverage, "language": language, "uncertainty": notes,
                            "timestamp_basis": "captured_video_seconds", "source_start_seconds": record["video"]["source_start_seconds"],
                            "video_sha256": record["video"]["file"]["sha256"], "recorded_at": _now()}
    record["next_step"] = "prepare_video_and_transcript_submission"
    return record


def submission_packet(intake: dict[str, Any], session_id: str, turn_id: str,
                      author_route: str, context: str = "") -> dict[str, Any]:
    """Build existing SessionStore requests, without dispatching either artifact."""
    record = _gate(intake)
    _id(session_id, "session_id")
    _id(turn_id, "turn_id")
    _id(author_route, "author_route")
    if any(event["event"] == "submitted" for event in record["delivery_observations"]):
        raise StateError("submission is already recorded; resume the existing session and reconcile, do not resend")
    _verify(record, "video")
    _verify(record, "transcript")
    attachments = [{"ref": record[role]["file"]["path"], "role": role,
                    "sha256": record[role]["file"]["sha256"], "byte_size": record[role]["file"]["byte_size"]}
                   for role in ("video", "transcript")]
    transcript = record["transcript"]
    source_label = record["source_ref"] if isinstance(record["source_ref"], str) else json.dumps(record["source_ref"], ensure_ascii=False)
    message = (
        "Coda: " + record["request"] + "\n\n"
        "Source: " + source_label + "\n"
        "The selected source video and its timestamped transcript are attached together. "
        f"Video duration: {record['video']['duration_seconds']} seconds. "
        f"Acquisition: {record['video']['acquisition']}. Transcript coverage: {transcript['coverage']}; method: {transcript['method']}.\n"
        "Please distinguish what you could inspect in the video itself from what you read in the transcript. "
        "If video inspection is available, cite relevant scene/motion observations and timestamps; "
        "otherwise say the video was unavailable or unsupported. A transcript-only review is useful but is not video inspection."
    )
    if context:
        message += "\nRelevant context: " + context
    if record["video"]["source_start_seconds"]:
        message += f"\nTranscript timestamps start at zero for this captured clip; add {record['video']['source_start_seconds']} seconds for the original source timeline."
    notes = record["uncertainty"] + record["video"]["uncertainty"] + transcript["uncertainty"]
    if notes:
        message += "\nKnown uncertainty: " + " | ".join(notes)
    return {"schema": SCHEMA + "/submission", "local_only": True, "executes": False,
            "intake_id": record["intake_id"], "attachments": attachments, "exact_message": message,
            "route_video_support": "unverified_until_observed", "requests": [
                {"method": "create_session", "arguments": {
                    "session_id": session_id, "author_route": author_route, "origin": "observed",
                    "source": {"kind": "video", "source_ref": record["source_ref"], "video_intake": record},
                    "context": context, "audience": "private interlocutor review", "request": record["request"],
                    "attachments": attachments}},
                {"method": "prepare_turn", "arguments": {"session_id": session_id, "turn_id": turn_id,
                    "exact_message": message, "attachments": attachments}},
            ]}


def record_delivery(intake: dict[str, Any], event: str, roles: list[str], evidence: Any,
                    sent_message_ref: Any = None) -> dict[str, Any]:
    """Append observed acceptance/rejection or a saved submission independently.

    Acceptance can precede send. A chip or submitted transcript cannot establish
    that the original video was sent, accepted, or inspected.
    """
    record = _gate(intake)
    if event not in {"accepted", "rejected", "submitted"}:
        raise ValidationError("event must be accepted, rejected, or submitted")
    if not isinstance(roles, list) or not roles or len(set(roles)) != len(roles) or any(role not in ROLES for role in roles):
        raise ValidationError("roles must name video and/or transcript exactly once")
    for role in roles:
        if record.get(role) is None:
            raise StateError(f"cannot record delivery of an uncaptured {role}")
    if event == "submitted":
        _ref(sent_message_ref, "sent_message_ref")
    elif sent_message_ref is not None:
        raise ValidationError("sent_message_ref belongs only to an actual submitted event")
    record["delivery_observations"].append({"event": event, "roles": roles,
        "evidence": _ref(evidence, "delivery evidence"), "sent_message_ref": sent_message_ref, "recorded_at": _now()})
    record["next_step"] = "retrieve_and_check_actual_review" if event == "submitted" else "inspect_route_and_existing_session"
    return record


def inspect_video_intake(intake: dict[str, Any]) -> dict[str, Any]:
    """Summarize independent evidence stages without claiming backend telemetry."""
    record = _record(intake)
    status = {role: {"submitted": False, "acceptance": "unknown"} for role in ROLES}
    for event in record["delivery_observations"]:
        for role in event["roles"]:
            if event["event"] == "submitted":
                status[role]["submitted"] = True
            else:
                status[role]["acceptance"] = event["event"]
    return {"intake_id": record["intake_id"], "ad_status": record["ad"]["status"],
            "next_step": record["next_step"], "delivery": status, "review": record["review"],
            "evidence_limit": "operator and author observations, not independent backend inspection telemetry",
            "local_only": True, "executes": False}


def record_review(intake: dict[str, Any], response_ref: Any, exact_response: str,
                  video_access: str, transcript_access: str, inspection_scope: str,
                  observations: list[dict[str, Any]], evidence: Any,
                  uncertainty: list[str] | None = None) -> dict[str, Any]:
    """Record the actual returned review, including unavailable video access.

    Video inspection needs accepted/submitted video and timestamped visual
    observations from the actual response. This remains an evidenced operator
    assessment; the helper cannot authenticate model-internal perception.
    """
    record = _gate(intake)
    if record["review"] is not None:
        raise StateError("review already recorded; preserve its exact source")
    _id(exact_response, "exact_response")
    if video_access not in {"inspected", "unavailable", "unknown"} or transcript_access not in {"inspected", "unavailable", "unknown"}:
        raise ValidationError("access must be inspected, unavailable, or unknown")
    if inspection_scope not in {"full", "partial", "unknown"}:
        raise ValidationError("inspection_scope must be full, partial, or unknown")
    if not isinstance(observations, list):
        raise ValidationError("observations must be a list")
    status = inspect_video_intake(record)["delivery"]
    for role, access in (("video", video_access), ("transcript", transcript_access)):
        if access == "inspected" and (not status[role]["submitted"] or status[role]["acceptance"] != "accepted"):
            raise StateError(f"cannot claim {role} inspection before observed submission and acceptance")
    if video_access == "inspected":
        if not observations:
            raise ValidationError("video inspection requires actual timestamped visual observations")
        for observation in observations:
            if not isinstance(observation, dict):
                raise ValidationError("visual observation must be an object")
            timestamp = _number(observation.get("time_seconds"), "observation.time_seconds")
            duration = record["video"]["duration_seconds"]
            if duration is not None and timestamp > duration:
                raise ValidationError("visual observation is outside the captured video")
            _id(observation.get("observation"), "visual observation")
    elif inspection_scope != "unknown":
        raise ValidationError("unavailable or unknown video requires unknown video inspection scope")
    record["review"] = {"response_ref": _ref(response_ref, "response_ref"), "exact_response": exact_response,
                        "video_access": video_access, "transcript_access": transcript_access,
                        "inspection_scope": inspection_scope, "observations": copy.deepcopy(observations),
                        "evidence": _ref(evidence, "inspection evidence"), "uncertainty": _notes(uncertainty),
                        "recorded_at": _now()}
    record["next_step"] = "review_recorded" if video_access == "inspected" else "report_video_inspection_limit"
    return record


def apply_video_request(request: dict[str, Any]) -> Any:
    if not isinstance(request, dict) or set(request) - {"method", "arguments"}:
        raise ValidationError("request must contain only method and arguments")
    method, arguments = request.get("method"), request.get("arguments", {})
    if not isinstance(method, str) or method not in METHODS or not isinstance(arguments, dict):
        raise ValidationError("unknown video method or invalid arguments")
    return globals()[method](**arguments)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="private UTF-8 JSON method/arguments request")
    args = parser.parse_args(argv)
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
        result = apply_video_request(request)
        print(json.dumps({"ok": True, "local_only": True, "result": result}, ensure_ascii=True, indent=2))
        return 0
    except (ValidationError, StateError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "local_only": True, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
