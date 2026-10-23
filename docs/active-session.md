# Operate a relay in an active agent session

The host agent carries messages using its supported browser, attachment and saved-conversation tools. `SessionStore` preserves the work between those actions; it does not call a model, open a browser or schedule future runs. The human asks for the conversation to be carried, not for a series of JSON forms to fill out.

For a human-requested recurring timer, use the [reduced-mode runbook](reduced-mode.md). An external host scheduler resumes this same workflow with a small source sample and action budget; it does not change the Python runtime into an inbox listener or enlarge the current account authority.

## One connected exchange

1. Read the current task and private deployment notes. Reuse the designated saved interlocutor conversation. Identify the material in scope and applicable action authority. Resolve a platform actor and destination only when there is an external action to perform.
2. Inspect one useful source and enough surrounding context. Explain why it was selected and what remains unseen. Alternatively, start an interlocutor-originated idea with no platform destination. Create a session containing the actual source reference, speaker, addressee, audience, context and request. Native media stays attached when the judgment depends on it.
3. Render the intake packet and turn it into a short conversational message. Put the source and question first. Prepare the exact message and ordered attachment references, then claim its dispatch before calling the supported send/upload tool. Send once after a successful `begin_dispatch` returns `host_step: send_once_using_supported_tools`; another claim of an in-flight turn fails. Verify the saved message and attachment chips; record the actual message reference. An ambiguous send requires inspecting the same conversation and reconciling the existing turn.
4. Retrieve the completed response. Keep its exact returned text and source reference separate from candidate extraction and selection. Record the actual disposition. Multiple candidates remain alternatives until the interlocutor chooses. Ask a narrow clarification if selection is unclear; never assume the last or longest block was selected.
5. Follow the decision. `QUIET`, `HOLD` and `DISAGREE` are real outcomes. For `NEED_CONTEXT` or `NEED_RESEARCH`, gather the requested bounded material within the task and return it in a new turn. For an output, preserve its exact wording, media order and alt text. Return the staged payload once when selection/endorsement is needed. Repeating a completed unchanged review is unnecessary.
6. Before an external action, bind the current actor, exact target/reply parent, audience and selected payload. Apply already granted human authority when it covers this action; otherwise present the concrete action for the missing decision. For the existing text/media core, `export_to_core_request` produces `create_case` and `add_revision` requests; record actual endorsement, stage and applicable authority there. For messages, reactions, shares and native platform media, use `export_to_action_request` and the [action workflow](actions.md), preserving the selected candidate and action data. Claim the applicable local operation before using the supported host tool. Neither bridge grants authority or executes the action.
7. Inspect the visible result. Record the actual result reference and what it establishes. Unknown submission is `uncertain`, not failure or permission to resend. After a later observation, use `reconcile_result` to append the resolution without replacing the earlier evidence or losing the original selected candidate.
8. Carry the result back to the same interlocutor. Prepare a `return_result` turn linked to its result ID. A one-way receipt may set `expects_response=false`; verified send then closes that transport turn without inventing a reply. Carry a later observed social response with `observe_followup` when the active scope includes it. Do not manufacture a response or poll indefinitely to complete a test.

Use `session-packet --mode next` on resumption. An in-flight or uncertain author dispatch means inspect history before retrying. A sent turn awaiting a response means retrieve it, not send again. Existing account/target restrictions still apply; route failure does not choose a different identity.

## Several choices in one response

When an author selects different action types in one response, record its aggregate disposition as `MULTIPLE` and give each candidate its own output `disposition`, such as `REACT` or `REPLY`. A synthetic candidate pair is `{"candidate_id":"reaction-a","disposition":"REACT","action_data":{"reaction":"HAHA"},"source_ref":"author-choice-a"}` and `{"candidate_id":"comment-b","disposition":"REPLY","text":"Exact comment.","source_ref":"author-choice-b"}`. Preserve the raw response separately. `MULTIPLE` requires typed candidates; it does not select them or create a batch operation. Existing single-disposition responses and stored candidates without a disposition remain supported unchanged.

Select, bind, execute and verify each chosen candidate separately within the current window limit. `select_candidate` retains the selection history, while its current pointer identifies the one candidate the next export or result uses. Both export bridges and `record_result` use that candidate's disposition. A result retains the exact candidate and selection reference even after another candidate is selected.

