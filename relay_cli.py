"""Local relay records only. This CLI has no external delivery capability."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from relay_core import RelayError, Store
from media_manifest import inspect_media, verify_media_manifest
from operator_packet import render_operator_packet
from relay_session import SessionStore, apply_session_request


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
    session = commands.add_parser("session", help="record or inspect a resumable operator session; never sends by itself")
    session.add_argument("--db", required=True, help="private shared SQLite path, outside release folder")
    session.add_argument("--request", required=True, help="JSON method and arguments for SessionStore")
    session_packet = commands.add_parser("session-packet", help="render the next supported-tool step or an author/result packet")
    session_packet.add_argument("--db", required=True)
    session_packet.add_argument("--session", required=True)
    session_packet.add_argument("--mode", choices=["intake", "next", "result"], default="next")
    session_packet.add_argument("--result", help="result ID, required with --mode result")
    action = commands.add_parser("action", help="record a local action plan or receipt; host tools perform external actions")
    action.add_argument("--db", required=True, help="same private delivery database used by the text/media core")
    action.add_argument("--request", required=True, help="JSON method and arguments for ActionStore")
    action_packet = commands.add_parser("action-packet", help="inspect an action plan and its supported-tool step; never executes it")
    action_packet.add_argument("--db", required=True, help="same private delivery database used by the text/media core")
    action_packet.add_argument("--action", required=True)
    source = commands.add_parser("source", help="record scoped host observations and progress; never reads a platform")
    source.add_argument("--db", required=True, help="existing private session database")
    source.add_argument("--request", required=True, help="JSON method and arguments for SourceStore")
    source_packet = commands.add_parser("source-packet", help="list pending observed items for one actor and stream")
    source_packet.add_argument("--db", required=True, help="existing private session database")
    source_packet.add_argument("--actor", required=True)
    source_packet.add_argument("--stream", required=True, choices=["notifications", "messages"])
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
        elif args.command == "session-packet":
            store = SessionStore(args.db)
            if args.mode == "intake":
                result = store.author_intake_packet(args.session)
            elif args.mode == "result":
                if not args.result:
                    raise ValueError("--result is required with --mode result")
                result = store.return_result_packet(args.session, args.result)
            else:
                result = store.next_step_packet(args.session)
        elif args.command == "session":
            request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
            store = SessionStore(args.db)
            result = apply_session_request(store, request)
        elif args.command in ("action", "action-packet"):
            from relay_actions import ActionStore, apply_action_request

            store = ActionStore(args.db)
            if args.command == "action-packet":
                result = store.action_packet(args.action)
            else:
                request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
                result = apply_action_request(store, request)
        elif args.command in ("source", "source-packet"):
            from relay_sources import SourceStore, apply_source_request

            store = SourceStore(args.db)
            if args.command == "source-packet":
                result = store.source_packet(args.actor, args.stream)
            else:
                request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
                result = apply_source_request(store, request)
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
