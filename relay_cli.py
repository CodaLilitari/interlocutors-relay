"""Local relay records only. This CLI has no external delivery capability."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from relay_core import RelayError, Store
from media_manifest import inspect_media, verify_media_manifest
from operator_packet import render_operator_packet


METHODS = {
    "create_case", "add_revision", "endorse", "stage", "authorize",
    "revoke_authority", "begin_delivery", "record_outcome", "reconcile",
    "inspect", "recover",
}


def apply_request(store, request):
    if not isinstance(request, dict) or set(request) - {"method", "arguments"}:
        raise ValueError("request must contain only method and arguments")
    method = request.get("method")
    arguments = request.get("arguments", {})
    if method not in METHODS or not isinstance(arguments, dict):
        raise ValueError("unknown method or invalid arguments")
    return getattr(store, method)(**arguments)


def demo():
    """Synthetic stage-only round trip; no live identities, approvals or actions."""
    store = Store(":memory:")
    try:
        binding = {
            "author_route": "synthetic-saved-interlocutor",
            "actor_id": "example-page", "actor_kind": "page",
            "destination_id": "example-thread", "action": "comment",
            "context_ref": "synthetic-context-1",
            "reply_parent_id": "example-parent", "audience": "public",
        }
        policy = {"allowed_actor_ids": ["example-page"],
                  "forbidden_actor_ids": ["example-controller-profile"],
                  "protected_destinations": {"example-thread": "page"}}
        store.create_case("demo-1", binding, policy)
        store.add_revision("demo-1", "r1", "Maria, I read the question differently.", "synthetic-source-1")
        store.add_revision("demo-1", "r2", "Mara, I read the question differently.", "synthetic-correction-2", "r1")
        store.endorse("demo-1", "r2", "synthetic-exact-return-2", "Mara, I read the question differently.")
        store.stage("demo-1", "r2")
        result = store.inspect("demo-1")
        return {"mode": "synthetic_stage_only", "external_actions": 0,
                "content_state": "endorsed_for_staging", "action_authority": "none",
                "execution_state": "staged", "record": result}
    finally:
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="run an in-memory synthetic stage-only example")
    apply = commands.add_parser("apply", help="record one local JSON request; never executes externally")
    apply.add_argument("--db", required=True, help="private shared SQLite path, outside release folder")
    apply.add_argument("--request", required=True, help="exact UTF-8 JSON request file")
    packet = commands.add_parser("packet", help="render a minimized local review or operator packet")
    packet.add_argument("--db", required=True)
    packet.add_argument("--case", required=True)
    packet.add_argument("--revision", required=True)
    packet.add_argument("--mode", choices=["author_review", "operator_preflight"], default="author_review")
    media = commands.add_parser("inspect-media", help="hash exact image paths from a private JSON list; never uploads")
    media.add_argument("--request", required=True)
    verify = commands.add_parser("verify-media", help="recheck a trusted private captured manifest; never uploads")
    verify.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    store = None
    try:
        if args.command == "demo":
            result = demo()
        elif args.command in ("inspect-media", "verify-media"):
            request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
            result = inspect_media(request) if args.command == "inspect-media" else verify_media_manifest(request)
        elif args.command == "packet":
            store = Store(args.db)
            result = render_operator_packet(store, args.case, args.revision, args.mode)
        else:
            request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
            store = Store(args.db)
            result = apply_request(store, request)
        print(json.dumps({"ok": True, "local_only": True, "result": result}, ensure_ascii=True, indent=2))
        return 0
    except (RelayError, ValueError, TypeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "local_only": True, "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
