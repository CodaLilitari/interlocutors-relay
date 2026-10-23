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
| `share` / `share_with_commentary` | `action_data.source_ref` for the original post; commentary remains exact candidate text | Exact share destination, surface and audience |
| `friend_request` | Selected rationale plus the separate specific user permission below | Intended profile actor and `surface: profile` target |
| `block` | Selected decision and context | Exact `surface: profile` or `surface: page` identity target; excluded from live tests |

Native GIFs or platform images use `action_data.platform_media: {"kind":"gif","ref":"actual-selected-asset-reference"}` (or `kind: image`). Keep the actual selected platform reference, not the search words or an invented file hash. A GIF can be a message or reply with an empty caption; it does not need a fabricated text post. Local image/meme attachments retain their ordered references and selected caption/alt text. The host verifies the actual staged attachment and rendered result.

For local attachments, `ActionStore` needs an absolute path from the attachment's string `ref`, `ref.path`, or explicit `local_path`. Creation captures each file as `local_attachments: [{ref, path, sha256, byte_size}]` and includes that capture in the action digest, leaving candidate provenance unchanged. Supplied `sha256`/`byte_size` or `core_media` descriptors must match the actual bytes. A new `begin_action` rechecks those bytes; a replay of an already claimed operation remains inspectable without rereading its files. Keep local paths private and selected bytes stable through the actual upload.

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

Before claiming an operation, verify the current actor, target, selected content, applicable authority and available interface. If the requested reaction is already selected, do not click it: that may remove it. Preserve the existing state and return a no-action receipt. `SessionStore.record_result` can record `outcome: verified` with an observation explicitly saying the desired reaction was already present and no mutation was performed, plus a source reference recording `action_taken: false`. This verifies the observed state, not a newly performed reaction. Do not create a synthetic submitted operation for that no-op.

For an actual mutation, `begin_action(action_id, operation_id)` commits the local claim. Only `newly_claimed: true` together with `host_step: execute_action_once_using_supported_tools` permits proceeding to the already authorized host step. Repeating the same operation ID returns `newly_claimed: false`; inspect it instead of resending. An in-flight or uncertain action holds the shared delivery guard and blocks new core/action claims. Positively observed `submitted_pending` releases that global guard: its submission is known, even though publication is unresolved. Its own no-resend boundary remains in force, so unrelated authorized work can proceed without duplicating that publication.

On an observed Facebook feed control, clicking React applied Like immediately. Inspect the current reaction and claim before using any possibly mutating control; do not assume a button named React only opens a picker. Use a picker-opening route verified for the current interface: Space on the focused reaction control opened the native picker in the observed run, but this is not a universal keyboard guarantee. Choose the requested reaction and verify the exact Remove/Change reaction label on the intended actor and target. Preserve any accidental reaction and its correction as separate evidence.

After using the supported tool once, inspect the actual result. A `verified`, `submitted_pending` or `not_published` receipt requires `observation`, `source_ref`, a zoned `observed_at`, matching `actor_id`, `actor_kind`, `destination_id`, `surface`, `audience`, `action` and `action_sha256`, plus `reply_parent_id` when the plan includes it. Action-specific fields must identify the observed selection: `reaction`, `shared_source_ref`, and `platform_media_ref` with `platform_media_kind` as applicable. The two publication outcomes apply only to posts, comments/replies and shares, not messages, reactions or relationship actions. Copying an intent digest alone does not inspect the platform. Preserve the actual rendered text/media observation and what the interface cannot establish.

Those three outcomes also require `text` equal to the exact selected text for content actions, including `""` for media-only content. With local attachments, `attachment_refs` must match the selected ordered refs, and `attachment_observations` must contain one `{source_ref, observation}` entry per rendered attachment. When any selected attachment has alt text, `attachment_alt_texts` must match the ordered selected values, with `null` for unspecified entries. These are observations of actual rendering; they do not claim remote bytes stayed identical through platform processing.

Use `uncertain` when submission or its result is ambiguous. It needs an actual `observation` and `source_ref`; absence in a limited view does not prove failure. `failed_before_submit` additionally requires `positive_no_submit: true` supported by observation. `recover` preserves interrupted operations as uncertain. `reconcile` appends new evidence to an uncertain or submitted-pending operation without resending; a positively verified pre-submit failure allows review before a new operation ID. Never clear uncertainty solely to unblock another action.

An available comment composer does not prove publication eligibility: a participation review after submission can leave the comment Pending. Record `submitted_pending` only when the submitted item is positively observed, with the bound evidence above plus `submission_observed: true` and `publication_state: pending`. Public publication remains unverified. Stop without resending, joining or completing participation forms merely to finish the selected comment. `action_packet` then requests `observe_pending_publication`, never a fresh execution.

Reconcile that same operation after a later authorized observation. It may remain `submitted_pending`, become `verified` with explicit `submission_observed: true` and `publication_state: published`, or become `not_published` with `submission_observed: true`, `terminal_observed: true` and `publication_state` equal to `rejected`, `removed` or `withdrawn`. A missing item in a limited view is not that terminal evidence. Once submission was observed, reconciliation cannot turn it into `uncertain` dispatch or `failed_before_submit`. A terminal outcome is not reopened through reconciliation; a later material platform change is a new observation and must not rewrite an earlier verified publication.

