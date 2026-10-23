# Check GitLab and update at idle startup

`relay_update.py` checks the configured GitLab `origin/main`. It defaults to **check only**. An authorized host can run it with `--apply --host-idle` at an idle startup, before any account-browser work or normal relay runtime use and with no other operator active, to validate and install a fast-forward update. It does not create a scheduler, run a social action, change database contents, install dependencies, push Git changes, or restart the host.

This workflow is for a Git checkout of the source. An extracted release ZIP has no Git history or configured upstream, so it cannot use in-place updating. Keep private databases and attachments outside the checkout. The frozen release archives remain unchanged.

A separately installed agent skill is outside this updater's ownership. The host's startup integration must synchronize or verify its installed skill against `skills/interlocutors-relay` after an update, before using it. Updating the source checkout alone does not establish that an installed workspace skill also changed.

## Commands

Check without fetching candidate code or opening runtime databases:

```powershell
python relay_update.py --repo C:\relay\interlocutors-relay
```

At an idle startup, request automatic application of a tested descendant:

```powershell
python relay_update.py --repo C:\relay\interlocutors-relay --apply --host-idle --session-db C:\relay-private\relay.sqlite --delivery-db C:\relay-private\delivery.sqlite
```

The default expected repository is `https://gitlab.com/codalilitari/interlocutors-relay.git`. `--expected-remote` can explicitly select another credential-free GitLab HTTPS repository ending in `.git`. The configured fetch URL must match exactly, the checked-out branch must be `main`, and its upstream configuration must be `origin/main`. No API key or token option is accepted. Git uses its existing access configuration without interactive credential prompts; raw Git diagnostics are not retained in receipts because they can contain credentials.

The importable entry point is:

```python
from relay_update import run_update

result = run_update(
    source_root,
    apply=True,
    host_idle=True,
    session_db=private_session_database,
    delivery_db=private_delivery_database,
)
```

`apply=False` and `host_idle=False` are the defaults. Apply without `host_idle=True` returns `host_busy`; the CLI equivalent is `--host-idle`. This assertion means the host has not started browser mutations or normal relay work and has established that no other operator is using the same account/runtime. It is not automatic browser-activity detection, and must not be supplied merely to bypass a busy host. Database guard checks remain mandatory even when this assertion is supplied. `expected_remote` is the optional explicit repository argument.

`run_update` returns a structured receipt and appends the same core receipt to `relay-update-receipts.jsonl` in the checkout's common Git metadata directory. The returned `receipt_recorded` field reports whether that append succeeded. Receipts record old, advertised candidate and observed new revisions, validation status, installation outcome, the idle assertion and whether a restart is required; they contain no database contents or raw test/Git output.

## Installation sequence

1. Acquire the shared `relay-update.lock` in common Git metadata. Verify the exact root, remote and main/upstream configuration. Reject tracked modifications and untracked files. Ignored files are not classified as checkout changes, but installation uses Git's `--no-overwrite-ignore` option so they cannot be silently replaced.
2. For apply mode, require the idle-host assertion and inspect both existing private databases. Their paths must resolve outside the checkout. Reject any author turn in `prepared`, `not_sent`, `in_flight` or `uncertain`, and a `sent` turn that expects a response (`expects_response` defaults to true for older records). Also reject an unresolved core/action delivery guard or action operation in `in_flight` or `uncertain`. Finish the pending send/response or reconcile uncertainty first. Positively observed `submitted_pending` is known submission awaiting platform publication and does not itself block an otherwise idle update; its no-resend record remains intact. Do not initialize, migrate, close or reconcile a database here.
3. Query GitLab's main reference. If it matches local HEAD, return `up_to_date`. Check-only returns `update_available` when revisions differ; that status does not certify ancestry or passing tests.
4. For apply, fetch the advertised candidate into a new temporary staging checkout. Import the current local ancestry and verify that the candidate is a descendant. Reject a moving remote, divergence or a remote behind local history. The installed checkout is untouched during candidate validation.
5. Run `python -B -m unittest discover -s tests` in the candidate using the same Python interpreter as the updater. At least one discovered test must run and the suite must pass within five minutes. Also verify validation has not changed the pinned revision or tracked source. This executes the selected repository's tests; it is not a sandbox or a proof of all platform behavior. Test output is not included in the receipt. On failure, inspect the candidate separately if diagnostics are needed; the prior working checkout remains installed.
6. Acquire temporary SQLite writer reservations on both runtime databases without changing records. Recheck the guard state, checkout cleanliness, remote configuration and original HEAD. This prevents cooperating SQLite writers from claiming a new operation while the update installs. The host must still be idle: the updater cannot detect a browser operator that does not record its operations or an unrelated Git writer that ignores the update lock.
7. Import the tested commit objects from staging and run `git merge --ff-only --no-overwrite-ignore` for that exact commit. Verify the new HEAD and clean worktree. No reset, stash, force operation or user-file deletion is used. Preserve the old revision in the receipt.
8. Release the runtime reservations and updater lock. After `updated`, restart or re-execute the normal host entry point before importing runtime modules. Do not continue operating with a mixture of already imported old modules and new source files.

