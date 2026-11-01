# Recover a blocked author delivery

Recover the existing exchange without replacing its author, payload or destination. A valid local artifact, a working conversation reader, an accepted upload, a saved message and the interlocutor's inspection are separate observations. This procedure uses the existing `SessionStore` APIs and supported host tools; it adds no uploader, automatic retry or route-wide lock.

## Establish what actually happened

Before another send, read the current private handoff, inspect pending turns across every local session using the saved author route, and check shared delivery guards and browser ownership. Retrieve any already-sent turn's response first. Another operator, an in-flight/uncertain dispatch or a response still pending stops a new dispatch. Do not clear records to make an update or retry possible.

Keep the exact prepared message, ordered attachment references and local file hashes. A local `not_sent` record must be checked against current history before a retry in case another operator has since delivered it. An already saved message is reconciled to that message, never resent to tidy bookkeeping.

There is no list-by-route SessionStore method. The host can use this read-only inventory against the existing database, then inspect the identified sessions through the normal session workflow. It returns only IDs and states, not message bodies. Supply the actual private path and canonical route from deployment notes, and repeat for any historical route aliases recorded there. A missing database/table or invalid record is an inspection failure, not an empty queue. The snapshot is not a lock; exclusive host ownership is still required before dispatch.

```python
import json
import sqlite3
from contextlib import closing
from pathlib import Path

def route_turns(db_path, author_route):
    uri = Path(db_path).resolve(strict=True).as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute("BEGIN")  # One consistent read snapshot; never write records.
        sessions = [sid for sid, raw in db.execute(
            "SELECT session_id, document FROM relay_sessions")
            if json.loads(raw)["author_route"] == author_route]
        result = []
        for sid in sessions:
            for tid, raw in db.execute(
                "SELECT turn_id, document FROM relay_session_turns "
                "WHERE session_id=? ORDER BY ordinal", (sid,)):
                result.append((sid, tid, json.loads(raw)["state"]))
        return result  # Keep this inventory private.
```

| Actual evidence | Record and next step |
|---|---|
| Upload rejected before Send; no send operation was invoked; exact composer/attachment observation retained | Record the artifact rejection. Reconcile an already claimed turn as `not_sent`; an unclaimed turn remains prepared. Preserve its exact payload. |
| Send invoked, but result timed out or cannot be established | Preserve `uncertain` and inspect the same conversation. An empty composer or absence from a limited history view alone does not establish non-delivery. |
| Explicit service rejection, corroborated by a scoped check of the attempted turn and saved history showing it was not accepted | Reconcile that attempt as `not_sent` with both observations. If acceptance is still ambiguous, retain `uncertain`. A rate limit stops the affected route for this window. |
| Exact saved message and expected attachments observed | Record the actual saved-message reference. Attachment persistence does not establish author inspection or recipient reading. |
| Author returns an actual review | Record the reply and its specific inspection limits separately from transport. A generic acknowledgment is not proof of video, image or audio access. |

After a verified `not_sent` outcome and restored access, resume the **same turn** with `begin_dispatch` and a new unique attempt ID. Recheck the exact files and chips before one send. Do not create a duplicate media intake, silently change a frozen packet, or reuse an attempt ID. A genuinely changed payload requires an explicit revision workflow preserving the old unsent record; do not edit SQLite rows to evade the current session rules.

## Rejected uploads and safe file selection

An error such as "File is empty" does not prove a nonempty local file is corrupt. A successful `setFiles` call proves neither browser-readable bytes nor upload acceptance. Record local size/hash and applicable decoding separately from the browser's reported file size and actual byte-read result. A zero-size browser file or read error with a verified nonempty local source narrows the failure to the transfer boundary; it does not establish its cause. A reported extension permission change is a proposed repair until a fresh exact-file attempt succeeds. One failed retry is not a reason to repeat the same operation indefinitely.

If permitted, use one controlled local probe with a non-sensitive, nonempty file and an exact-path allowlist. Compare its known size/hash with bytes read by the browser on a supported local test page. The probe sends nothing to the service and changes no security controls. A policy denial stops the affected route; do not use another mechanism to bypass it.

A supported browser download may provide a working copy route. Use the exact destination returned by that operation, never an assumed Downloads or application-data path or a broad folder search. Preserve the source and privately map each allowlisted copy to its original size/hash. Keep frozen selected attachment references intact; record the alternate exact path in a private transport mapping. Identical bytes at a different path are not new media. Changed bytes, ordering or author-visible packet content require the existing explicit revision workflow. Compare browser-readable copy bytes with the source, then verify the actual packet files: one working control file does not validate the packet. If both location and creation method changed, report a verified route for those files, not a proven root cause or universal repair.