Changing local action or operation IDs does not authorize a resend. While pending, another publication with the same actor, target and selected content is rejected even under different candidate IDs. After the recorded submission resolves, the original candidate's provenance still cannot authorize another copy. A genuinely new author choice is separate work under current authority, not a retry disguised by new IDs.

Return the observed outcome to the original session with `record_result`, then carry its `return_result_packet` to the interlocutor. A returned pending receipt closes that conversational transport, not the publication: it remains available for later observation. Append later session evidence with `reconcile_result` and return that new result separately. Keep no-action, submitted, pending, published, not-published and recipient-read claims distinct. A later incoming message or comment may use `observe_followup` when the active task includes it; there is no automatic polling or inbox listener here.

## Relationship actions and capability limits

### Liking a Page

`LIKE_PAGE` / `like_page` records a Page relationship, separately from a Like reaction on a post, Follow, joining a group, or sending a friend request. Ask the interlocutor for the exact Pages and acting profile or Page. A recalled wishlist supplies context, not a guessed set of targets: resolve names to actual Page identities and URLs, returning ambiguous matches for selection. A historical wish does not establish the current state or the selected actor.

Before each proposed Like, freshly verify the acting account and exact Page, and inspect its current Like control. An existing Liked state is `already_liked`: preserve it without clicking, and return `SessionStore.record_result(outcome="verified", ...)` with an observation saying no mutation occurred and a `source_ref` containing `state: already_liked`, `action_taken: false`, the actor, Page URL and observation reference. This verifies an existing state; do not create or claim a synthetic ActionStore operation. If the state or available control is unclear, return that uncertainty rather than toggling it.

For an observed unliked Page, bind `target.page_url` to the same absolute HTTPS URL as `candidate.action_data.page_url`, set `target.surface: page` and `target.page_like_state: not_liked`, and preserve the exact target identity and audience. The candidate may include a rationale but cannot carry a caption, attachment, reaction or another action. After the ordinary single-operation claim, use the actual Like control once and reopen or inspect the resulting Liked state for the same actor and Page. A verified action receipt requires the usual actor/target/digest/time evidence plus `page_url`, `page_like_state: liked` and `action_taken: true`. Return each Page's result separately, including already-liked no-ops; an uncertain click still uses the shared no-blind-retry guard.

Availability can differ between the profile actor and Page actor. A Follow-only control is not a Like; do not substitute it. If the interface bundles a Like with another relationship change, disclose the bundle and obtain an applicable selection and authority before using it. Page-like authority does not itself authorize a separate Follow, group join, friend request or block. These local records do not prove that Facebook offers a Like control for every Page or actor.

### Following a Page

`FOLLOW_PAGE` / `follow_page` is a distinct selection for a Page's Follow control. If a requested Page Like is unavailable but Follow is offered, return that limitation and obtain the interlocutor's actual Follow choice under the applicable human scope. Do not relabel Follow as Like or silently convert the previous selection. This action supports Page targets only, not following a person's profile, joining a group, friend requests or blocking.

Freshly check the acting account, exact Page identity/URL and current Follow state. Existing Following is `already_following`: leave it untouched and return a verified SessionStore no-op receipt with `state: already_following`, `action_taken: false`, actor, Page URL and actual observation reference. Never click an existing Following control to fulfill a follow request, and do not synthesize an ActionStore operation for a no-op.

For an observed unfollowed Page, use the selected `candidate.action_data.page_url`, matching `target.page_url`, `surface: page` and `target.page_follow_state: not_following`. The shared Page relationship validation preserves the exact URL and actor while keeping Follow separate from Like. Claim the selected operation once before using the Follow control; verify the same actor/Page now shows Following. The result requires ordinary bound evidence plus the same `page_url`, `page_follow_state: following` and `action_taken: true`. A Like state or post reaction cannot establish Follow. Return the actual result, preserving uncertainty without a blind retry. No additional notification preference or relationship change is implied.

A friend request requires a separate `friend_request_permission` object with `approved: true`, `granted_by: user`, the exact `actor_id` and `destination_id`, a nonempty `rationale`, and `source_ref` pointing to the human's specific permission. A general account-use grant, the interlocutor's preference or a suggested rationale cannot substitute for that decision. A Page cannot use this profile-only action type.

Block capability may be represented, but `begin_action` rejects block plans with `scope: live_test`. Do not test blocking live, even by intending to undo it afterward. A real blocking request would need its own applicable human authority and observed target/control; this package does not establish live block support.

For each proposed live test, retain the chosen actor, exact recipient/source, intended action, available control and observed outcome. Report unsupported, unavailable, no action needed and uncertain states plainly. Do not infer Page/profile parity, recipient receipt/read, remote file-byte identity after platform processing, or the availability of every human UI action from these local records.
