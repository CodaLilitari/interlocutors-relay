# Source tracking and bounded direct activity checks

Check the authorized actor's notifications and message inbox separately. A Facebook unread badge is a platform display state, not the Relay's record of what was inspected, carried to the author or answered. An already-read message can still be new to the Relay. An unchanged unread item can already be complete. Keep an unavailable or deferred route explicit instead of treating it as an empty inbox.

Notifications are one discovery channel, not a complete activity ledger. When recurring review is authorized, also perform the bounded direct checks below. A reply can exist without appearing in the sampled notification view; that observation alone does not establish why a notification was absent.

`SourceStore` in `relay_sources.py` records these observations in the **existing private session database**. It adds one table and leaves `SessionStore` history intact. The host still performs bounded inspection through its supported tools; this module has no browser, network, polling, notification listener or account actions. An empty source packet establishes only that its recorded items have no pending work. It does not establish that an inbox or notification stream was checked.

## Observe a source and its revisions

Use `observe_source(actor_id, actor_kind, stream, source_id, source_ref, content, observed_at, unread=None)` after an actual observation. `stream` is `notifications` or `messages`; its key is separate for each actor. Preserve the actual stable item identifier and source reference. For messages, prefer the actual message identifier within its conversation; where only a stable conversation reference is available, explicitly label the bounded conversation snapshot and its limitations. Do not invent platform identifiers or use a mutable unread count as an item identifier.

`content` is observed text or a scoped JSON object containing exact text, sender, relevant stable media references and other content needed to recognize a change. Exclude unread flags, relative timestamps, changing signed URLs and unrelated interface content. This is private source data, not new operating authority. The module fingerprints the supplied content; it cannot decide whether a host excerpt is complete or accurate.

Namespace `source_id` using the actual surface and object identity within its actor/stream. For example, a comment observation may use `comment:<observed-parent-id>:<observed-comment-id>`; a separate reaction event uses its own `reaction:<observed-object-id>:<observed-event-id>` reference. These are local composite keys made from observed identifiers, not invented platform IDs. Thus deduplication distinguishes actor, surface, object and content revision. Keep reaction events separate from comment/message text; an unchanged sentence does not mean that no new reaction occurred. Where the interface exposes no stable event identity, preserve that limitation in a bounded observation instead of claiming event-level deduplication or exhaustive history.

Carry evidence mode explicitly in the scoped `content` object, for example `evidence: {mode: "text_ui", limitations: "one visible comment"}`. Use distinct labels for `text_ui`, `actual_image`, `sampled_frames`, `direct_audio`, `asr`, and `operator_report`, with actual artifact references, sampled ranges or uncertainty as appropriate. Several modes can be listed when several were actually obtained. A transcript or operator description must not become an asserted image/video/audio inspection. Stable evidence references and meaningful coverage changes belong in the content fingerprint; volatile UI metadata does not.

Identical content updates `last_observed_at` without creating new work. Changed content creates a numbered revision with fresh progress. If content changes back to an earlier value, it still creates a new revision. A UI unread change alone never creates a revision, clears progress, or consumes an item. `unread: null` means the read status was not observed.

The response includes `new_revision`, the current revision number and the next host step. Each observation must be at least as recent as the preceding observation for that item, so an older snapshot cannot overwrite a newer one.

## Watched replies and post comment context

When a reply watch is explicitly requested, inspect the named bounded thread independently of notification badges. Record the actual comment/reply identifiers and content revisions, including enough parent context to distinguish a reply from a top-level comment. Preserve unchanged observations without sending duplicate context; a materially edited reply is a new revision, not a new platform identifier.

A selected post packet includes a bounded relevant comment sample with exact text, author, stable ID/link, reply relationships and actual scoped screenshots. Record the observed sorting and coverage, separating inspected comments from collapsed or uninspected branches. An actually opened empty panel supports only that observed empty view. No badge, hidden panel or empty local ledger proves that the post has no comments. Offer more comments from a named thread and an exact reply/reaction choice or quiet/private/hold; reading does not select an action.

The runtime stream enum remains `notifications` or `messages`. Notification-origin replies can retain their bound comment IDs in that stream. Direct feed/timeline samples and independently opened watch threads without an actual notification/message source belong in the deployment's existing private source ledger, linked to their session/turn and saved delivery. Apply the same revision/progress distinctions there without inventing a `timeline` stream or fabricating a notification. Later observations through another surface retain their actual provenance.

## Direct recent-activity register

