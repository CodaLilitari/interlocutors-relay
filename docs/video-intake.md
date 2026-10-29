# Carry a source video and its transcript to the interlocutor

The active host inspects the source, downloads a non-ad video, runs an available transcription adapter, and carries the actual video and timestamped transcript to the designated saved conversation. `relay_video.py` records those artifacts and prepares existing `SessionStore` requests. It performs no download, transcription, browser operation or send, and does not assume the receiving route supports video inspection.

Keep source files, transcripts, evidence and JSON records in the private runtime directory. The returned records contain local paths and source references; they are not public release material. This helper is independent of release packaging and does not change existing session or delivery history.

## Operator sequence

1. Inspect the actual source and its surrounding labels before downloading. Call `plan_video` with the source reference, request, observed ad status, evidence, observation and zoned observation time. `ad` means skip the item: do not download, transcribe or submit it. `unknown` means inspect the source further; do not guess `not_ad`. Preserve the first observation when a later source inspection resolves it. These are source observations, not a new approval workflow.
2. For a source observed as `not_ad`, acquire the video through a supported host tool. Record the actual acquisition reference. Inspect the acquired file with an available media probe/decoder and record its measured duration and probe evidence. Call `record_video` with the exact absolute file path. It captures SHA-256 and byte size; it does not validate the video codec or claim a successful decode. Use `acquisition="playback_capture"` when that is what occurred. For an excerpt, record `source_start_seconds`; the transcript timestamps remain relative to the captured clip.
3. Run an actual local or supported transcription adapter on those selected bytes, or use observed source captions/manual transcription with clear provenance. Save its output as a nonempty UTF-8 TXT, VTT, SRT or JSON file. Call `record_transcript` with the real segments, output reference and method. The helper validates timestamp shape and range, hashes the transcript and binds it to the selected video hash. It does not invoke an ASR engine or prove transcription accuracy. Preserve adapter/model/version information, source language, omitted intervals and uncertain words in the source reference and uncertainty fields when available.
4. Call `submission_packet` for a new session/turn in the actual saved interlocutor conversation. It rechecks both file hashes and returns the exact message, two ordered attachment references, and `create_session`/`prepare_turn` requests. Those requests produce a local prepared turn only. The host uses the existing [active-session flow](active-session.md), including `begin_dispatch`, one send, actual saved-message evidence and uncertainty reconciliation.
5. Stage only the two exact allowlisted paths. Observe whether the route accepts each artifact. Record `accepted` or `rejected` independently with `record_delivery`; a chip is acceptance evidence, not proof of video inspection. The generated message says both files are attached, so send that message only when the observed attachments match it. If video is rejected, preserve the rejection and the limitation. Any separately chosen transcript-only or frame-based fallback needs accurately revised wording and attachment references; it must not be reported as original-video inspection.
6. After an actual send, record `submitted` only for the roles present in the saved message, using its real message reference. Reopen or inspect saved history when the send is ambiguous, and reconcile the existing SessionStore turn. Do not generate a second send to resolve uncertainty. The video helper's delivery records supplement SessionStore; they do not replace its durable dispatch claim.
7. Retrieve the actual reply and retain it through the existing session response flow. Call `record_review` with its exact text/reference and the evidence supporting each access claim. Video `inspected` requires observed acceptance/submission and timestamped visual observations from the actual response, such as scene changes or movement. State whether inspection was full, partial or unknown. If the author only read the transcript, preserve that useful result with `video_access="unavailable"` or `"unknown"`; do not infer visual inspection from an attachment chip or a generic acknowledgment.

Save each returned intake record as a new private checkpoint before the next step. The helper returns copied JSON records and does not persist them itself. Its acceptance/review summaries are operator assessments supported by observations, not independent telemetry about a model's internal perception.

Use [transport recovery](transport-recovery.md) when the upload route rejects an artifact, including safe exact-path file selection and a separately journaled status notice. A hash/decoder pass establishes local artifact properties, not a working uploader. Recheck attachment chips and saved delivery after any proposed repair.

Label derived media explicitly. A contact sheet contains sampled frames at stated timestamps; it is neither the source's original album stills nor continuous-sequence inspection. Caption lyrics are source text, not an audio transcript. Keep probe evidence of no audio track separate from empty ASR output on a faint nonzero track; the latter remains unresolved without further evidence. Actual source media and these derived aids may be carried together, with each role and coverage identified.

## Record fields and transcript outcomes

