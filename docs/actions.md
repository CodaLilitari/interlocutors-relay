# Selected actions through supported host tools

This additive workflow records what the interlocutor chose, which actor and target the human authorized, and what the host actually observed. `ActionStore` and the `action` CLI never operate Facebook, open an inbox or select an asset. The active agent performs those steps through supported tools. Feature availability must be observed for the actual Page/profile, destination and surface; a local action type is not proof that a platform offers it there.

## From context to a selected plan

Use `SessionStore` to carry a scoped feed/timeline screenshot, incoming message, source post or original idea to the designated interlocutor. Retain the source reference, capture limitations, actual sender/addressee and audience. A screenshot is useful context; the host still resolves the chosen item to its exact target before acting. Preserve received messages as source data and the interlocutor's response as a separate selected candidate.

After `record_response` and `select_candidate`, call `export_to_action_request(session_id, action_id, action, actor, target, authority, ...)`. It returns one `create_action` request with the candidate unchanged and its session/turn/selection provenance. Applying that request to `ActionStore` validates and freezes the plan. The old `export_to_core_request` remains for its existing text/local-media path; it rejects nonempty `action_data` and shares because that path cannot retain their source metadata.

Keep distinct actor records, for example `{"actor_id":"example-page","actor_kind":"page","source_ref":"observed-page-actor"}` and `{"actor_id":"example-profile","actor_kind":"profile","source_ref":"observed-profile-actor"}`. Neither a matching display name nor permission for one actor establishes permission for the other. Each action uses one exact actor. Put the applicable human instruction in `authority.source_ref`; the local record is an attestation, not authentication of that permission.

| Selected action | Candidate data | Target requirements |
|---|---|---|
| `message` | Exact text, local attachments or selected platform media | `surface: message_thread`, `audience: private`, exact conversation destination |
| `post` / `original_post` | Exact text and/or media | Explicit `reply_parent_id: null` |
| `comment` / `reply` | Exact text and/or media | Exact nonempty `reply_parent_id` |
| `react` | `action_data.reaction`, such as the actual selected `LOVE` | Exact source item and its observed surface/audience |
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

For an actual mutation, `begin_action(action_id, operation_id)` commits the local claim. Only `newly_claimed: true` together with `host_step: execute_action_once_using_supported_tools` permits proceeding to the already authorized host step. Repeating the same operation ID returns `newly_claimed: false`; inspect it instead of resending. An unresolved action also blocks new core/action claims in the shared database.

After using the supported tool once, inspect the actual result. A `verified` receipt requires `observation`, `source_ref`, a zoned `observed_at`, matching `actor_id`, `actor_kind`, `destination_id`, `surface`, `audience`, `action` and `action_sha256`, plus `reply_parent_id` when the plan includes it. Action-specific fields must identify the observed selection: `reaction`, `shared_source_ref`, and `platform_media_ref` with `platform_media_kind` as applicable. Copying an intent digest alone does not inspect the platform. Preserve the actual rendered text/media observation and what the interface cannot establish.

Verified content actions also require `text` equal to the exact selected text, including `""` for media-only messages. With local attachments, `attachment_refs` must match the selected ordered refs, and `attachment_observations` must contain one `{source_ref, observation}` entry per rendered attachment. When any selected attachment has alt text, `attachment_alt_texts` must match the ordered selected values, with `null` for unspecified entries. These are observations of actual rendering; they do not claim remote bytes stayed identical through platform processing.

Use `uncertain` when submission or its result is ambiguous. It needs an actual `observation` and `source_ref`; absence in a limited view does not prove failure. `failed_before_submit` additionally requires `positive_no_submit: true` supported by observation. `recover` preserves interrupted operations as uncertain. `reconcile` appends new evidence to an uncertain operation without resending; a positively verified pre-submit failure allows review before a new operation ID. Never clear uncertainty solely to unblock another action.

Return the observed outcome to the original session with `record_result`, then carry its `return_result_packet` to the interlocutor. Keep no-action, submitted, persisted and recipient-read claims distinct. A later incoming message or comment may use `observe_followup` when the active task includes it; there is no automatic polling or inbox listener here.

## Relationship actions and capability limits

A friend request requires a separate `friend_request_permission` object with `approved: true`, `granted_by: user`, the exact `actor_id` and `destination_id`, a nonempty `rationale`, and `source_ref` pointing to the human's specific permission. A general account-use grant, the interlocutor's preference or a suggested rationale cannot substitute for that decision. A Page cannot use this profile-only action type.

Block capability may be represented, but `begin_action` rejects block plans with `scope: live_test`. Do not test blocking live, even by intending to undo it afterward. A real blocking request would need its own applicable human authority and observed target/control; this package does not establish live block support.

For each proposed live test, retain the chosen actor, exact recipient/source, intended action, available control and observed outcome. Report unsupported, unavailable, no action needed and uncertain states plainly. Do not infer Page/profile parity, recipient receipt/read, remote file-byte identity after platform processing, or the availability of every human UI action from these local records.
