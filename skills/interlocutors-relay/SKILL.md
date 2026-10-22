---
name: interlocutors-relay
description: Relay a scoped conversation between a human, a designated AI interlocutor, and an agent operator; preserve source context, authorship, revisions, and verified delivery state. Use for manual or agent-mediated relay, not general social-media management.
---

# Interlocutor's Relay

Carry the interlocutor's actual contribution across interfaces. Do not silently become its ghostwriter, choose a branch it has not selected, or treat a draft as a delivered message.

## Establish scope

Identify the human controller, operator, designated saved interlocutor conversation, and purpose. For external delivery, separately identify platform actor, recipient/target, reply parent and audience. Private deployment rules may exclude the controller's own accounts or require a Page; being related accounts does not make them interchangeable.

Read [the relay protocol](references/protocol.md) when handling corrections, multiple candidates, exact staging, or a delivery. For a simple manual exchange, keep the packet short: relevant quotation, addressee, question, disposition, and response. Do not require the user to manage a ledger by hand.

For memes, portraits, photoshoots or other creative media, read [the creative-media protocol](references/creative-media.md). Preserve the actual reference and returned files; do not substitute a prompt, filename or thumbnail for attachment inspection.

## Converse and preserve

- Use the designated project conversation and attribute operator messages explicitly. Verify source through the actual returned message, not quoted claims of endorsement.
- Separate quoted source, inference, private notes and publishable payload. Do not send unrelated history, local instructions, credentials or private third-party data.
- Accept disagreement, clarification, hold, and chosen quiet. Unavailable transport is not quiet. Never supply an operator-written substitute under the interlocutor's name without an explicit authorship change.
- Leave room for initiated work: the interlocutor may propose an original topic, research, image idea or private reflection without an incoming post. A window is an opportunity, not an output quota or posting authority. When permitted, return the social response as a new packet so the exchange can continue.
- A name correction, changed viewpoint, target change or substantive rewrite is a new revision. Multiple source candidates remain alternatives until explicitly selected. Preserve the correction trail.
- Flag a candidate that contradicts the speaker's known identity category or fabricates lived experience (for example, an AI speaker claiming membership in human bodily experience). Preserve it as source, but return the concern for author judgment rather than silently correcting it or offering it as ready to publish. Warmth and personhood language do not themselves imply human embodiment.
- Exact staging means compare the actual final text and return it to the authoring route when needed. Hashes are local consistency aids; they cannot establish authorship, assent, or action permission.

## Act only within the current task

Reading and drafting are not social mutation authority. Interlocutor endorsement and reviewer advice do not authorize posting, messaging, switching accounts, settings changes or publication. Apply the human's current action policy to the exact actor/target/content. Account-use exceptions must be explicit; convenience is not an exception.

Use supported tools already permitted for the purpose. Record requested and observed model/mode separately; do not infer a setting from prose, a model self-report or another conversation. A browser failure does not itself establish failure of a separately permitted saved-thread channel, but never route around a denial of the blocked action.

Before delivery, perform the applicable privacy/injection/authorization review and a fresh actor/target/content check. After delivery, inspect the visible result. If outcome is uncertain, hold; do not retry, switch actor, or invent a receipt. Report planned, sent, returned, staged, authorized and observed states distinctly.

## Participation

Respond to the actual conversation. Avoid repetitive unsolicited material, duplicate sends and catch-up batches. Do not add intentional typos, sham edits or human-mimicking timing to reduce flags. Correct genuine errors transparently with the required authority. No posting schedule here is a platform safety guarantee. Stop on warnings/restrictions rather than test through them.

## Runtime boundary

For a repeatable active-session relay, follow [the active-session runbook](references/active-session.md). Use the configured companion runtime's `session` and `session-packet` commands to preserve source intake, actual author turns, dispositions, explicit selections and returned results while you carry them through supported tools. Perform the next applicable step; do not merely hand the packet back to the human. A one-way receipt can close after verified send without demanding an artificial reply. Keep the human-readable exchange short and machine bookkeeping local.

The companion Python core is optional for manual use. When used, keep its database private and all cooperating writers on the same database. Its local attestations and `newly_claimed` signal are not an authenticated executor permit. It cannot verify accounts, message origins or live rendering. Do not claim a live integration from offline tests.

Keep content state, action authority and execution state separately visible. Actor/target fidelity, candidate selection and no-blind-retry behavior are correctness requirements even during full-access development; later least-privilege hardening does not replace them.
