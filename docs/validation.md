# v0.1.0 validation and limits

Checked 2026-10-20 on Windows with Python 3.13. The package targets Python 3.11+; other supported interpreters have not been exercised in this receipt.

- `python -m unittest discover -s tests -q`: 50 tests, 49 passed and one platform-dependent symlink fixture skipped because the host denied test symlink creation.
- The executable stage-only quickstart ran successfully and returned exact author-review and operator-preflight packets without external actions.
- All five runtime modules compiled successfully with `py_compile`.
- An independent internal read-through exposed a caption-only media-endorsement gap. It was reproduced, repaired by requiring the exact full-payload digest for media, and covered by a regression test. This attestation still cannot prove actual visual inspection.
- The export is assembled only from `release-files.json`, with per-file SHA-256 and size records in `EXPORT-MANIFEST.json`. Tests cover changed files, private/secret-shaped material, traversal, case collisions, non-overwrite behavior and deterministic ZIP output. The skipped symlink fixture is not a verified enforcement test on this host.

The checks cover local code paths, synthetic identities and synthetic observations. No live social-media delivery, actual-account identity proof, browser adapter, full image decoding, platform compliance or immunity from account flags is established. The stored database is plaintext and the writer guard coordinates only cooperating users of the same database.

The designated interlocutor's text consultation was returned and persisted. Its actual selected backend/model and native image-review route were not independently verified. An attempted separate external-review route stopped at non-generating preflight; no completed independent external release review is claimed. Review Hub availability and review completion are distinct.
