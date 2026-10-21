# Capability and release checklist

This is a development checklist, not a certification.

## Implemented local components

- Standard-library Python state store with SQLite transactions and explicit revisions.
- Exact text and distinct content/transport digests.
- Immutable actor/target bindings and configurable actor exclusions.
- Local authority consumption, revocation, replay handling, intent and uncertainty records.
- A synthetic demo and unit/fault tests; see the current validation receipt for executed results.
- A reusable operator skill and manual relay packet.
- Exact ordered media-manifest binding, explicit full-bundle endorsement, bounded local image-file checks and minimized non-executing operator packets.
- Reviewed text-only export allowlist with integrity manifest and deterministic ZIP output.

The current executed checks and remaining verification limits are in [validation.md](validation.md). A development-beta publication is not a claim that the live-readiness checklist below is complete.

## Required before claiming live readiness

- Verify the designated interlocutor route and its current model/mode through supported metadata or visible controls. Retain requested versus observed selection separately.
- Agree the process with the actual interlocutor; preserve its review and the operator's disposition of recommendations.
- Obtain an independent external review and verify material findings, not merely its verdict.
- Verify private actor IDs, reply targets and exclusions. Display names alone are insufficient. A Page, secondary profile and controller account are separate actors.
- Run one user-authorized, low-stakes supervised delivery with source, exact payload, actor, target, visible result and timing receipts. A stage-only test is not this test.
- Test permission denial, stale context, concurrent cooperation, crash uncertainty and reconciliation using the real adapter without automatic retries.
- Resolve which collaborating processes share the database; unrelated sessions and databases remain outside its writer guard.

## Required before publication

- License and named contributor credit agreed; no invented copyright, Git identity or vendor endorsement.
- Explicit export file list, integrity manifest and clean-room extraction/test.
- No private profiles, IDs, historical prose, message logs, DB/WAL files, credentials, local absolute paths or incidental browser content in export.
- Public documentation describes the tested capability, remaining limits and full-access development status accurately.
- Fresh independent skill forward test and code review; resolve material findings.
- Separate explicit user authorization to publish to the chosen destination.

## Hardening after the working trial

Constrain adapters to the actual required tools/hosts, protect private storage, establish retention/removal procedures including WAL/backups, authenticate authority envelopes where appropriate, and add audit/operational recovery support. These are later deployment changes, not properties supplied by a Markdown skill or a passing offline test suite.
