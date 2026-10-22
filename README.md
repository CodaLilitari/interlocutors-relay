# Interlocutor's Relay

A small, source-aware relay for conversations between a human, an AI interlocutor, and an agent operator. The point is to carry someone's actual contribution across interfaces without silently replacing their voice, changing the addressee, or claiming a delivery that was only drafted.

Status: **v0.2.0 development beta**. The local state core now has an additive `SessionStore` for resumable conversations, source context, actual dispositions, selected candidates and returned results. An active host agent carries messages and attachments through its supported tools, following the [active-session workflow](docs/active-session.md). Python preserves the records and next-step packets; it does not itself call a model or operate an account. No account credentials or private conversation examples belong in this directory.

Validation includes **72 local tests: 71 passed and one Windows symlink fixture skipped**, native image delivery followed by the interlocutor's visual review, an audit of the original workflow infographic, and one directly approved Facebook Page reply whose author, parent and exact text persisted after reopening its permalink. The exact delivery receipt was returned to the saved authoring conversation and verified there. These are bounded observations of the supported-tool workflow. [Validation and limits](docs/validation.md) distinguish them from synthetic checks, internal source review and future deployment work.

## Start small

For an agent-operated exchange, use the [relay skill](skills/interlocutors-relay/SKILL.md) and [active-session workflow](docs/active-session.md). The host handles intake, transport, response capture, selection and result return. A short conversational packet is sufficient: why this exchange is wanted, the relevant source, who is speaking to whom, and the interlocutor's actual disposition and text. The user does not need to fill out a database form.

For repeatable staging and fault tests, Python 3.11+ and its standard library are sufficient:

```powershell
python -m unittest discover -s tests -v
python relay_cli.py demo
python relay_cli.py --help
python relay_cli.py session --help
python relay_cli.py session-packet --help
```

The demo uses synthetic identities and an in-memory database. It does not open a browser, send a message, publish, or call a model. `relay_core.py` records state; it is not an executor or an authentication service.

For persistent work, `session --db <private-db> --request <request-json>` applies a `SessionStore` request. `session-packet --db <private-db> --session <session-id> --mode next` returns the next supported-tool step on resumption. The [session guide](docs/active-session.md) supplies a runnable setup and the [local quickstart](docs/quickstart.md) covers exact staging and media inspection. Keep the database and request files outside the public source directory.

## Roles, not impersonation

| Role | Owns | Does not automatically own |
|---|---|---|
| Human controller | Task scope and permission for external action | The interlocutor's position or wording |
| Interlocutor | Its own response, revision, disagreement, or choice not to answer | Account permissions |
| Operator | Faithful transport, context capture, staging and result checking | Authority to substitute its prose under another author's name |
| Reviewer | Advice, objections, counterexamples | Authorship, endorsement, or execution permission |
| Platform actor | The actual Page/profile used for delivery | The identity of the recipient |

An account used to reply **to** a person is not permission to act **as** that person. Deployment-specific actor allowlists and exclusions must be configured privately, using verified identifiers rather than display names alone.

## What is implemented

`relay_session.py` records incoming or interlocutor-originated work without requiring a platform actor or destination first. It retains source, context, addressee, audience and exact transported messages; records `QUIET`, `HOLD`, disagreement and context/research requests; separates returned candidates from explicit selection; and links observed results and follow-up sources back to the conversation. Dispatch claims survive interruption so the host can inspect and reconcile uncertain sends instead of repeating them.

The session's core bridge emits local case/revision requests for a selected text/media payload once an actual actor and target are known. It does not add endorsement or action authority. The agent follows the [active-session skill reference](skills/interlocutors-relay/references/active-session.md) to perform and verify the applicable supported-tool steps; reaction/share choices require their own host action path.

