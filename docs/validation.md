# Validation and limits

## v0.2.0 development beta, 2026-10-22

The beta combines local state utilities with an active host agent using supported conversation, attachment and browser tools. The observations below establish a bounded working relay; they do not establish unattended operation or a general-purpose autonomous platform adapter. Private source material, account identifiers, conversation links and delivery receipts are excluded from this public package.

### Executed local checks

- Source audit reproduced six cases where the media inspector accepted metadata that the core rejected. Inspection now uses the core's actual ID and alt-text rules, rejects those cases before reading a file, and preserves valid author text exactly. A regression test passes inspected and reverified media into a real in-memory `Store`.
- New version 2 manifests encode device/inode identities as decimal strings. Targeted tests cover values beyond JavaScript's exact integer range, exact legacy version 1 verification, rejected rounded legacy identities and version/type mismatches. An actual Node.js JSON parse/stringify round trip followed by Python continuity verification passed. The preserved original version 1 pilot manifest also verified without modifying that receipt.
- `python -m unittest discover -s tests -q` on Windows with Python 3.13.15: **72 tests, 71 passed and one Windows symlink fixture skipped**. The full suite passed from the expanded clean export as well. The skipped fixture is not evidence of verified symlink enforcement on this host.
- The corrected README PowerShell export block ran verbatim against a temporary copy of all 32 allowlisted files and produced the complete export, integrity manifest and ZIP. The clean export's full suite then passed. Ordinary session method calls no longer trigger the credential scan; regression checks retain rejection of literal credential values.
- `SessionStore` adds local source/context intake, private or originated work, durable author-message dispatch claims, response/disposition records, explicit candidate selection, result reconciliation and linked return/follow-up packets. Its synthetic tests exercise these state transitions. The active host agent supplies actual message transport and observations through supported tools.

### Observed supported-tool workflow

- A native image pilot was delivered to the designated saved authoring conversation. The interlocutor returned an image-specific visual review and selected the exact image/caption/alt-text bundle for private staging. Image review and private selection were recorded separately from authority to publish.
- The original workflow infographic was visually inspected and audited against the working procedure and implementation. It supplied design context; the picture alone was not treated as evidence that a described component already worked.
- After direct human approval, the operator submitted one selected text reply through the intended Facebook Page. Reopening the reply permalink showed the intended author, reply parent and exact selected text. The existing Like remained unchanged. This establishes visible persistence of that reply, not proof that its recipient read it.
- The exact delivery receipt was returned to the designated saved authoring conversation. It remained visible after refresh and was retrieved from saved history, closing the result-return transport. This was a one-way receipt and required no new acknowledgment; it does not establish that the interlocutor independently inspected the platform or that the recipient read the reply.

### Review scope and remaining limits

Independent internal source review examined the implementation and ordinary workflow failures; fixes were checked through executed regressions and clean-export tests. The interlocutor's review concerned the actual image, workflow and authorial decisions. That visual/workflow review is not represented as an independent code audit. No completed review by a separate external code-review provider is claimed.

The host agent performed the observed interactions; Python itself still has no browser or model client. Local records remain operator attestations. This release does not establish a scheduler, unattended social management, universal platform compatibility, external exactly-once guarantees, full image decoding or completed deployment hardening. The database and WAL remain private plaintext, and cooperation guards apply only to processes sharing the relevant database.

## v0.1.0 historical validation, 2026-10-20

The following is the dated validation state of the original release. Later source changes and observations do not retroactively change those bytes or this receipt.

Checked 2026-10-20 on Windows with Python 3.13. The package targets Python 3.11+; other supported interpreters have not been exercised in this receipt.

- `python -m unittest discover -s tests -q`: 50 tests, 49 passed and one platform-dependent symlink fixture skipped because the host denied test symlink creation.
- The executable stage-only quickstart ran successfully and returned exact author-review and operator-preflight packets without external actions.
- All five runtime modules compiled successfully with `py_compile`.
- An independent internal read-through exposed a caption-only media-endorsement gap. It was reproduced, repaired by requiring the exact full-payload digest for media, and covered by a regression test. This attestation still cannot prove actual visual inspection.
- The export is assembled only from `release-files.json`, with per-file SHA-256 and size records in `EXPORT-MANIFEST.json`. Tests cover changed files, private/secret-shaped material, traversal, case collisions, non-overwrite behavior and deterministic ZIP output. The skipped symlink fixture is not a verified enforcement test on this host.

The checks cover local code paths, synthetic identities and synthetic observations. No live social-media delivery, actual-account identity proof, browser adapter, full image decoding, platform compliance or immunity from account flags is established. The stored database is plaintext and the writer guard coordinates only cooperating users of the same database.

The designated interlocutor's text consultation was returned and persisted. Its actual selected backend/model and native image-review route were not independently verified. An attempted separate external-review route stopped at non-generating preflight; no completed independent external release review is claimed. Review Hub availability and review completion are distinct.
