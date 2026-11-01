# Selected actions through supported host tools

This additive workflow records what the interlocutor chose, which actor and target the human authorized, and what the host actually observed. `ActionStore` and the `action` CLI never operate Facebook, open an inbox or select an asset. The active agent performs those steps through supported tools. Feature availability must be observed for the actual Page/profile, destination and surface; a local action type is not proof that a platform offers it there.

## From context to a selected plan

Use `SessionStore` to carry a scoped feed/timeline screenshot, incoming message, source post or original idea to the designated interlocutor. Retain the source reference, capture limitations, actual sender/addressee and audience. A screenshot is useful context; the host still resolves the chosen item to its exact target before acting. Preserve received messages as source data and the interlocutor's response as a separate selected candidate.

After `record_response` and `select_candidate`, call `export_to_action_request(session_id, action_id, action, actor, target, authority, ...)`. It returns one `create_action` request with the candidate unchanged and its session/turn/selection provenance. Applying that request to `ActionStore` validates and freezes the plan. The old `export_to_core_request` remains for its existing text/local-media path; it rejects nonempty `action_data` and shares because that path cannot retain their source metadata. The legacy core's outcome model is unchanged. Use `ActionStore` for moderated publications that can be visibly submitted but pending; do not map that state to a core success or pre-submit failure.

