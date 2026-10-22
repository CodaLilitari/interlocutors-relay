# Local quickstart

For the v0.2.0 connected host-agent procedure, start with [the active-session runbook](active-session.md). A directly approved live Page reply has been exercised through that supported-tool workflow; [validation](validation.md) records its scope separately from the local tests. The examples below explain the platform-bound staging core and exact media checks used at the delivery step.

Python 3.11+ and the standard library are sufficient. Run commands from the package directory. Everything below is local; no command sends, posts, authenticates or uploads.

```powershell
python -m unittest discover -s tests -v
python relay_cli.py demo
```

## A minimal stage-only exchange

Run this Python example in a private working context. The database is in memory and all identifiers are synthetic.

```python
from relay_core import Store
from operator_packet import render_operator_packet

store = Store(':memory:')
try:
    binding = {
        'author_route': 'example-interlocutor',
        'actor_id': 'example-page', 'actor_kind': 'page',
        'destination_id': 'example-thread', 'action': 'comment',
        'context_ref': 'example-context',
        'reply_parent_id': 'example-parent', 'audience': 'public',
    }
    policy = {
        'allowed_actor_ids': ['example-page'],
        'forbidden_actor_ids': ['example-controller'],
        'protected_destinations': {'example-thread': 'page'},
    }
    store.create_case('case-1', binding, policy)
    text = 'I read the question differently.'
    store.add_revision('case-1', 'r1', text, 'example-source')
    print(render_operator_packet(store, 'case-1', 'r1', 'author_review'))
    # Record only an actually obtained exact endorsement in a real case.
    store.endorse('case-1', 'r1', 'example-return', text)
    store.stage('case-1', 'r1')
    print(render_operator_packet(store, 'case-1', 'r1', 'operator_preflight'))
finally:
    store.close()
```

For a persistent private database, the CLI's `apply` command accepts a JSON object with `method` and `arguments` corresponding to the documented Store methods. Use `packet --db <private-db> --case <id> --revision <id> --mode author_review` to emit a minimal candidate packet. `operator_preflight` additionally requires a staged selection and explicit actor/target, reply parent and audience. It returns a plan for an operator, never live permission. A local `authorize` record is only an attestation; actual external authorization must come from the deployment's trusted human channel.

## Exact image files

```python
from media_manifest import inspect_media, verify_media_manifest

# Replace the placeholder with one exact, authorized absolute image path.
capture = inspect_media([{
    'file_id': 'candidate-1',
    'path': '/exact/authorized/candidate.png',
    'alt_text': 'The actual visible content, checked by an operator.',
}])
media = capture['public_media']
# Keep capture private. Only media is suitable for the revision metadata.
# store.add_revision(..., media=media)
# After actual image/caption/alt review, pass the reviewed revision's
# full_payload_sha256 as exact_full_payload_sha256 to store.endorse(...).
# For media, caption-only endorsement is deliberately rejected.
verify_media_manifest(capture)  # immediately before a separately allowed upload
```

`inspect-media --request <private-json-list>` provides the same inspection. Its stdout contains private paths and must remain private. `verify-media --request <captured-json>` expects the inner inspection result, not the CLI's outer `ok/local_only/result` envelope. PNG, JPEG, GIF and WebP headers are supported, bounded to 16 images, 10 MiB each and 160 MiB total. Video and full image decoding are not implemented. Reopening and hashing reduces accidental substitution risk; it cannot freeze a file between the last check and an external uploader's read. Keep the selected bytes stable and verify the actual attachment/result.

New captures use manifest version 2 with decimal-string device/inode identities, so JSON tools retain large values exactly. The verifier also accepts unchanged version 1 integer manifests. Preserve an old receipt as recorded; do not round its numbers or convert it through a lossy JSON implementation. Media identifiers and alt text must satisfy the same contract as the core, and author text is never silently normalized.

An author's text-only reply cannot prove inspection of an image. Return real attachment pixels for visual review, and keep `NEED_ATTACHMENT` when the route cannot deliver them. New crop, overlay, caption, ordering or alt text means a new revision. Runtime files, manifests and packets may contain private data and never belong in a public export.
