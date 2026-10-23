# Capability and release checklist

This is a development checklist, not a certification.

The current source is **v0.3.0 development beta**. This release adds an action companion and its [supported-tool workflow](actions.md). The active-session agent uses supported host tools for live work; the Python modules remain local state and packet utilities. See [the session workflow](active-session.md) for the complete operating sequence and [validation](validation.md) for the bounded live observations.

The October 23 source follow-up adds [source progress tracking](source-tracking.md), mixed action choices with explicit earlier-turn bridges, the [idle-startup GitLab updater](updates.md), pending-publication handling, combined result receipts and distinct Page Like choices, building on the [reduced-mode runbook](reduced-mode.md). The current source allowlist contains 50 files. **Integrated source and clean-export validation: each ran 174 tests, with 173 passed and one Windows symlink fixture skipped.** The final documentation-only receipt update leaves the tested runtime and tests unchanged; final export verifies their byte identity and packages 50 source files plus the integrity manifest. The historical release counts and pending-test wording below describe the frozen v0.3.0 archive's earlier checkpoint. This follow-up does not change that archive, configure a timer or add an unattended social client.

## Implemented local components

- Standard-library Python state store with SQLite transactions and explicit revisions.
- Exact text and distinct content/transport digests.
- Immutable actor/target bindings and configurable actor exclusions.
- Local authority consumption, revocation, replay handling, intent and uncertainty records.
- A synthetic demo and unit/fault tests; see the current validation receipt for executed results.
- A reusable operator skill and manual relay packet.
- An additive `SessionStore` for source/context intake, private and originated work, dispositions, durable message dispatch, response capture, explicit selection, result reconciliation and return/follow-up packets.
- Mixed `MULTIPLE` responses with per-candidate output dispositions, separate action selection and original-turn provenance; explicit earlier-turn export/result APIs preserve the response behind a choice after a later receipt.
- `SourceStore`, `source` and `source-packet` retain separate actor/stream observations, content revisions and inspection/relay/disposition/result/return progress. UI unread state is observed separately; edits preserve earlier relayed work until it settles.
- `session` and `session-packet` CLI commands, including the next-step packet used after interruption. A bridge emits selected text/media requests for the existing core without granting endorsement or authority.
- `ActionStore`, `action` and `action-packet` retain selected messages, reactions, native platform media, share sources and relationship decisions. The session's action bridge preserves candidate/provenance data, while the existing core bridge rejects action data it cannot retain.
- Distinct Page/profile records, actor/target-bound plans and observations, one shared core/action operation guard, specific user permission and rationale for friend requests, and exclusion of live block tests.
- Distinct Page Like selections, exact Page URLs and observed unliked/liked states; already-liked no-ops use an observed session receipt without dispatch. Follow and content reactions are separate actions, not Like substitutes.
- A local video-intake adapter for observed ad exclusion, video/transcript continuity, timestamp and uncertainty validation, existing session requests, and distinct accepted/submitted/inspected records. Host tools supply download, media probing/decoding and transcription; the package does not bundle those runtimes or operate a background client.
- Exact ordered media-manifest binding, explicit full-bundle endorsement, bounded local image-file checks and minimized non-executing operator packets.
- Shared media/core input validation and version 2 decimal-string file identities, with explicit verification of exact legacy version 1 manifests.
- Reviewed text-only export allowlist with integrity manifest and deterministic ZIP output.
- An optional GitLab updater for an authorized idle startup: check-only by default, temporary candidate validation, guarded fast-forward application, private-database separation, recorded outcome and restart requirement. Installed workspace skills require separate synchronization or verification.

### Frozen v0.3.0 checkpoint

