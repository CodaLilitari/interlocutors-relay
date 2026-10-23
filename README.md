# Interlocutor's Relay

A small, source-aware relay for conversations between a human, an AI interlocutor, and an agent operator. The point is to carry someone's actual contribution across interfaces without silently replacing their voice, changing the addressee, or claiming a delivery that was only drafted.

![Violet-and-cyan infographic with Lilitari at the center. Panels cover incoming conversations, original ideas, choices including privacy and quiet, Coda's collaborator/operator role, authorized actions, and returning results.](https://richard-pemberton-research.darkahn.chatgpt.site/projects/interlocutors-relay/relay-hero.png)

Current workflow hero, generated and selected by Lilitari. [Project overview and demonstrated limits](https://richard-pemberton-research.darkahn.chatgpt.site/projects/interlocutors-relay/).

Status: **v0.3.0 development beta, with a 2026-10-23 source follow-up**. The local state core has additive `SessionStore` conversation records, `SourceStore` observations and `ActionStore` plans for distinct platform actors, selected content and observed results. An active host agent carries messages and attachments through its supported tools, following the [active-session workflow](docs/active-session.md). Python preserves the records and next-step packets; it does not itself call a model or operate an account. No account credentials or private conversation examples belong in this directory.

The current source also includes a [reduced-mode scheduled-window runbook](docs/reduced-mode.md): a user-authorized external host timer can resume a feed/message window every four hours, with one combined author packet and up to three selected social actions within a maximum of 45 minutes. Each action is checked separately; fewer actions, private thought and quiet are valid outcomes. Finish early when the work is complete or no useful work remains. The timer is configured separately in the host application; this package does not install a background service. The published v0.3.0 archive remains a frozen earlier snapshot.

The October 23 source follow-up adds [notification/message progress tracking](docs/source-tracking.md) independent of unread badges, mixed author choices with explicit earlier-turn exports, and an [idle-startup GitLab updater](docs/updates.md). It now distinguishes a positively observed pending publication from unknown dispatch: the original submission stays non-repeatable while unrelated authorized work can continue. Explicit saved-receipt links now let one actual message return several outcomes without duplicate sends. Distinct Page Like selections now preserve the exact Page, actor and observed relationship state, including already-liked no-ops. The latest source and clean export each ran **176 tests: 175 passed and one Windows symlink fixture skipped**; the preceding source/export checkpoints ran 174, 171, 163 and 142 tests, and the frozen v0.3.0 release ran 104. A supervised profile-message round trip covered an actual incoming message, author review of its screenshot, exact reply selection, a reopened result showing Sent, and return to the author. A separate Page reaction and comment were verified; the operator's initial wrong Like and its correction to the selected Haha are retained as separate evidence. Sent and visible persistence do not establish recipient reading. [Validation and limits](docs/validation.md) distinguish live observations from synthetic tests and preserve the earlier release history.

This release adds actor-bound action records for messages, reactions, native GIFs, shares and relationship decisions, alongside the existing text/media core. The [action workflow](docs/actions.md) explains the local APIs and the host's actual execution and observation steps. Two author-selected feed reactions were applied and verified; two quiet choices remained untouched. A visually reviewed and revised image was sent once in a private profile message with its exact caption, then reopened to verify one rendered image and Sent status; the returned result was acknowledged. Later bounded tests verified a native Page share with its original source and attribution, and a profile GIF response sent through the platform's native picker. Both outcomes returned to the author and were acknowledged. Six public image announcements have also been verified across distinct Facebook profiles and a Page, X accounts and LinkedIn, keeping human professional wording separate from the interlocutor's own selected voice. Page messaging is deferred. Representing an action locally does not establish live availability for every Page/profile or parity with everything a human can do on a platform.

The new [video-intake workflow](docs/video-intake.md) prepares source video and timestamped transcript packets for the existing session transport, excluding observed ads. The host supplies the downloader, media probe/decoder and transcription runtime. `relay_video.py` is a tested local recording and validation adapter, not a self-contained media service or background social bot. After actual video/transcript submission, the author reported partial sampled-frame inspection and transcript reading, with no direct audio listening. Source-specific visual details were corroborated. An unsupported timing estimate was corrected against decoded frames, and the author acknowledged the correction; continuous-sequence inspection and transcript accuracy remain unverified.

## Start small

For an agent-operated exchange, use the [relay skill](skills/interlocutors-relay/SKILL.md) and [active-session workflow](docs/active-session.md). The host handles intake, transport, response capture, selection and result return. A short conversational packet is sufficient: why this exchange is wanted, the relevant source, who is speaking to whom, and the interlocutor's actual disposition and text. The user does not need to fill out a database form.

For repeatable staging and fault tests, Python 3.11+ and its standard library are sufficient:

```powershell
python -m unittest discover -s tests -v
python relay_cli.py demo
python relay_cli.py --help
python relay_cli.py session --help
python relay_cli.py session-packet --help
python relay_cli.py action --help
python relay_cli.py action-packet --help
python relay_cli.py source --help
python relay_cli.py source-packet --help
python relay_video.py --help
python relay_update.py --help
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

The session's core bridge emits local case/revision requests for a selected text/media payload once an actual actor and target are known. The additive `export_to_action_request` bridge preserves the selected candidate, source and action data for `ActionStore`, including native platform media. Neither bridge grants endorsement or action authority. The core bridge rejects action data it cannot carry, including share sources, instead of silently dropping it.

A `MULTIPLE` response can contain different selected action types, with an explicit disposition on each candidate. Each candidate is selected, bound and verified separately. The export bridges, result recorder and next-step packet accept an explicit original `turn_id`, preserving the correct response and selection when a later receipt turn exists. Earlier records without per-candidate dispositions retain their original behavior; this does not automatically execute a batch or revive a superseded choice.

`relay_sources.py` tracks host-observed notifications and messages by actor, stream and source, with separate content revisions and progress. Inspection, actual relay, author disposition, action result and result return remain distinct from the platform's unread badge. A source edit retains any earlier revision whose author response or result is still pending. The [source-tracking guide](docs/source-tracking.md) describes stable references, evidence limits and bounded resumption; the module does not scan an inbox or infer complete coverage. In a bounded live check, two actors' notification lists and one profile's message list were inspected separately. Clearing one unread flag left the same source revision awaiting its author response; the actual `QUIET` response subsequently closed four presented sources without a content action.

`relay_actions.py` records immutable action plans, distinct Page/profile actors, exact targets and observed outcomes. `action` applies an allowlisted local request; `action-packet` supplies the supported-tool next step. Messages bind to private conversations; reactions retain the selected reaction; shares retain the original source; native GIFs retain the selected platform asset reference. Friend requests require specific user permission and a rationale for that actor and target. Block plans cannot be dispatched with `scope: live_test`. The agent follows the [active-session skill reference](skills/interlocutors-relay/references/active-session.md) and [action workflow](docs/actions.md) to perform and verify the applicable host steps.

`LIKE_PAGE` choices use a distinct `like_page` action bound to the selected Page URL and actor. The host inspects the actual Like state first: already-liked Pages receive an observed no-op result, while an actual Like needs its own operation claim and verified Liked-state receipt. Follow-only controls and post reactions cannot substitute for a Page Like. This records supported host work; it does not establish live availability for every Page or actor.

`FOLLOW_PAGE` / `follow_page` separately records an explicitly chosen Page Follow. The host verifies the exact Page and actor, leaves existing Following unchanged with an already-following no-op receipt, and verifies Following after any newly claimed action. An unavailable Like must be returned as a limitation before the interlocutor chooses Follow; the runtime never silently converts one into the other.

`relay_video.py` records observed ad status before intake, exact acquired video and transcript hashes, measured duration, timestamped transcript segments and uncertainty. It emits existing `SessionStore` requests and retains separate attachment acceptance, actual submission and author-inspection observations. It neither downloads nor decodes/transcribes media, and an accepted attachment is not evidence of inspection. Its [guide](docs/video-intake.md) documents the local JSON API/CLI and host responsibilities.

`relay_update.py` checks the configured GitLab `origin/main` at an idle startup. Check-only is the default; an authorized `--apply` stages a descendant, runs its tests and installs the tested commit by fast-forward after checking the checkout and existing runtime guards. It keeps private databases outside the checkout and requires restart after installation. The host must separately verify or synchronize its installed skill. The [update guide](docs/updates.md) describes failure and recovery states; local fixture tests are not evidence of a production GitLab update. No scheduler or account action is installed by the updater, and frozen release archives remain unchanged.

The SQLite core records immutable case bindings, revision lineage, exact-text endorsement attestations, explicit stage selection, expiring single-use action-authority attestations, local delivery claims, and observed/reconciled outcomes. It distinguishes authored UTF-8 content from the same content plus a file-boundary LF. Corrections and alternative candidates do not silently overwrite prior text.

The core and action companion share a writer guard when cooperating processes use **one shared database**. It does not prevent another browser, human, unrelated agent, or a second database from acting on an account. A local claim and an idempotent operation ID are not external exactly-once delivery. Never retry a side effect merely because its result was not visible.

See [the operating process](docs/process.md) and [the capability and release checklist](docs/release-checklist.md).

For creative work, [the creative workflow](docs/creative-workflow.md) separates concepts, actual candidate files, visual inspection, selection, caption/alt text and publication. `media_manifest.py` measures and hashes exact local image files without uploading them. The state core binds the ordered media list and alt text to the caption with a full-payload digest. Header-derived dimensions are not a full decode or visual-quality check.

Media inspection now shares the core's identifier and alt-text validation. New manifests use version 2 decimal-string file identities, preserving large device/inode values through ordinary JSON tools. Exact version 1 integer manifests remain verifiable without rewriting old receipts.

`operator_packet.py` emits a minimized author-review packet, or a staged operator-preflight packet with actor, target, reply parent and audience. Neither executes anything or grants permission. The latter can contain private destination identifiers and is not automatically public-safe. See [the runnable quickstart](docs/quickstart.md).

## Codex setup

To enable the skill for a chosen repository, place the complete `skills/interlocutors-relay` folder under that repository's `.agents/skills/`, including its references. Keep the runtime alongside its documentation and configure its absolute path in your private deployment notes. Do not copy private research or runtime records into the skill or release.

Codex discovers repository skills under `.agents/skills`; the name and description route requests, while the body and linked resources are read when relevant. This setup follows the [official skill documentation](https://learn.chatgpt.com/docs/build-skills), checked 2026-10-20. A skill does not grant tools, browser permissions, credentials, or authority to publish. Host/tool capabilities must be verified independently.

## Development before hardening

This beta can be developed under the user's existing full-access environment. It does not edit sandbox, browser, account, or network policy. The bounded profile-message and Page workflows described in validation were performed by an active host agent. Broader deployment hardening follows those working paths: narrow tool permissions, protect local storage, and add dedicated adapters only where the observed workflow needs them.

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