The temporary staging checkout is removed when the attempt ends. Private database paths are used only for guard inspection/reservations and are never copied into staging. The updater does not back up or migrate private data. Candidate tests run in a separate process, but still have the normal permissions of the host user.

The source schema distinguishes pending author sends from stored ideas. `SessionStore.prepare_turn` refuses to create another turn while any existing turn in that session is outside `responded` or `closed`; it has no separate superseded/abandoned state. A real `prepared` or retryable `not_sent` turn therefore remains pending regardless of age. A successfully sent one-way receipt becomes `closed`. Responded `HOLD`/`QUIET` turns, unselected response candidates, selected proposals with no execution, and `ActionStore` plans whose execution is `not_started` are persisted choices/intent, not active dispatches, and do not independently block an idle-startup update. A host actively staging one of those choices in a browser must withhold its idle assertion. The updater never treats elapsed time as cancellation or clears old records to make itself run.

Known `submitted_pending` releases the ActionStore shared delivery guard while retaining the submitted item for later publication observation. Returning its receipt does not settle publication, but neither that persisted pending state nor terminal `not_published` requires an active browser dispatch. They do not independently make the updater busy. An unclosed author receipt/response or an unrelated uncertain operation still blocks according to the checks above. A host currently observing or interacting with a pending item is not idle and must wait until a later startup. The updater never supplies rejection evidence, converts pending to success, clears uncertainty, or grants permission to resend.

## Results and host behavior

| Status | Meaning and next step |
|---|---|
| `up_to_date` | Local main matched the observed GitLab main; no installation |
| `update_available` | Check-only found a different remote revision; no candidate fetched or tested |
| `updated` | Candidate suite passed and the exact fast-forward was verified; restart before operation |
| `dirty_checkout` / `checkout_changed` | Local edits, untracked files or a changed HEAD were preserved; finish/review existing work before another apply |
| `host_busy` | Apply lacked the explicit idle-startup assertion; do not update during browser/runtime activity or while another operator is active |
| `runtime_busy` | Finish the pending author send/response or reconcile uncertain/in-flight author or social operations first; do not clear records merely to update |
| `runtime_unavailable` | A required database is missing, inside the checkout, locked, unreadable or lacks expected schema; restore valid existing paths/state |
| `updater_busy` | Another lock exists; skip this attempt. An interrupted process can leave a stale lock, which requires checking its owner before manual removal |
| `configuration_mismatch` | Actual root/remote/branch/upstream does not match; no automatic reconfiguration |
| `remote_changed` / `non_fast_forward` | Candidate moved during staging or is not a descendant; no reset or divergent merge |
| `validation_failed` | Candidate tests failed, timed out, did not run tests, or changed tracked code; prior runtime remains installed |
| `check_failed` | Git/network/local metadata could not complete the check; no raw authentication diagnostics retained |
| `installation_uncertain` | Final installation did not verify cleanly or was interrupted. Stop runtime operations and inspect the checkout; never automatically reset or blindly retry |

CLI exit code is `0` for `up_to_date`, `update_available` or `updated`, and `2` for other outcomes. The host must interpret `status`, not only the exit code. An apply command that succeeds in installing returns `restart_required: true`; an interrupted installation also requests restart/inspection. A failed receipt append is reported separately and must not be described as a saved receipt.

An updater can check automatically when the host starts or a bounded recurring window begins. Apply must run before account-browser work or normal runtime use, with no other operator active; after activity has begun, wait until a later idle startup rather than adding the idle flag retroactively. A failed check is not permission to change the account workflow, erase a pending send, modify the expected remote, or buy usage. Update checking and social scheduling remain separate.

## Local validation

```powershell
python -B -m unittest discover -s tests -p test_relay_update.py -v
```

The fixture suite uses temporary local Git repositories and synthetic private databases. Only the GitLab URL-syntax check is mocked for those local fixtures; real Git history, staged test execution, failure paths and fast-forward installation are exercised without public network access. Production has no local-remote bypass. Tests cover default check-only, up-to-date/available results, private-file preservation, dirty/untracked files, divergent history, failing validation, pending author turn states created through the actual SessionStore API, terminal one-way receipts and responded/unexecuted proposals, explicit idle-host assertion, database writer contention, concurrent updater lock, wrong-remote diagnostic redaction and an ignored file that must not be overwritten. These tests do not demonstrate a production update or current GitLab availability.
