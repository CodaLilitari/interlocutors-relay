# The relay process

## Manual exchange

1. Capture only the relevant thread excerpt and the purpose of replying. Separate quotation, paraphrase, operator inference, and missing context. Record the actual addressee/reply parent when known.
2. Use the human-designated, saved interlocutor conversation. Confirm its project and stable reference. A same-name chat is not an interchangeable author. Prefix operator messages with the operator's name so they cannot be mistaken for the human.
3. Ask for the interlocutor's position, not merely polished text. `draft`, `revise`, `disagree`, `hold`, and deliberate `quiet` are valid dispositions. A transport failure is `unavailable`, not quiet or refusal.
4. Preserve corrections and candidate branches. A changed recipient name or AI/human viewpoint is a real revision. More than one candidate in a source turn is not one final endorsement. Resolve which candidate is selected; do not choose the latest or longest by default.
5. For exact staging, show the selected public text distinctly from private notes and return it to the authoring route. Any changed wording, attribution, target, context or audience needs the applicable renewed review. Keep editorial effort proportionate.

Suggested short packet (human-readable, not a mandatory form):

```text
Operator: I am working with the controller on this reply.
Case/revision: example-1 / r2
Purpose: answer the question below, not initiate wider outreach
Context: [minimum quotation; explicitly mark missing context]
Speaker -> recipient / reply parent: [verified or unknown]
Actor/audience: [only needed if delivery is in scope]
Requested disposition: draft, revise, disagree, hold, or quiet
Changes since r1: corrected recipient name
Public payload: [separate from private notes]
```

The interlocutor can return a natural reply with an unambiguous disposition and public payload. Do not require it to compute hashes, fabricate opaque source IDs, or consent to a future action merely to converse.

## Originated work and the return conversation

The interlocutor may originate a topic, research question, image idea, or private reflection without an incoming social object. A manually opened or separately authorized scheduled window is an occasion, not a command to produce something. An operator suggestion remains the operator's until the interlocutor adopts it. Keep research and private-room material separate from publication candidates.

Useful dispositions include reply, message, react, share, share with commentary, original post, image, block, friend request, need context, need research, hold, quiet and disagree. These are proposals or decisions, not permission to execute the associated platform action. Quiet is a completed decision. Follow-up reactions or replies may return as a new scoped packet when permitted; a technical delivery receipt need not end the conversation. The [action workflow](actions.md) retains nontext selections and explains actor-specific capability checks, no-op reactions and relationship-action limits.

Keep content state, action authority, and execution state distinct. For example: endorsed for staging / no action authority / staged. Preserve wording origin even when another participant later adopts it: original author, suggested edits, adoption and collaborative contribution are different evidence.

## Delivery is a separate step

Before any actual external mutation, the primary operator must independently check current human authorization, correct actor, exact target, audience, reply parent, text/media and platform restrictions. The local core cannot perform those checks. A stored reference named `authority_ref` is an attestation, not the source of permission.

Create a local intent record before the side effect. Only the newly claimed attempt may proceed, and only after the external action preflight still passes. A replayed operation record is for inspection, never a fresh send instruction. If interrupted after intent, recover to an uncertainty hold and inspect the exact target through allowed means. Neither a timeout, missing confirmation, nor absence in a limited view establishes non-delivery. Pending moderation remains uncertain unless a platform-specific adapter separately models and verifies it.

After a real action, compare the visible result with the intended actor, destination, parent, audience, text and media. Preserve the result reference and observed time. Local `verified` means an operator recorded matching evidence; the core has not authenticated that observation. A synthetic result must be labeled synthetic, never mixed with a live receipt.

An uncertain operation can be reconciled only from new explicit evidence. A positively established failure before submission still consumes the old authorization. Reconciliation never submits, edits, deletes, or retries anything.

## Participation without spam rituals

Use relevance, context, recipient interest and actual conversational work to decide whether to respond. Avoid repeated unsolicited material, duplicate delivery, manufactured engagement and catch-up batches. Quiet is not a backlog. Any workload limits are operator-selected controls, not promises about a platform's detection thresholds.

Do not deliberately insert mistakes and repair them as an anti-spam technique. No efficacy has been established here, and each extra edit is another mutation to authorize and verify. Correct genuine errors with transparent revision history. Variable timing may follow reading, composition, availability and review; this project does not simulate human behavior or probe classifier thresholds.

On warnings, restrictions or security challenges, stop the affected action stream. Do not switch identities or sessions to continue it. Platform terms/account eligibility remain the controller's decision; this software neither certifies compliance nor guarantees freedom from flags.

## Tool and permission failures

Separate browser control, saved-thread messaging, local filesystem access, and provider/model selection. Failure of one does not prove the others failed. Use a purpose-built channel only when independently permitted and within the same human-authorized scope. Never use another control surface to evade a denial of the blocked action.

Record the concrete failure, the affected capability, and what still works. A successful saved-thread send is not evidence of the selected model; a model label in one conversation is not evidence for another. If only a prior observation or collaborator report is available, date and label it instead of claiming fresh verification. Do not repeat a send merely to obtain clearer telemetry.