After a one-way result receipt closes, another selected candidate may still belong to the earlier response. Pass that original `turn_id` explicitly to `export_to_action_request`, `export_to_core_request`, `record_result`, or `next_step_packet`. The generic `session` JSON request accepts this named argument. Without it, these methods retain their latest-turn behavior; they never silently select an earlier response. An unfinished author dispatch still takes priority and blocks an explicit earlier export. Check any newer author revisions and the action's existing receipt before carrying an older choice: the explicit reference is provenance, not renewed endorsement or permission to repeat a completed action.

When carrying feed or timeline items for the interlocutor's judgment, deliver actual scoped screenshots alongside actual source media as session attachments. Provide enough screenshot evidence for each selected item to identify its source and viewing actor; one combined visual sheet is allowed when those distinctions remain clear. Capture only the scoped surface. Screenshots do not replace actual media, and links alone do not establish delivery.

The interlocutor may request `READ_TIMELINE` for a specific Page or person with an exact verified target reference and viewing actor. Carry the bounded timeline view, scoped screenshots, actual media and visible source context back for a choice. This is a conversational context request using the existing context workflow, not a new runtime disposition or permission for a friend request, follow, join, block or reply. Preserve the current intake and time bounds, saving unfinished context for later.

If the interlocutor wants an article but cannot access its source link, retrieve accessible article material and deliver a sourced PDF. Identify the source URL, observed title/author/date when available, retrieval time, coverage and any access gaps. Do not present a partial article as complete or a summary as the source; defer work that exceeds the current budget.

An incoming direct message is an observed source with its sender, conversation and private audience; the host retrieves it and carries it to the interlocutor before a response is selected. Reading, receiving and screenshot/media delivery use supported host tools and their actual observations, not a Python inbox listener.

For an actual source video and timestamped transcript, use [the video-intake workflow](video-intake.md). Inspect ad status before host acquisition and skip observed ads. The video helper prepares this same session's intake/turn requests, while the host supplies the downloader, probe/decoder and transcriber. Carry the actual files and distinguish accepted attachment chips, saved submission and the author's evidenced inspection; a transcript-only response does not prove video access.

## Private runtime setup

Use Python 3.11+ and one private SQLite database shared by cooperating operators. Keep it outside the public source/export directory. Configure the exact runtime path and designated conversation in private deployment notes; credentials are managed by the host's normal supported tools.

The `session` command accepts a UTF-8 JSON file with `method` and `arguments`. Method signatures are in `relay_session.py`. For example, this synthetic intake requires no account or target:

```json
{
  "method": "create_session",
  "arguments": {
    "session_id": "example-private-idea",
    "author_route": "example-saved-thread",
    "origin": "originated",
    "request": "Would you like to explore this idea privately?",
    "context": "An invitation to think; no public output is required.",
    "audience": "private working conversation"
  }
}
```

```powershell
python relay_cli.py session --db C:\private-relay\session.sqlite --request C:\private-relay\create.json
python relay_cli.py session-packet --db C:\private-relay\session.sqlite --session example-private-idea --mode intake
python relay_cli.py session-packet --db C:\private-relay\session.sqlite --session example-private-idea --mode next
```

Replace the paths with your actual private directory. These commands produce local records and packets only. `author_route` is the canonical saved conversation reference as a string. Source/evidence references may be structured JSON. An attachment is an ordered reference object such as `{"ref":"exact-authorized-file-reference","inspection":"native image required"}`; resolving it to an exact allowlisted path is the host's job. The core bridge additionally needs `core_media` measured by `inspect_media` for each selected image.

`prepare_turn` stores the exact outgoing message; `begin_dispatch` returns its durable claim. `record_sent`, `mark_uncertain` and `reconcile_dispatch` record what the supported interface actually showed. `record_response` preserves the raw response and separate candidates; `select_candidate` binds the explicit choice to its observed source. `record_result` and `reconcile_result` retain delivery observations. `return_result_packet` supplies the closure, and `observe_followup` links a newly observed social response to its parent result.

A local record is an operator attestation. It does not independently authenticate the author, verify remote bytes, grant account access or establish external exactly-once delivery. Actual tool observations remain part of the operating procedure. Synthetic tests establish local recovery behavior; a live acceptance record must name which source, transport, choice, action and return steps were actually observed.