Resolve an explicit allowlist of exact absolute files before opening a chooser. Prefer a supported direct file-chooser API or exact keyboard path entry. If a native picker opens in an unexpected directory, cancel it by keyboard without taking a screenshot. Do not inspect recent files, thumbnails or unrelated directories. If the correct native window/focus cannot be established safely, stop that route and preserve the blocker.

Before Send, verify every attachment chip against the allowlist and check that no extra file is present. A filename in prose, a checksum, a local path or a successful decoder is not an attachment. After Send, bind the exact saved message and ordered attachments. Where supported, recover the saved attachment bytes and compare their size/hash with the source; otherwise record that coverage gap. Matching names or chips do not prove byte identity. Record the interlocutor's actual inspection separately from successful transport.

## Tell the interlocutor when transport is blocked

When the human delegates delivery, perform supported recovery or carry an authorized, concise status notice to the interlocutor. Do not routinely hand the upload job back to the human. Request human intervention only for an actual required decision or access step the operator cannot perform, explaining the specific blocker. If the human has rejected manual upload, preserve that instruction in the private checkpoint.

The notice contains: operator attribution; the affected pending turn; what is prepared but undelivered; the observed transport failure; the next pending step; and a clear statement that media review and any social action remain unperformed. It is a text-only operational update, not a substitute media review. An empty ASR result or sampled frame sheet retains the limits in the [video guide](video-intake.md).

Use an ordinary continuation when the session permits it: `purpose="continuation"`, `attachments=[]`, `expects_response=false`. If the frozen unsent media turn prevents preparing another turn, an authorized status notice may use a **separate local incident session in the same existing database**, explicitly linked in its context to the original session/turn and bound to the **same saved remote author route**. Keep both records in the private handoff and resume an existing notice rather than preparing another one.

This is a bookkeeping separation, not a new remote conversation or a bypass of a service restriction. `prepare_turn` blocks unfinished work within its own local session; `begin_dispatch` protects its turn. Neither globally serializes all sessions sharing an author route. The host must perform the cross-session ownership and pending-work checks above before claiming and sending the notice. The core/action shared guard is not an author-route lock.

Journal and verify the notice through the same dispatch API as any other message. A verified send closes the one-way notice without requiring an artificial reply. If the notice itself is rejected or uncertain, preserve that outcome and **do not report that the interlocutor has been informed**. Keep the original media turn pending and unchanged. An unchanged blocker does not justify a fresh notice every scheduled window.

## Route and model evidence

A browser-control timeout does not establish that a saved conversation is gone, full or disconnected. Reuse the canonical route and obtain a fresh tab binding through supported inventory if the old handle is stale. A separately authorized saved-thread reader may supply history when browser control fails; its success does not prove that uploads or sends work. Never switch tools, accounts or conversations to bypass an actual service restriction.

A native URL-confidence error concerns window or route binding, not file corruption. Establish the intended and observed window, tab and URL through supported controls before using a chooser. A foreground/background mismatch remains a hypothesis until observations corroborate it. If safe binding is unavailable or policy denies access, stop that route rather than forcing a different mechanism.

Before new authoring, verify the actual account/workspace, selected model and mode through supported metadata or visible controls. Renew this check after reconnecting or replacing the tab binding; prior settings and "no change made" are not fresh evidence. If the required controls or metadata are unavailable, record the setting as unverified and hold new authoring. Store requested and observed settings separately. Hidden/template warning text unrelated to the selected control is not by itself an observed lock or usage limit; equally, a model's self-description is not proof of the selected setting. Reconcile conflicting evidence before acting.

Create a replacement remote conversation only under the human's applicable authorization and after observing that the designated route is inaccessible. A transient control failure alone is insufficient. Preserve the historical route, session, pending operations and any unrecovered history; record the new canonical route and actual handoff delivery separately. Do not call the old conversation full unless that condition was observed. A replacement introduction is sent once, not replayed each time a window resumes.

## Recover a response omitted by a reader

A saved-thread connector may omit a completed response still visible in the conversation. Inspect the same saved route through the supported semantic browser view and, when needed, reopen it to check persistence. Preserve the actual message ID, author role, ordered text and meaningful structure, completion evidence, and the connector's coverage limitation. Label the evidence by its actual source; semantic or DOM capture is not clipboard capture unless the clipboard was used.

Reconcile that response to the existing turn without repeating its prompt. Partial, still-generating or ambiguously bound content remains pending. A completed visible response does not independently prove media inspection; retain the author's specific claims and any access limits.

## Private recovery checkpoint

Record the exact route and requested/observed model; affected session/turn and attempt IDs; immutable packet and allowlisted artifact references; upload acceptance/rejection; whether Send was invoked; actual saved-message evidence or its limits; notice state; remaining review; next step; and operator ownership. Keep source text, account IDs, paths, screenshots and receipts private.

The public repository carries the procedure and synthetic tests. An updated runbook or passing test does not establish that a live upload service has been repaired. Retain blocked, prepared, accepted, sent and inspected as distinct states.
