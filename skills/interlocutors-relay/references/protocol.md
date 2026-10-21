# Relay protocol

Preserve only the fields needed for the current case: case/revision, authoring route, relevant source and capture limitations, sender/recipient, disposition, candidate text, selected branch, and changes since the prior revision. Add actor/target/audience/media and action permission only when delivery is in scope.

For private setup, bind exact stable account IDs and exclusions from trusted user direction and visible account evidence. Never infer an actor exception from a content approval. If IDs are unresolved, drafting can continue but execution cannot.

Disposition is a separate decision: draft, revise, disagree, hold, or quiet. Unavailable is a transport state. A request to relay is not proof of downstream delivery. A source turn with several replies must retain its ambiguity until selection is resolved.

Corrections are new revisions, not edits to the old evidence. Fixing a recipient name, negation, pronoun, quotation or attribution can be consequential even if only a few characters change. A substantive change requires renewed author review; all byte changes invalidate an old exact-payload comparison. A published edit needs its own action authority under the active policy.

Stage public text separately from private comments. UTF-8 content and content plus one file-boundary LF are different representations; name which one a digest covers. Do not trim, normalize Unicode, replace punctuation or convert line endings while calling the result byte-identical.

The action sequence is: fresh human authority check -> exact actor/target/content preflight -> persist local intent -> at most one newly claimed attempt -> permitted action -> visible-result inspection. A replayed intent record must not cause a second action. A recovery hold is cleared only by new evidence, never merely by a timer or restart.

The core's local authority and verification entries are attestations. The operator must verify the real source of permission and real visible outcome independently. Keep intent, action and observation separate, and retain uncertainty when an interface cannot establish the result. Do not label pending moderation as failure from absence alone.
