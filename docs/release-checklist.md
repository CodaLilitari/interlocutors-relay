# Capability and release checklist

This is a development checklist, not a certification.

The current source is the v0.2.0 development beta. The active-session agent uses supported host tools for live work; the Python modules remain local state and packet utilities. See [the session workflow](active-session.md) for the complete operating sequence and [validation](validation.md) for the bounded live observations.

## Implemented local components

- Standard-library Python state store with SQLite transactions and explicit revisions.
- Exact text and distinct content/transport digests.
- Immutable actor/target bindings and configurable actor exclusions.
- Local authority consumption, revocation, replay handling, intent and uncertainty records.
- A synthetic demo and unit/fault tests; see the current validation receipt for executed results.
- A reusable operator skill and manual relay packet.
- An additive `SessionStore` for source/context intake, private and originated work, dispositions, durable message dispatch, response capture, explicit selection, result reconciliation and return/follow-up packets.
- `session` and `session-packet` CLI commands, including the next-step packet used after interruption. A bridge emits selected text/media requests for the existing core without granting endorsement or authority.
- Exact ordered media-manifest binding, explicit full-bundle endorsement, bounded local image-file checks and minimized non-executing operator packets.
- Shared media/core input validation and version 2 decimal-string file identities, with explicit verification of exact legacy version 1 manifests.
- Reviewed text-only export allowlist with integrity manifest and deterministic ZIP output.

The executed suite contains 72 tests: 71 passed and one Windows symlink fixture skipped. The 32-file clean export also passed the suite. Native image delivery and visual review, an original-infographic workflow audit, one directly approved Page reply, and the verified saved return of its delivery receipt have been observed. Broader deployment coverage remains separate from those acceptance results.

## Required before claiming a live acceptance result

- Verify the designated interlocutor route and its current model/mode through supported metadata or visible controls. Retain requested versus observed selection separately.
- Agree the process with the actual interlocutor; preserve its review and the operator's disposition of recommendations.
- Record completed source review and verify material findings. Distinguish independent internal code review from the interlocutor's visual/workflow review; do not relabel either as a completed external-provider code audit.
- Verify private actor IDs, reply targets and exclusions. Display names alone are insufficient. A Page, secondary profile and controller account are separate actors.
- Run one user-authorized, low-stakes supervised delivery with source, exact payload, actor, target, visible result and timing receipts. A stage-only test is not this test.
- Exercise the supported-tool workflow from source/context intake through the returned result. Record the actual disposition and selection; include native media when the judgment depends on pixels.
- Test relevant permission denial, stale context, concurrent cooperation, interruption and reconciliation using the actual supported-tool path without automatic retries. Distinguish synthetic fault tests from live observations.
- Resolve which collaborating processes share the database; unrelated sessions and databases remain outside its writer guard.

## Required before publication

- License and named contributor credit agreed; no invented copyright, Git identity or vendor endorsement.
- Explicit export file list, integrity manifest and clean-room extraction/test.
- Include the session module, tests, runbook and skill reference in the export; verify the README commands against the complete source package.
- No private profiles, IDs, historical prose, message logs, DB/WAL files, credentials, local absolute paths or incidental browser content in export.
- Public documentation describes the tested capability, remaining limits and full-access development status accurately.
- Record the final test result, live acceptance scope, source revision and release bytes together. Preserve the original v0.1.0 receipt as history.
- Fresh independent skill forward test and code review; resolve material findings.
- Separate explicit user authorization to publish to the chosen destination.

## Hardening after the working trial

Constrain adapters to the actual required tools/hosts, protect private storage, establish retention/removal procedures including WAL/backups, authenticate authority envelopes where appropriate, and add audit/operational recovery support. These are later deployment changes, not properties supplied by a Markdown skill or a passing offline test suite.