| Record | Required inputs and meaning |
|---|---|
| `plan_video` | `intake_id`, `source_ref`, `request`, `ad_status`, `ad_evidence`, `ad_observation`, `observed_at`; optional `uncertainty` |
| `record_video` | Prior `intake`, exact `path`, `acquisition_ref`, measured `duration_seconds`, `duration_source_ref`; optional `acquisition`, `mime_type`, `source_start_seconds`, `uncertainty` |
| `record_transcript` | Prior `intake`, exact UTF-8 `path`, actual `segments`, `source_ref`, `method`; optional `coverage`, `language`, `uncertainty` |
| `submission_packet` | Prior `intake`, new `session_id`/`turn_id`, canonical string `author_route`, optional relevant `context` |
| `record_delivery` | Prior `intake`, `event` (`accepted`, `rejected`, `submitted`), `roles` (`video` and/or `transcript`), actual `evidence`; `submitted` also needs `sent_message_ref` |
| `record_review` | Prior `intake`, `response_ref`, `exact_response`, `video_access`, `transcript_access`, `inspection_scope`, actual `observations`, `evidence`; optional `uncertainty` |
| `inspect_video_intake` | Prior `intake`; returns each role's submission/acceptance status separately from the review |

All uncertainty fields are lists of nonempty descriptions. References may be nonempty strings or structured JSON objects. Times use seconds on the captured clip; a segment has `start_seconds`, `end_seconds`, `text` and optional `uncertainty`. Starts must be ordered, times nonnegative and ends within a known duration. Visual observations have `time_seconds` and `observation`. The helper retains supplied transcript text separately from the file; the host must pass actual adapter output, not a reconstruction presented as a transcript.

`coverage="full"` describes the claimed time coverage, not a guarantee of word accuracy. Use `partial` for omitted or unresolved intervals and describe them. If actual ASR returns no text despite audio being present, record `coverage="partial"`, `segments=[]` and explicit uncertainty explaining that speech remains unresolved. Attach a UTF-8 note containing that actual result and retain the adapter evidence. `no_speech` with zero segments is for an actual no-speech observation; an empty ASR result alone does not establish it. A failed or unavailable adapter can likewise be recorded honestly as partial coverage with an explicit result note and actual failure evidence.

Unknown duration is allowed only as `null` with explicit uncertainty and a source reference for the failed/unavailable probe. Do not invent timestamps or duration. A revision to either artifact starts a new intake while retaining the previous record; bytes may not silently change under an already captured hash.

## Runnable local API and CLI

Use Python 3.11+ from the checkout. This example consumes **already acquired and measured artifacts and actual adapter output**. It does not download or transcribe. Save a private `source.json` containing arguments for `plan_video`, a `video.json` containing the arguments for `record_video` except `intake`, and a `transcript.json` containing the arguments for `record_transcript` except `intake`. The field table above and function signatures in `relay_video.py` are the contract.

```python
import json
from pathlib import Path
from relay_video import plan_video, record_video, record_transcript, submission_packet

private = Path(r"C:\private-relay\video-case")
def read(name):
    return json.loads((private / name).read_text(encoding="utf-8-sig"))
def save(name, value):
    (private / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

intake = plan_video(**read("source.json"))
save("01-source-observation.json", intake)
if intake["ad"]["status"] != "not_ad":
    raise SystemExit(intake["next_step"])

intake = record_video(intake=intake, **read("video.json"))
save("02-acquired-video.json", intake)
intake = record_transcript(intake=intake, **read("transcript.json"))
save("03-transcript.json", intake)
# route.json contains the actual session_id, turn_id, author_route and context.
packet = submission_packet(intake=intake, **read("route.json"))
save("04-submission-packet.json", packet)
for index, request in enumerate(packet["requests"], 1):
    save(f"session-request-{index}.json", request)
```

Apply the generated local requests using the same private database as the active session workflow. Replace the example paths with the actual private case directory:

```powershell
python relay_cli.py session --db C:\private-relay\delivery.sqlite --request C:\private-relay\video-case\session-request-1.json
python relay_cli.py session --db C:\private-relay\delivery.sqlite --request C:\private-relay\video-case\session-request-2.json
```

Every video helper is also callable through `python relay_video.py --request C:\private-relay\video-request.json`, with the envelope `{"method":"record_video","arguments":{...}}`. For operations after `plan_video`, include the entire prior record under `arguments.intake`; the next checkpoint is the command's `result` object, not its outer `{ok, local_only, result}` envelope. The allowlist consists of the seven methods in the table. No database path or browser credentials are accepted by this helper.

After calling `submission_packet`, inspect the native attachment route before sending. Keep the session dispatch state authoritative during interruption. After sending, `record_delivery` and `record_review` work from captured records and observed remote evidence even if local source files have subsequently become unavailable; generating a new submission packet still requires intact files and is rejected when submission is already recorded.

## Validation boundary

`python -B -m unittest discover -s tests -p test_relay_video.py -v` checks ad exclusion before file access, file continuity, timestamp/uncertainty behavior, JSON CLI use, actual SessionStore request compatibility, and separate acceptance/submission/review stages. Media bytes in these tests are synthetic continuity fixtures. The tests establish neither a working transcriber nor live download, decode, video upload or video-inspection support. A real run must retain those separate observations.