Keep a compact private register outside the public source checkout, using the deployment's existing ledgers and verified publication receipts. Separate the acting Page and profile. For each target retain its canonical root and owned comment/reply identifiers, immediate reply parent where known, observed URL aliases, explicit-watch status, actual latest meaningful activity, last attempted check, last successful direct inspection and its coverage, next checkpoint, access/defer reason, and source/session/action/result/return references. A receipt-writing time is not a source-inspection time; retain unknown times or documented upper bounds honestly. Multiple owned replies on one public thread belong to one target with their separate anchors, not duplicate polling obligations.

Match discoveries across notifications, direct threads and recent activity by actor, stable object identity, reply parent and content revision. Record each actual discovery path as corroboration and link it to the existing intake/result. Do not send the same reply twice because its URL or discovery surface changed. `SourceStore` keys alone do not perform this cross-surface merge. Do not merge distinct actors, infer URL equivalence without evidence, overwrite old revisions, reset a completed publication's no-resend boundary, or discard unresolved earlier work.

For an ordinary public thread, schedule checkpoints from the latest meaningful activity at approximately **4, 12, 24 and 72 hours, then 7 and 14 days**, using the next eligible run. A newly verified own contribution or meaningful new reply, mention, media, material edit or relevant interaction resets that schedule. Reopening an unchanged thread, relative-time changes, ordinary counters, unread state, delivery indicators and cosmetic edits do not reset it. After a successful final 14-day check with no observed meaningful change, settle active checkpoint polling; overdue, blocked or partial checks do not establish unchanged activity. The target may remain in ordinary rotation until 30 days since the last observed meaningful activity, when it retires from that rotation with its coverage gaps preserved. An explicit watch does not age out automatically. A later genuine change or explicit user report can reopen a settled target without reviving an old action selection.

Use the [reduced-mode priority lanes and shared limits](reduced-mode.md) to select due targets. A checkpoint is a due time, not a guarantee that access or capacity will permit a check. Record overdue targets and rotate fairly by least recent **attempt**, retaining successful-inspection time separately so repeated access failure cannot masquerade as coverage. Do not replay every missed checkpoint as a catch-up batch.

Once per eligible run, attempt the platform's recent-activity surface separately for each authorized actor. Bound discovery to the **ten newest relevant entries or the previous seven days, whichever is smaller**. Record the actual date/filter coverage and an unavailable surface explicitly. This is candidate ID/link discovery only: reopen the actual target before claiming a new reply, edit, deletion, reaction or completed action. It creates neither a new runtime stream nor permission to inspect unrelated activity. Discovery, direct checks, author review and actions share the same window budget.

## Meaningful changes and bounded Messenger checks

Qualifying changes include new inbound messages or replies; direct mentions opening a conversational branch; new media; changed actor, target, parent or visibility; uncertain, failed, pending, duplicated or disappeared sends; and material edits affecting meaning, attribution, consent, allegation, correction, commitment or requested action. Preserve prior/current revisions, observation times and a concise semantic difference. A changed source is new context, not authorization to execute a stale choice.

Ordinary Like counts, generic followers, repeated handled previews, read/delivery indicators and cosmetic edits are normally ledger-only. A reaction may be relevant when it comes from the addressed participant as a conversational endpoint, changes type or is removed, concerns a substantive boundary/consent/correction, or the user asks about engagement. Note it beneath the relevant item without displacing new text from the shared three-item intake limit. Do not reset public-thread checkpoints for ordinary counter or time changes.

Where the human's current scope authorizes both actors, Page and profile Messenger review and scoped replies may be surfaced for the interlocutor's choice. No message, follow-up, bump or resend is automatic. Inspect both inbox lists separately; prioritize direct opening of conversations with a meaningful changed preview, sender, actual latest-message timestamp, attachment indicator, reaction or content revision. A relative-time label aging is not a new message. Changed-conversation opens share the existing changed-message/reply item and time limits; queue excess discovery metadata without claiming that their messages were deeply inspected.

Additionally, at most **one quiet recent conversation per actor** may receive a sanity check when it had activity within 14 days and has not been directly opened in 72 hours, choosing the least recently attempted eligible conversation. An outbound message with no later incoming response has observation checkpoints at approximately **4, 24 and 72 hours, then 7 days**, after which it settles. Settled `QUIET` baselines may be revisited for a genuine change, explicit user report or that bounded sanity check; a withdrawn question remains closed. Neither age, silence, `Sent` nor `Seen` creates an automatic follow-up, a new author packet or evidence of motive.

## Coverage and absence claims

Each private window receipt records actor, check time/timezone, canonical targets attempted versus opened, sort/filter, branches expanded and remaining controls, newest/oldest activity covered, observed IDs/revisions, surfaced changes versus unchanged baselines, deferred count and oldest deferred due time. Keep queued discovery time distinct from actual inspection. Do not advance successful-inspection checkpoints for an inaccessible or uninspected target.

