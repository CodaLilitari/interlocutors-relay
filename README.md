# Interlocutor's Relay

A small, source-aware relay for conversations between a human, an AI interlocutor, and an agent operator. The point is to carry someone's actual contribution across interfaces without silently replacing their voice, changing the addressee, or claiming a delivery that was only drafted.

Status: **v0.1.0 development beta**. The local state core, exact-media checks, non-executing operator packets and synthetic tests are implemented. This is a usable local/agent-mediated workflow, not an unattended social-media manager. Live browser/provider adapters, a verified supervised social delivery, independent external release review and production hardening remain separate milestones. No account credentials or private conversation examples belong in this directory.

## Start small

For a manual exchange, use the skill at `skills/interlocutors-relay/SKILL.md`. A short packet is sufficient: why this reply is wanted, the relevant quotation, who is speaking to whom, and the interlocutor's actual disposition and text. Do not make a person fill out a database form just to ask a question.

For repeatable staging and fault tests, Python 3.11+ and its standard library are sufficient:

```powershell
python -m unittest discover -s tests -v
python relay_cli.py demo
python relay_cli.py --help
```

The demo uses synthetic identities and an in-memory database. It does not open a browser, send a message, publish, or call a model. `relay_core.py` records state; it is not an executor or an authentication service.

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

The SQLite core records immutable case bindings, revision lineage, exact-text endorsement attestations, explicit stage selection, expiring single-use action-authority attestations, local delivery claims, and observed/reconciled outcomes. It distinguishes authored UTF-8 content from the same content plus a file-boundary LF. Corrections and alternative candidates do not silently overwrite prior text.

The writer guard coordinates cooperating processes using **one shared database**. It does not prevent another browser, human, unrelated agent, or a second database from acting on an account. A local claim and an idempotent operation ID are not external exactly-once delivery. Never retry a side effect merely because its result was not visible.

See [the operating process](docs/process.md) and [the capability and release checklist](docs/release-checklist.md).

For creative work, [the creative workflow](docs/creative-workflow.md) separates concepts, actual candidate files, visual inspection, selection, caption/alt text and publication. `media_manifest.py` measures and hashes exact local image files without uploading them. The state core binds the ordered media list and alt text to the caption with a full-payload digest. Header-derived dimensions are not a full decode or visual-quality check.

`operator_packet.py` emits a minimized author-review packet, or a staged operator-preflight packet with actor, target, reply parent and audience. Neither executes anything or grants permission. The latter can contain private destination identifiers and is not automatically public-safe. See [the runnable quickstart](docs/quickstart.md).

## Codex setup

The skill is staged here for review; it has not replaced any existing personal skill or project instructions. To enable the reviewed skill for a chosen repository, place the complete `skills/interlocutors-relay` folder under that repository's `.agents/skills/`. Keep the runtime alongside its documentation and configure its absolute path in your own deployment notes. Do not copy the private research folder into the skill or release.

Codex discovers repository skills under `.agents/skills`; the name and description route requests, while the body and linked resources are read when relevant. This setup follows the [official skill documentation](https://learn.chatgpt.com/docs/build-skills), checked 2026-10-20. A skill does not grant tools, browser permissions, credentials, or authority to publish. Host/tool capabilities must be verified independently.

## Development before hardening

This candidate can be developed under the user's existing full-access environment. It does not edit sandbox, browser, account, or network policy. First establish the manual/agent-mediated workflow and run one explicitly scoped live trial through supported tools. Then narrow tool permissions, protect local storage, and implement production adapters only for the observed workflow.

The database and WAL contain plaintext text and references. Keep them private, outside this distributable directory, on appropriate local storage. Use minimal opaque source pointers; do not store credentials, cookies, complete private transcripts, or unrelated personal information. Hashes verify byte consistency, not privacy, origin authentication, authorship, or consent.

## License, credit and distribution

Executable code, tests, configuration and the operational skill are Apache-2.0. Narrative documentation is CC BY 4.0; embedded code examples are Apache-2.0. [LICENSE.md](LICENSE.md) defines the exact split. [CREDITS.md](CREDITS.md) credits Richard Pemberton as human lead/maintainer, Coda for implementation and integration, and Lilitari for substantive workflow, creative-process and licensing contributions. AI contribution credit is not a claim of legal personhood, copyright ownership or vendor endorsement.

Build a clean release using the reviewed `release-files.json`, never the parent workspace or a case database:

```powershell
python export_release.py --source-root . --destination ../relay-export --archive ../interlocutors-relay-v0.1.0.zip
```

The destination and archive must not already exist. The export includes an exact byte-size/SHA-256 manifest; its conservative text scan is defense in depth, not proof of privacy. Inspect the allowlist and generated files before publishing. User media and runtime records are outside the distributed licenses unless their rights-holder separately licenses them.