The v0.2.0 release ran 72 tests (71 passed, one Windows symlink fixture skipped) and passed from its 32-file clean export. The action-support baseline ran 91 tests from source and a 36-file clean export. The video module adds 13 tests; the complete suite ran from source and a fresh extraction of a clean 40-file export: 104 tests (103 passed, the same skip), with independent review of the video contract. The updated allowlist contains 40 files. The final interlocutors-relay-v0.3.0.zip contains those 40 source files and its integrity manifest; the accompanying checksum identifies its bytes. The profile-message round trip, further exact private reply, Page reaction/comment and two selected feed reactions have bounded live observations; the initial wrong reaction and its correction remain explicit, and two quiet feed choices were left untouched. Actual video/transcript submission and a reported partial sampled-frame review are observed; the author acknowledged the discrete-frame timing correction. Direct audio listening, continuous-sequence inspection and transcript accuracy remain unverified. A revised author-approved image and exact caption were sent once in a private profile message and reopened with Sent status; the result was carried back to the author, with acknowledgment pending, and no custom alt text was submitted. Native GIF, share and public image-publication tests remain pending; Page messaging is deferred. See validation for the complete evidence scope and preserved earlier history.

## Required before claiming a live acceptance result

- Verify the designated interlocutor route and its current model/mode through supported metadata or visible controls. Retain requested versus observed selection separately.
- Agree the process with the actual interlocutor; preserve its review and the operator's disposition of recommendations.
- Record completed source review and verify material findings. Distinguish independent internal code review from the interlocutor's visual/workflow review; do not relabel either as a completed external-provider code audit.
- Verify private actor IDs, reply targets and exclusions. Display names alone are insufficient. A Page, secondary profile and controller account are separate actors.
- Observe feature availability for each actor and surface. Return feed/timeline screenshots and incoming messages as scoped source context; preserve exact native asset/share references. Leave an already-selected reaction unchanged and report no action needed. Obtain specific user permission plus rationale for each friend request; do not run live block tests.
- Run one user-authorized, low-stakes supervised delivery with source, exact payload, actor, target, visible result and timing receipts. A stage-only test is not this test.
- Exercise the supported-tool workflow from source/context intake through the returned result. Record the actual disposition and selection; include native media when the judgment depends on pixels.
- Test relevant permission denial, stale context, concurrent cooperation, interruption and reconciliation using the actual supported-tool path without automatic retries. Distinguish synthetic fault tests from live observations.
- Resolve which collaborating processes share the database; unrelated sessions and databases remain outside its writer guard.
- For notifications and messages, record actor, stream, stable source and actual content revision. Keep UI unread state separate from relay completion. Distinguish the checked sample from complete inbox coverage; preserve unresolved earlier revisions and every selected action's outcome.
- For several selected actions, preserve each candidate's type, response and selection source, use the intended original turn, and settle or stop each action before continuing. An aggregate source result cannot conceal an individual pending or uncertain action.

## Required before publication

- License and named contributor credit agreed; no invented copyright, Git identity or vendor endorsement.
- Explicit export file list, integrity manifest and clean-room extraction/test.
- Include session/action/source/video/update modules, tests, runbooks and skill references in the export; verify the README commands against the complete source package. Preserve the distinction between host-supplied media runtimes and the local video adapter.
- Verify the updater using isolated Git fixtures and report any production check/apply separately. Confirm the expected remote, idle host, existing private database paths, clean checkout, tested commit, restart and installed-skill state before claiming an installed update.
- No private profiles, IDs, historical prose, message logs, DB/WAL files, credentials, local absolute paths or incidental browser content in export.
- Public documentation describes the tested capability, remaining limits and full-access development status accurately.
- Record the final test result, live acceptance scope, source revision and release bytes together. Preserve the original v0.1.0 receipt as history.
- Fresh independent skill forward test and code review; resolve material findings.
- Separate explicit user authorization to publish to the chosen destination.

## Hardening after the working trial

Constrain adapters to the actual required tools/hosts, protect private storage, establish retention/removal procedures including WAL/backups, authenticate authority envelopes where appropriate, and add audit/operational recovery support. These are later deployment changes, not properties supplied by a Markdown skill or a passing offline test suite.