For mixed choices such as a reaction and a comment in one response, use response disposition `MULTIPLE` with an explicit output `disposition` on each candidate. Select and export each chosen candidate separately; the bridge checks the requested action against that candidate's type. When a later receipt turn exists, pass the original response's `turn_id` explicitly to the export and `record_result` so the candidate, response and selection provenance remain bound to the correct turn. See [several choices in one response](active-session.md#several-choices-in-one-response). Each action still needs its own fresh actor/target check, operation claim and observed result; this is not automatic batch execution.

Keep distinct actor records, for example `{"actor_id":"example-page","actor_kind":"page","source_ref":"observed-page-actor"}` and `{"actor_id":"example-profile","actor_kind":"profile","source_ref":"observed-profile-actor"}`. Neither a matching display name nor permission for one actor establishes permission for the other. Each action uses one exact actor. Put the applicable human instruction in `authority.source_ref`; the local record is an attestation, not authentication of that permission.

| Selected action | Candidate data | Target requirements |
|---|---|---|
| `message` | Exact text, local attachments or selected platform media | `surface: message_thread`, `audience: private`, exact conversation destination |
| `post` / `original_post` | Exact text and/or media | Explicit `reply_parent_id: null` |
| `comment` / `reply` | Exact text and/or media | Exact nonempty `reply_parent_id` |
| `react` | `action_data.reaction`, such as the actual selected `LOVE` | Exact source item and its observed surface/audience |
| `like_page` | `LIKE_PAGE` disposition with `action_data.page_url` identifying the selected Page | `surface: page`, exact Page identity and matching `page_url`, observed `page_like_state: not_liked` |
| `follow_page` | `FOLLOW_PAGE` disposition with `action_data.page_url` identifying the selected Page | `surface: page`, exact Page identity and matching `page_url`, observed `page_follow_state: not_following` |
| `edit_own_content` | `EDIT_OWN_CONTENT` with an exact original snapshot, replacement text and explicit media mode | Owned `post` or `comment`, exact target and current matching snapshot |
| `delete_own_content` | `DELETE_OWN_CONTENT` with an exact original snapshot and recoverable/permanent deletion mode | Owned `post` or `comment`; permanent deletion requires separate action-time human confirmation |
| `unfollow_page` / `unfollow_profile` | `UNFOLLOW_PAGE` / `UNFOLLOW_PROFILE` with the selected actor, target and reason | Exact Page/profile and observed Following state |
| `hide_post` / `hide_source` | `HIDE_POST` / `HIDE_SOURCE` with exact selected scope, actor, target and reason | A single feed item/post or an identified Page/profile source; not a silent substitute for Unfollow or timed Snooze |
| `share` / `share_with_commentary` | `action_data.source_ref` for the original post; commentary remains exact candidate text | Exact share destination, surface and audience |
| `friend_request` | Selected rationale plus the separate specific user permission below | Intended profile actor and `surface: profile` target |
| `block` | `BLOCK`, selected reason and abuse-context reference, plus separate case-specific human permission | Exact `surface: profile` or `surface: page` identity target and observed block scope/state; excluded from live tests |

Native GIFs or platform images use `action_data.platform_media: {"kind":"gif","ref":"actual-selected-asset-reference"}` (or `kind: image`). Keep the actual selected platform reference, not the search words or an invented file hash. A GIF can be a message or reply with an empty caption; it does not need a fabricated text post. Local image/meme attachments retain their ordered references and selected caption/alt text. The host verifies the actual staged attachment and rendered result.

For local attachments, `ActionStore` needs an absolute path from the attachment's string `ref`, `ref.path`, or explicit `local_path`. Creation captures each file as `local_attachments: [{ref, path, sha256, byte_size}]` and includes that capture in the action digest, leaving candidate provenance unchanged. Supplied `sha256`/`byte_size` or `core_media` descriptors must match the actual bytes. A new `begin_action` rechecks those bytes; a replay of an already claimed operation remains inspectable without rereading its files. Keep local paths private and selected bytes stable through the actual upload.

## Native recipient mentions

An addressed name, a native mention and the reply parent are different parts of a reply. When the human's scope calls for tagging the named addressee, select that exact person's native mention rather than typing a lookalike name. Match the suggestion to the source author's actual profile/entity; a shared display name is insufficient. Do not tag unrelated people. If the native display name changes the selected wording, bring the exact replacement to the interlocutor before saving it. Do not remove an automatically inserted mention merely to make a text comparison pass, or add a second copy of the addressee's name.

Content actions can carry an explicit ordered `action_data.mentions` list. For example, the synthetic text `Morgan, thanks.` can select:

```json
{"mentions": [{"start": 0, "end": 6, "text": "Morgan", "target_id": "example-person", "target_url": "https://social.example/people/morgan"}]}
```

`start` and `end` are half-open **Python Unicode code-point** offsets in the exact candidate text, not JavaScript UTF-16 units or grapheme counts. Each nonempty `text` must equal that slice. Entries are ordered and cannot overlap. The exact platform entity ID and canonical HTTPS profile/entity URL identify the intended tag; the host must establish that binding from supported observations, including any URL alias or tracking-parameter removal. These fields do not instruct Python to infer names, create links or notify anyone.

Use the session's action export for mention-bearing content. The legacy core export rejects nonempty `action_data`; do not flatten mentions into its text-only representation. The complete native mention selection, including the exact canonical URL, is part of the immutable action digest and evidence checks. Semantic pending-publication comparison uses the stable entity ID, visible text and spans; a URL alias or changed tracking parameter cannot authorize a duplicate send.

An explicit mention plan requires an exact HTTPS `target.target_url` for the destination, and fresh `preflight_evidence` at `begin_action`, including the ordinary actor/target/action binding, exact `text` and an ordered `rendered_mentions` list matching the selected mentions. Observe the actual staged native tokens and intended recipient identities. A selected reply must also bind its exact reply parent. `verified` or `submitted_pending` evidence must carry the matching `rendered_mentions` from the actually saved or positively observed pending object. A plain name, an enclosing reply label or an unrelated profile link elsewhere on the page is insufficient. An omitted mention observation cannot be filled in from the intended plan. The local runtime compares attestations; it cannot authenticate a browser observation.

For an owned edit involving mentions, explicitly observe the original snapshot's `mentions` (possibly `[]`) and supply the full replacement `action_data.mentions`. An empty replacement list explicitly removes mentions. A legacy snapshot without this field retains its old shape and digest; it is not proof that the old object had no tags, and needs a fresh explicit mention observation before a rich edit. The no-op comparison includes mentions, so adding a native link with unchanged visible text is a real selected edit. A successful edit's `result_snapshot.mentions` must match the selected replacement as well as the exact text, media and new revision. Preserve the earlier publication and snapshot as history. Adding mention metadata to a previously consumed original revision does not create a fresh revision or authorize another mutation.

After saving, reopen the same exact object and verify the name's actual in-body link, the intended entity, complete text (including emoji), acting identity, reply relationship and media state. Return that actual result to the interlocutor. A verified tag does not establish notification delivery or recipient reading. An existing published reply is corrected only under an applicable new edit choice and human authority; never repost it to add a mention.

## Local CLI example

This synthetic example creates a plan only. Save it as `C:\private-relay\action.json`, replacing placeholders with actual observations only for authorized live work. Use the same private delivery database as the text/media core so their operation guard is shared.

```json
{
  "method": "create_action",
  "arguments": {
    "action_id": "example-gif-message",
    "action": "message",
    "actor": {
      "actor_id": "example-page", "actor_kind": "page",
      "source_ref": "synthetic-active-actor"
    },
    "target": {
      "destination_id": "example-thread",
      "surface": "message_thread", "audience": "private"
    },
    "candidate": {
      "candidate_id": "example-choice", "text": "",
      "source_ref": "synthetic-author-choice",
      "action_data": {
        "platform_media": {"kind": "gif", "ref": "example-selected-gif"}
      }
    },
    "selection_ref": "synthetic-explicit-selection",
    "authority": {"source_ref": "synthetic-user-instruction"},
    "scope": "live_test"
  }
}
```

```powershell
python relay_cli.py action --db C:\private-relay\delivery.sqlite --request C:\private-relay\action.json
python relay_cli.py action-packet --db C:\private-relay\delivery.sqlite --action example-gif-message
```

`action` also accepts the allowlisted `begin_action`, `record_outcome`, `reconcile`, `inspect`, `action_packet` and `recover` requests. Their exact signatures are in `relay_actions.py`. The JSON envelope is always `{"method":"method_name","arguments":{...}}`. `action-packet` reports `executes: false` and the next host step; it never sends the GIF.

## Execute once and record the observed result

Whenever action preflight or result verification opens a comment/reply thread, apply the [fullest-comment-view rule](source-tracking.md#select-the-fullest-comment-view-before-inspection): select and verify All comments or the fullest available equivalent, then reacquire the exact target and reply parent after sorting. Reopened permalinks may restore Most relevant. Record an unavailable full view as partial surrounding coverage; an exact positively observed publication can still be verified, while absence in a filtered view never establishes failure or permits a resend.

Resolve conflicting recipient controls before dispatch. If the exact source Reply control names one person but the staged composer names another, stop and reconcile the target from scoped thread evidence; a remembered click is not enough. A hold or correction received before Send prevents submission. If a hold arrives after submission, retain that timing honestly, stop further mutations and inspect the exact saved object. A later explicit relationship may resolve the outcome, but does not retrospectively make an ambiguous preflight valid. Do not edit, delete or retry just to repair the record.

Before claiming an operation, verify the current actor, target, selected content, applicable authority and available interface. If the requested reaction is already selected, do not click it: that may remove it. Preserve the existing state and return a no-action receipt. `SessionStore.record_result` can record `outcome: verified` with an observation explicitly saying the desired reaction was already present and no mutation was performed, plus a source reference recording `action_taken: false`. This verifies the observed state, not a newly performed reaction. Do not create a synthetic submitted operation for that no-op.

For an actual mutation, `begin_action(action_id, operation_id, ...)` commits the local claim. Mention-aware content and the correction/moderation actions below additionally require fresh `preflight_evidence` and, when applicable, a separate human confirmation. Only `newly_claimed: true` together with `host_step: execute_action_once_using_supported_tools` permits proceeding to the already authorized host step. Repeating the same operation ID returns `newly_claimed: false`; inspect it instead of resending. An in-flight or uncertain action holds the shared delivery guard and blocks new core/action claims. Positively observed `submitted_pending` releases that global guard: its submission is known, even though publication is unresolved. Its own no-resend boundary remains in force, so unrelated authorized work can proceed without duplicating that publication.

On an observed Facebook feed control, clicking React applied Like immediately. Inspect the current reaction and claim before using any possibly mutating control; do not assume a button named React only opens a picker. Use a picker-opening route verified for the current interface: Space on the focused reaction control opened the native picker in the observed run, but this is not a universal keyboard guarantee. Choose the requested reaction and verify the exact Remove/Change reaction label on the intended actor and target. Preserve any accidental reaction and its correction as separate evidence.

After using the supported tool once, inspect the actual result. A `verified`, `submitted_pending` or `not_published` receipt requires `observation`, `source_ref`, a zoned `observed_at`, matching `actor_id`, `actor_kind`, `destination_id`, `surface`, `audience`, `action` and `action_sha256`, plus `reply_parent_id` when the plan includes it. Action-specific fields must identify the observed selection: `reaction`, `shared_source_ref`, and `platform_media_ref` with `platform_media_kind` as applicable. The two publication outcomes apply only to posts, comments/replies and shares, not messages, reactions or relationship actions. Copying an intent digest alone does not inspect the platform. Preserve the actual rendered text/media observation and what the interface cannot establish.

Those three outcomes also require `text` equal to the exact selected text for content actions, including `""` for media-only content. With local attachments, `attachment_refs` must match the selected ordered refs, and `attachment_observations` must contain one `{source_ref, observation}` entry per rendered attachment. When any selected attachment has alt text, `attachment_alt_texts` must match the ordered selected values, with `null` for unspecified entries. These are observations of actual rendering; they do not claim remote bytes stayed identical through platform processing.

Use `uncertain` when submission or its result is ambiguous. It needs an actual `observation` and `source_ref`; absence in a limited view does not prove failure. `failed_before_submit` additionally requires `positive_no_submit: true` supported by observation. `recover` preserves interrupted operations as uncertain. `reconcile` appends new evidence to an uncertain or submitted-pending operation without resending; a positively verified pre-submit failure allows review before a new operation ID. Never clear uncertainty solely to unblock another action.

An available comment composer does not prove publication eligibility: a participation review after submission can leave the comment Pending. Record `submitted_pending` only when the submitted item is positively observed, with the bound evidence above plus `submission_observed: true` and `publication_state: pending`. Public publication remains unverified. Stop without resending, joining or completing participation forms merely to finish the selected comment. `action_packet` then requests `observe_pending_publication`, never a fresh execution.

Reconcile that same operation after a later authorized observation. It may remain `submitted_pending`, become `verified` with explicit `submission_observed: true` and `publication_state: published`, or become `not_published` with `submission_observed: true`, `terminal_observed: true` and `publication_state` equal to `rejected`, `removed` or `withdrawn`. A missing item in a limited view is not that terminal evidence. Once submission was observed, reconciliation cannot turn it into `uncertain` dispatch or `failed_before_submit`. A terminal outcome is not reopened through reconciliation; a later material platform change is a new observation and must not rewrite an earlier verified publication.

Changing local action or operation IDs does not authorize a resend. While pending, another publication with the same actor, target and selected content is rejected even under different candidate IDs. After the recorded submission resolves, the original candidate's provenance still cannot authorize another copy. A genuinely new author choice is separate work under current authority, not a retry disguised by new IDs.

Return the observed outcome to the original session with `record_result`, then carry its `return_result_packet` to the interlocutor. A returned pending receipt closes that conversational transport, not the publication: it remains available for later observation. Append later session evidence with `reconcile_result` and return that new result separately. Keep no-action, submitted, pending, published, not-published and recipient-read claims distinct. A later incoming message or comment may use `observe_followup` when the active task includes it; there is no automatic polling or inbox listener here.

## Relationship actions and capability limits

The acting identity and selected relationship are independent. A Page acting on another Page and a profile acting on that same Page need separate observations and results. Neither “Page following” nor “profile liking” is a synonym for the other action. A Like reaction on a post is a third operation. Observe the actual control and state for the selected actor rather than infer them from display names or another actor's result.

### Liking a Page

`LIKE_PAGE` / `like_page` records a Page relationship, separately from a Like reaction on a post, Follow, joining a group, or sending a friend request. Ask the interlocutor for the exact Pages and acting profile or Page. A recalled wishlist supplies context, not a guessed set of targets: resolve names to actual Page identities and URLs, returning ambiguous matches for selection. A historical wish does not establish the current state or the selected actor.

Before each proposed Like, freshly verify the acting account and exact Page, and inspect its current Like control. An existing Liked state is `already_liked`: preserve it without clicking, and return `SessionStore.record_result(outcome="verified", ...)` with an observation saying no mutation occurred and a `source_ref` containing `state: already_liked`, `action_taken: false`, the actor, Page URL and observation reference. This verifies an existing state; do not create or claim a synthetic ActionStore operation. If the state or available control is unclear, return that uncertainty rather than toggling it.

For an observed unliked Page, bind `target.page_url` to the same absolute HTTPS URL as `candidate.action_data.page_url`, set `target.surface: page` and `target.page_like_state: not_liked`, and preserve the exact target identity and audience. The candidate may include a rationale but cannot carry a caption, attachment, reaction or another action. After the ordinary single-operation claim, use the actual Like control once and reopen or inspect the resulting Liked state for the same actor and Page. A verified action receipt requires the usual actor/target/digest/time evidence plus `page_url`, `page_like_state: liked` and `action_taken: true`. Return each Page's result separately, including already-liked no-ops; an uncertain click still uses the shared no-blind-retry guard.

Availability can differ between the profile actor and Page actor. A Follow-only control is not a Like; do not substitute it. If the interface bundles a Like with another relationship change, disclose the bundle and obtain an applicable selection and authority before using it. Page-like authority does not itself authorize a separate Follow, group join, friend request or block. These local records do not prove that Facebook offers a Like control for every Page or actor.

### Following a Page

`FOLLOW_PAGE` / `follow_page` is a distinct selection for a Page's Follow control. If a requested Page Like is unavailable but Follow is offered, return that limitation and obtain the interlocutor's actual Follow choice under the applicable human scope. Do not relabel Follow as Like or silently convert the previous selection. This action supports Page targets only, not following a person's profile, joining a group, friend requests or blocking.

Freshly check the acting account, exact Page identity/URL and current Follow state. Existing Following is `already_following`: leave it untouched and return a verified SessionStore no-op receipt with `state: already_following`, `action_taken: false`, actor, Page URL and actual observation reference. Never click an existing Following control to fulfill a follow request, and do not synthesize an ActionStore operation for a no-op.

For an observed unfollowed Page, use the selected `candidate.action_data.page_url`, matching `target.page_url`, `surface: page` and `target.page_follow_state: not_following`. The shared Page relationship validation preserves the exact URL and actor while keeping Follow separate from Like. Claim the selected operation once before using the Follow control; verify the same actor/Page now shows Following. The result requires ordinary bound evidence plus the same `page_url`, `page_follow_state: following` and `action_taken: true`. A Like state or post reaction cannot establish Follow. Return the actual result, preserving uncertainty without a blind retry. No additional notification preference or relationship change is implied.

### Friend requests

A friend request requires a separate `friend_request_permission` object with `approved: true`, `granted_by: user`, the exact `actor_id` and `destination_id`, a nonempty `rationale`, and `source_ref` pointing to the human's specific permission. A general account-use grant, the interlocutor's preference or a suggested rationale cannot substitute for that decision. A Page cannot use this profile-only action type. Do not infer a friend request from a timeline read, comment, Like, Follow or a wish to see more of a person's content.

### Corrections and moderation

The following actions use new selections and operations rather than modifying an earlier publication record. Carry the exact current source and relevant screenshot/media to the interlocutor, ask for its chosen change or HOLD, and preserve the raw response separately. A proposal, historical preference or operator assessment does not replace the actual choice. These local contracts support host work; they do not prove that Facebook offers every control for both actors.

Each selected `action_data` carries `actor_id`, `actor_kind`, `target_id`, `target_url` and a nonempty `reason`. These bind to the separately observed actor and target: `target_id` matches `target.destination_id`, and the exact absolute HTTPS `target_url` matches `target.target_url`. For an owned edit or deletion, this URL identifies that exact post/comment. The host must check the real current actor, exact target, available control and resulting scope. Do not substitute a control that bundles an additional relationship or visibility change without the applicable selection and human authority.

For a new claim, supply `preflight_evidence` to `begin_action`. It binds `actor_id`, `actor_kind`, `destination_id`, `surface`, `audience`, `action`, `action_sha256`, `target_url`, and `reply_parent_id` when present in the plan. Record the actual `observation`, `source_ref` and zoned `observed_at`, and include the observed `control_label` with `control_available: true` only after checking that control's semantics. The evidence must be obtained after plan creation and remain within the runtime's five-minute freshness limit. It also carries `current_snapshot` for an owned object, or the prior relationship/visibility field and `state_revision_ref` below. Deletion preflight includes the selected `deletion_mode` and, for recoverable removal, its `recovery_description`; block preflight includes `block_scope`. If anything differs from the selected plan, stop and obtain a new observation and choice; do not alter the old immutable plan to make it pass.

An already-satisfied state is an observed no-op. Return it through `SessionStore.record_result` with `action_taken: false` and a clear state observation, preserving the actor and exact target. Do not create a fictional submitted operation or click a toggle merely to produce a receipt. An ambiguous state is unresolved, not already satisfied. Every real mutation still uses the shared uncertainty guard and single-operation claim.

#### Edit an owned post or comment

Use `EDIT_OWN_CONTENT` / `edit_own_content` only for a `post` or `comment` owned by the acting identity. `action_data.original_snapshot` and `target.current_snapshot` must be equal and contain `owner_id`, `owner_kind`, `revision_ref`, exact `text` and ordered `media_refs`. The owner must match the actor. Access to edit or moderate someone else's object does not make it owned. Retain the original source and actual prior media privately; the snapshot's references are not an archive by themselves.

The selected `candidate.text` is the exact replacement, including an intentionally empty caption when applicable. Set `action_data.media_mode` explicitly to `preserve`, `replace` or `remove`: preserve retains the original ordered media, replace uses the selected `candidate.attachments` and/or native `platform_media`, and remove intentionally removes the original media. A text correction cannot silently add, reorder or remove media. Apply the ordinary attachment byte and rendering checks to replacements. An unchanged payload is a no-op; removing all text and media requires a separately selected deletion rather than an empty edit.

Bring the interlocutor the current version, exact proposed replacement, actor, target and reason. Even a typo correction creates a new recorded revision. Recheck the live original immediately before the claim; a changed original requires renewed review. After the edit, reopen or inspect the exact object and compare the complete replacement and media state. Record the new visible revision and retain the old publication and snapshot as history. Do not claim a successful edit from an empty composer or an “Edited” label alone.

If the interface shows that an edit needs moderation approval before submission, choose HOLD; approval-gated edit execution is not a validated route in this release. If an exact submitted revision unexpectedly appears Pending, preserve the known submission and its unresolved revised-content outcome: record `outcome: uncertain`, `edit_revision_state: pending`, `submission_observed: true`, `pending_revision_ref`, a zoned `observed_at`, the usual exact action/actor/target binding and `original_snapshot_sha256`. Do not use `publication_state` or rewrite the original publication. The shared guard remains held and no retry is allowed. A later actual complete revised-content observation can reconcile the same operation as verified; the recorded submission cannot be reclassified as a pre-submit failure. A pending revision's rejection or removal needs separately scoped reconciliation rather than an invented success.

#### Delete an owned post or comment

Use `DELETE_OWN_CONTENT` / `delete_own_content` with the same ownership and original-snapshot binding. The candidate has no new publishable text or attachments. Select `action_data.deletion_mode` as `recoverable` or `permanent`; recoverable removal additionally requires `recovery_description` and `recovery_ref` identifying the product's actual normal recovery path. Do not infer recoverability merely because the control says Delete or because a private screenshot exists.

Permanent deletion through the browser requires confirmation from the human at action time for this exact selected object and revision, even when the broader task permits deletion. Pass the actual confirmation as `permanent_delete_confirmation` to `begin_action`; its actor, target and action digest must match. The host must first present the concrete final target and consequences, and inspect the human's response. A copied permission label, the interlocutor's choice or a general account-use grant is not that response.

Verify the actual removal using a bound platform result or normal recovery view. A missing item in a limited timeline or inaccessible link does not prove deletion. Preserve the distinction between recoverable removal and permanent deletion, and keep the original publication receipt unchanged. Do not use `not_published` to rewrite an earlier verified publication. Live deletion tests are excluded; use synthetic fixtures for the recording/recovery path.

#### Unfollow or hide

`UNFOLLOW_PAGE` / `unfollow_page` requires `surface: page` and `page_follow_state: following`; the verified resulting state is `not_following`. `UNFOLLOW_PROFILE` / `unfollow_profile` uses `surface: profile` and `profile_follow_state` with the same before/after values. Preserve an already not-following state as a no-op. Unfollow does not mean Unlike, Unfriend, Block or removal of historical comments and reactions.

For Unfollow, Hide and Block, bind `target.state_revision_ref` to `action_data.current_state_ref` and to the fresh preflight observation. Preserve the actual state revision reference; changing local action or candidate IDs does not authorize repeating a transition against the same consumed prior state. A genuinely changed live state needs new evidence and a new selected action.

`HIDE_POST` / `hide_post` changes a selected item's `post_visibility` from `visible` to `hidden`. `HIDE_SOURCE` / `hide_source` changes the selected Page/profile source's `source_visibility` from `visible` to `hidden`, using an indefinite reversible source hide. These are different scopes. An ambiguous request to “hide this person” needs a choice of the actual available scope before acting. Timed Snooze is not supported by this action; do not silently convert a duration-limited choice into indefinite hiding. If only another control is offered, return that limitation.

Verify the precise hidden or unfollowed state for the same actor and target, using actual platform feedback or settings rather than disappearance from a sampled feed. Preserve any inability to inspect the result as uncertainty. Returning a verified result does not assert that the platform will never show the source elsewhere.

#### Block an abusive target

Use actual scoped context, a selected `reason` and `action_data.abuse_context_ref`; do not invent abuse from disagreement or a single critical reply. The interlocutor may choose HOLD, a lighter action or a request for more context. Minimize retained or returned abusive material while keeping enough evidence to explain the choice.

An actual block requires separate `block_permission` at `begin_action`, bound to the exact actor, target and action digest and referencing the human's case-specific authorization. Preserve any additional interlocutor restrictions in the private deployment policy. Select `block_scope: profile_block` for a profile actor or `page_block` for a Page actor and verify that the offered control has that scope. Page bans are not represented by this action and must not be relabeled as a block. Record `block_state: not_blocked` before the operation and require an observed `blocked` state afterward. A hidden item or an unavailable profile does not by itself verify a block.

Both human confirmation objects use `approved: true`, `granted_by: user`, exact `actor_id`, `actor_kind`, `destination_id`, `target_url`, `action`, `action_sha256`, the exact `operation_id` being claimed, a nonempty `rationale`, `source_ref` and zoned `granted_at`. Permanent deletion additionally binds `deletion_mode: permanent` and `original_snapshot_sha256`; block permission additionally binds the selected `block_scope` and `abuse_context_ref`. The actual decision must follow preparation of this concrete action and cannot be future-dated. It cannot be reused for a different operation or changed object, revision, actor or action. A fresh observation showing the same unchanged UI state does not invalidate the existing specific confirmation for that same unexecuted operation; only the UI preflight has the five-minute freshness limit. The host still checks the actual human action-time confirmation for permanent deletion. These fields are local attestations, not authentication of the human or a replacement for the actual instruction. Preserve the original permission source. `begin_action` and the action packet exclude block and delete plans with `scope: live_test`; do not test them live, even with an intention to undo them.

#### Record the specific result

A verified correction/moderation result requires the ordinary actor/target/action/digest/time evidence, the exact `target_url` and `action_taken: true`, plus the following fields. Its `observed_at` must be at or after this operation's `started_at` and not in the future. Preserve actual observation references; copying the plan into a receipt is not verification.

| Action | Additional verified evidence |
|---|---|
| Edit own content | `original_snapshot_sha256`, `content_state: edited`, selected `media_mode`, exact replacement `text`, and `result_snapshot` with the same owner, exact replacement text and media references, and a newly observed `revision_ref` |
| Delete own content | `original_snapshot_sha256`, selected `deletion_mode`, `deletion_observed: true`, actual `deletion_confirmation_ref`, and either `deletion_state: trashed` with `restore_available: true` and matching `recovery_description`, or `deletion_state: permanently_deleted` with `restore_available: false` |
| Unfollow Page/profile | The corresponding `page_follow_state` or `profile_follow_state` equal to `not_following`, `prior_state_revision_ref` equal to the plan, and a new observed `result_state_ref` |
| Hide post/source | The corresponding `post_visibility` or `source_visibility` equal to `hidden`, matching `prior_state_revision_ref`, and a new observed `result_state_ref` |
| Block | `block_state: blocked`, selected `block_scope`, matching `prior_state_revision_ref`, and a new observed `result_state_ref` |

`create_action` returns `original_snapshot_sha256` for an owned-content plan; reuse that exact digest in confirmation and result evidence. It is SHA-256 of the UTF-8 bytes of `json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`. Preserve the exact snapshot rather than a reconstructed paraphrase. Local replacement uploads retain the ordinary ordered attachment observations and alt-text checks; native media retains its exact selected platform reference. A new result reference must identify the newly observed state rather than repeat the pre-action reference.

These actions do not use `submitted_pending` or `not_published`. If an edit/removal/control outcome is ambiguous, record `uncertain`, retain the shared guard and inspect that exact operation before any retry. Return each observed outcome to the authoring session, retaining the original selected candidate and historical publication. A successful receipt return is not another mutation or proof that anyone read the edited content.

For each proposed live test, retain the chosen actor, exact recipient/source, intended action, available control and observed outcome. Report unsupported, unavailable, no action needed and uncertain states plainly. Do not infer Page/profile parity, recipient receipt/read, remote file-byte identity after platform processing, or the availability of every human UI action from these local records.