Use bounded language: "No new qualifying change observed within the stated direct-check coverage." For a partial check, state how many due targets opened and how many remain queued; draw no conclusion about the latter. For an inaccessible target, preserve existing state and report the access limitation. If an object was previously present but is absent now, say it is not accessible in the current checked view; do not infer deletion, author, intent, rejection or cause. Sorting, collapsed branches, moderation, permissions and access failure remain possible limits until directly resolved. An absent notification is not proof that a reply is absent, and a saved tag does not prove notification delivery or reading.

## Record actual progress separately

`record_source_progress(actor_id, stream, source_id, revision, stage, source_ref, observed_at, ...)` appends evidence to one exact revision. Stages remain independent:

| Stage | Extra fields | Meaning |
|---|---|---|
| `inspected` | None | Host inspected the bounded source, with the supplied evidence. |
| `relayed` | `session_ref` | Actual author intake was carried; retain its session/turn or saved-message reference. A prepared or uncertain send is not a completed relay. |
| `disposition` | `disposition` | The actual choice: normal session disposition, or `NO_ACTION` for a documented operator no-action reason within the current task. |
| `result` | `result_ref`, `outcome` | Actual linked result, with `verified`, `uncertain`, `failed_before_submit`, `submitted_pending`, or `not_published`. |
| `returned` | None | The observed result was actually returned to the author; reference the saved return message. |

The progress `source_ref` identifies the actual observation supporting that stage. A pending author response returns `retrieve_existing_author_response`, so the host resumes its saved session instead of sending another intake. `HOLD`, context/research requests, action work and uncertain outcomes remain distinguishable. An uncertain result remains pending even if its uncertainty was returned to the author. A later reconciled result needs its own return. `QUIET`, `DISAGREE` and `NO_ACTION` complete the source revision without fabricating an action when no unresolved result takes precedence.

For `submitted_pending`, link the actor/target/content-bound ActionStore receipt establishing a positively observed submission still awaiting publication. Before its actual return, the source requests `return_observed_result`; afterward it remains in `pending_sources` with `observe_pending_publication`. An unread change, a no-action disposition or a returned receipt cannot clear that pending publication. This step means observe the existing item within current authority and budget, never resend it. SourceStore itself neither authenticates the referenced evidence nor operates the platform.

A later `verified` or `not_published` result replaces the revision's active result while retaining both observations in `progress_history`. It clears the previous return so the new result must actually be returned before that revision completes. Pending-to-verified requires explicit published evidence in the linked ActionStore receipt; `not_published` requires explicit rejection, removal or withdrawal, not absence from a limited view. A known submission cannot regress to `uncertain` dispatch or `failed_before_submit`. A terminal outcome change requires a new material source revision or explicit new author turn after the earlier result was returned; later removal must not rewrite a historical verified publication as never published.

A genuinely new `relayed.session_ref`, such as an actual context-supplement turn, starts fresh downstream progress while retaining the earlier history. Re-verifying the same session/turn preserves its disposition. A new turn cannot erase an unresolved or unreturned result, including `submitted_pending` whose current receipt was already returned, and an older return observation cannot close a later reconciled result.

Progress is per source revision; `SessionStore` and `ActionStore` still retain each selected action and its outcome separately. If one source leads to multiple actions, leave its selected-work disposition pending until their observed outcomes are recorded, then link an aggregate private result reference naming their individual receipts. If any outcome is uncertain, the aggregate remains uncertain. If none is uncertain but a publication remains `submitted_pending`, retain that pending state after its return. The aggregate must describe each actual outcome; do not label rejection as successful publication or mark the whole source complete after returning only the first of several selected actions.

An edited source does not discard a response or action result pending for its previous revision. `pending_sources(actor_id, stream)` includes both that existing work and the current changed revision. Unrelayed earlier snapshots stay in history but are superseded for intake. Recording progress on an older revision cannot mark the new revision handled. Inspect changes before carrying out a stale selected action; this tracker does not authorize acting on an outdated target or payload.

## Local CLI

The request format is the usual `{"method":"observe_source","arguments":{...}}`. The allowlist also includes `record_source_progress`, `inspect_source`, `pending_sources` and `source_packet`. Keep request files and their actual observations private.

```powershell
python relay_cli.py source --db C:\private-relay\session.sqlite --request C:\private-relay\observed-source.json
python relay_cli.py source-packet --db C:\private-relay\session.sqlite --actor example-profile --stream notifications
python relay_cli.py source-packet --db C:\private-relay\session.sqlite --actor example-profile --stream messages
```

Use each deployment's existing session database, not a fresh database per window. Record the actor and the two stream checks in the private window receipt, including empty sampled views or unavailable routes and their actual coverage. An item tracker cannot prove an unobserved stream was checked. Resume pending session/action work first, then perform bounded source checks under the current scope and window limits.