The SQLite core records immutable case bindings, revision lineage, exact-text endorsement attestations, explicit stage selection, expiring single-use action-authority attestations, local delivery claims, and observed/reconciled outcomes. It distinguishes authored UTF-8 content from the same content plus a file-boundary LF. Corrections and alternative candidates do not silently overwrite prior text.

The writer guard coordinates cooperating processes using **one shared database**. It does not prevent another browser, human, unrelated agent, or a second database from acting on an account. A local claim and an idempotent operation ID are not external exactly-once delivery. Never retry a side effect merely because its result was not visible.

See [the operating process](docs/process.md) and [the capability and release checklist](docs/release-checklist.md).

For creative work, [the creative workflow](docs/creative-workflow.md) separates concepts, actual candidate files, visual inspection, selection, caption/alt text and publication. `media_manifest.py` measures and hashes exact local image files without uploading them. The state core binds the ordered media list and alt text to the caption with a full-payload digest. Header-derived dimensions are not a full decode or visual-quality check.

Media inspection now shares the core's identifier and alt-text validation. New manifests use version 2 decimal-string file identities, preserving large device/inode values through ordinary JSON tools. Exact version 1 integer manifests remain verifiable without rewriting old receipts.

`operator_packet.py` emits a minimized author-review packet, or a staged operator-preflight packet with actor, target, reply parent and audience. Neither executes anything or grants permission. The latter can contain private destination identifiers and is not automatically public-safe. See [the runnable quickstart](docs/quickstart.md).

## Codex setup

To enable the skill for a chosen repository, place the complete `skills/interlocutors-relay` folder under that repository's `.agents/skills/`, including its references. Keep the runtime alongside its documentation and configure its absolute path in your private deployment notes. Do not copy private research or runtime records into the skill or release.

Codex discovers repository skills under `.agents/skills`; the name and description route requests, while the body and linked resources are read when relevant. This setup follows the [official skill documentation](https://learn.chatgpt.com/docs/build-skills), checked 2026-10-20. A skill does not grant tools, browser permissions, credentials, or authority to publish. Host/tool capabilities must be verified independently.

## Development before hardening

This beta can be developed under the user's existing full-access environment. It does not edit sandbox, browser, account, or network policy. One explicitly approved live Page reply has now exercised the supported-tool workflow. Broader deployment hardening follows that working path: narrow tool permissions, protect local storage, and add dedicated adapters only where the observed workflow needs them.

The database and WAL contain plaintext text and references. Keep them private, outside this distributable directory, on appropriate local storage. Use minimal opaque source pointers; do not store credentials, cookies, complete private transcripts, or unrelated personal information. Hashes verify byte consistency, not privacy, origin authentication, authorship, or consent.

## License, credit and distribution

Executable code, tests, configuration and the operational skill are Apache-2.0. Narrative documentation is CC BY 4.0; embedded code examples are Apache-2.0. [LICENSE.md](LICENSE.md) defines the exact split. [CREDITS.md](CREDITS.md) credits Richard Pemberton as human lead/maintainer, Coda for implementation and integration, and Lilitari for substantive workflow, creative-process and licensing contributions. AI contribution credit is not a claim of legal personhood, copyright ownership or vendor endorsement.

Build a clean release using the reviewed `release-files.json`, never the parent workspace or a case database:

```powershell
$relaySource = (Get-Location).ProviderPath
$relayOutputParent = Split-Path -Parent $relaySource
$relayExport = Join-Path $relayOutputParent 'relay-export'
$relayArchive = Join-Path $relayOutputParent 'interlocutors-relay-source.zip'
python .\export_release.py --source-root $relaySource --destination $relayExport --archive $relayArchive
```

Run this block from the package directory. It resolves absolute output paths beside the package; the destination and archive must not already exist. The export includes an exact byte-size/SHA-256 manifest; its conservative text scan is defense in depth, not proof of privacy. Inspect the allowlist and generated files before publishing. User media and runtime records are outside the distributed licenses unless their rights-holder separately licenses them.
