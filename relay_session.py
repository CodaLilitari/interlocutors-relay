"""Resumable conversations for an active Interlocutor's Relay host agent.

This additive SQLite companion stores source context and the *actual* messages
the host carries through supported tools. It never opens a browser or performs
an external action. A dispatch claim must be committed before the host sends;
an interrupted claim requires observation and reconciliation before retrying.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relay_core import ConflictError, NotFoundError, StateError, ValidationError


DISPOSITIONS = {
    "REPLY", "REACT", "SHARE", "SHARE_WITH_COMMENTARY", "ORIGINAL_POST", "IMAGE",
    "NEED_CONTEXT", "NEED_RESEARCH", "HOLD", "QUIET", "DISAGREE",
}
NON_OUTPUT = {"NEED_CONTEXT", "NEED_RESEARCH", "HOLD", "QUIET", "DISAGREE"}
SESSION_METHODS = {
    "create_session", "prepare_turn", "begin_dispatch", "record_sent",
    "mark_uncertain", "reconcile_dispatch", "record_response", "select_candidate",
    "record_result", "reconcile_result", "observe_followup", "inspect", "author_intake_packet",
    "next_step_packet", "return_result_packet", "export_to_core_request",
}
PACKET_SCHEMA = "interlocutors-relay/session-packet/v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _string(value: Any, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValidationError(f"{label} must be a {'possibly empty' if empty else 'nonempty'} string")
    return value  # Source messages are preserved, not normalized into public text.


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValidationError("session values must be JSON serializable") from exc


def _ref(value: Any, label: str) -> Any:
    if not isinstance(value, (str, dict)) or not value or (isinstance(value, str) and not value.strip()):
        raise ValidationError(f"{label} must be a nonempty reference string or object")
    _json(value)
    return value


def _attachments(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("attachments must be an ordered list of exact reference objects")
    for item in value:
        if not isinstance(item, dict):
            raise ValidationError("each attachment must be an object with an exact ref")
        _ref(item.get("ref"), "attachment.ref")
    _json(value)
    return value


def _packet(mode: str, session: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "schema": PACKET_SCHEMA, "mode": mode, "session_id": session["session_id"],
        "author_route": session["author_route"], "local_only": True, "executes": False,
        **fields,
    }


class SessionStore:
    """Durable conversation state, separate from platform-bound core cases.

    References and evidence are operator observations, not authenticated proof.
    Use one turn per exact outbound message; subsequent edits use a new turn.
    Methods return JSON dictionaries suitable for the CLI or an agent packet.
    """

    def __init__(self, db_path: str | Path):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path), timeout=10, isolation_level=None,
                                   check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS relay_sessions (
                session_id TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS relay_session_turns (
                ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES relay_sessions(session_id),
                turn_id TEXT NOT NULL, document TEXT NOT NULL,
                UNIQUE(session_id, turn_id));
            CREATE TABLE IF NOT EXISTS relay_session_results (
                ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES relay_sessions(session_id),
                result_id TEXT NOT NULL, document TEXT NOT NULL,
                UNIQUE(session_id, result_id));
        """)

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _write(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def _session(self, session_id: str) -> dict[str, Any]:
        row = self._db.execute("SELECT document FROM relay_sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise NotFoundError("session not found")
        return json.loads(row["document"])

    def _turn(self, session_id: str, turn_id: str) -> dict[str, Any]:
        row = self._db.execute(
            "SELECT document FROM relay_session_turns WHERE session_id=? AND turn_id=?",
            (session_id, turn_id)).fetchone()
        if row is None:
            raise NotFoundError("turn not found for session")
        return json.loads(row["document"])

    def _save_turn(self, session_id: str, turn: dict[str, Any]) -> dict[str, Any]:
        self._db.execute("UPDATE relay_session_turns SET document=? WHERE session_id=? AND turn_id=?",
                         (_json(turn), session_id, turn["turn_id"]))
        return turn

    def _session_document(self, session_id, author_route, origin, request, source,
                          context, addressee, audience, attachments) -> dict[str, Any]:
        _string(session_id, "session_id")
        _string(author_route, "author_route")
        if origin not in {"originated", "observed"}:
            raise ValidationError("origin must be originated or observed")
        if source is not None:
            _ref(source, "source")
        elif origin == "observed":
            raise ValidationError("observed sessions require the actual source reference")
        document = {
            "session_id": session_id, "created_at": _now(), "origin": origin,
            "author_route": author_route, "source": source,
            "context": _string(context, "context", empty=True),
            "addressee": addressee, "audience": audience,
            "request": _string(request, "request"), "attachments": _attachments(attachments),
            "parent": None,
        }
        _json(document)
        return document

    def _insert_session(self, document: dict[str, Any]) -> None:
        try:
            self._db.execute("INSERT INTO relay_sessions VALUES (?,?)",
                             (document["session_id"], _json(document)))
        except sqlite3.IntegrityError as exc:
            raise ConflictError("session_id already exists") from exc

    def create_session(self, session_id: str, author_route: Any, origin: str, request: str,
                       source: Any = None, context: str = "", addressee: Any = None,
                       audience: Any = None, attachments: Any = None) -> dict[str, Any]:
        """Start private/originated or observed work without a platform target."""
        document = self._session_document(session_id, author_route, origin, request, source,
                                          context, addressee, audience, attachments)
        with self._write():
            self._insert_session(document)
        return document

    def inspect(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            document = self._session(session_id)
            for key, table in (("turns", "relay_session_turns"), ("results", "relay_session_results")):
                document[key] = [json.loads(row[0]) for row in self._db.execute(
                    f"SELECT document FROM {table} WHERE session_id=? ORDER BY ordinal", (session_id,))]
            return document

    def author_intake_packet(self, session_id: str) -> dict[str, Any]:
        session = self.inspect(session_id)
        return _packet("author_intake", session,
                       **{key: session[key] for key in ("origin", "source", "context", "addressee",
                                                       "audience", "request", "attachments", "parent")},
                       available_dispositions=sorted(DISPOSITIONS), content_state="awaiting_author",
                       action_authority="not_recorded", execution_state="not_dispatched")

    def prepare_turn(self, session_id: str, turn_id: str, exact_message: str,
                     attachments: Any = None, purpose: str = "author_intake",
                     result_id: str | None = None, expects_response: bool = True) -> dict[str, Any]:
        """Freeze the exact message before tool use. This does not claim a send."""
        _string(turn_id, "turn_id")
        _string(exact_message, "exact_message")
        if purpose not in {"author_intake", "continuation", "return_result"}:
            raise ValidationError("unsupported turn purpose")
        if (purpose == "return_result") != (result_id is not None):
            raise ValidationError("return_result turns require result_id, other turns exclude it")
        if not isinstance(expects_response, bool):
            raise ValidationError("expects_response must be true or false")
        turn = {"turn_id": turn_id, "prepared_at": _now(), "purpose": purpose,
                "result_id": result_id, "exact_message": exact_message,
                "expects_response": expects_response,
                "attachments": _attachments(attachments), "state": "prepared",
                "attempts": [], "sent_message_ref": None, "response": None,
                "selected_candidate_id": None, "selections": []}
        with self._write():
            session = self.inspect(session_id)
            if any(item["state"] not in {"responded", "closed"} for item in session["turns"]):
                raise StateError("finish or reconcile the existing turn before preparing another")
            if result_id is not None:
                if not any(item["result_id"] == result_id for item in session["results"]):
                    raise NotFoundError("result not found for session")
                if any(item["result_id"] == result_id for item in session["turns"]):
                    raise StateError("this result already has a return turn; resume it")
            try:
                self._db.execute("INSERT INTO relay_session_turns(session_id,turn_id,document) VALUES (?,?,?)",
                                 (session_id, turn_id, _json(turn)))
            except sqlite3.IntegrityError as exc:
                raise ConflictError("turn_id already exists for session") from exc
        return turn

    def begin_dispatch(self, session_id: str, turn_id: str, attempt_id: str) -> dict[str, Any]:
        """Commit before sending. Never repeat this call to retry an unknown send."""
        _string(attempt_id, "attempt_id")
        with self._write():
            turn = self._turn(session_id, turn_id)
            if turn["state"] not in {"prepared", "not_sent"}:
                raise StateError("dispatch already started; inspect and reconcile, do not resend")
            if any(item["attempt_id"] == attempt_id for item in turn["attempts"]):
                raise ConflictError("retry requires a new attempt_id after a verified not_sent outcome")
            turn["state"] = "in_flight"
            turn["attempts"].append({"attempt_id": attempt_id, "began_at": _now(),
                                     "state": "in_flight", "observations": []})
            self._save_turn(session_id, turn)
            session = self._session(session_id)
        return _packet("dispatch", session, turn_id=turn_id, attempt_id=attempt_id,
                       exact_message=turn["exact_message"], attachments=turn["attachments"],
                       execution_state="in_flight", host_step="send_once_using_supported_tools")

    def _dispatch_outcome(self, session_id, turn_id, outcome, evidence, sent_message_ref=None):
        _ref(evidence, "evidence")
        if outcome == "sent":
            _ref(sent_message_ref, "sent_message_ref")
        elif sent_message_ref is not None:
            raise ValidationError("only a sent outcome can include sent_message_ref")
        with self._write():
            turn = self._turn(session_id, turn_id)
            if turn["state"] not in {"in_flight", "uncertain"}:
                raise StateError("dispatch reconciliation requires an in-flight or uncertain turn")
            turn["state"] = "closed" if outcome == "sent" and not turn.get("expects_response", True) else outcome
            turn["sent_message_ref"] = sent_message_ref
            attempt = turn["attempts"][-1]
            attempt["state"] = outcome
            attempt["observations"].append({"outcome": outcome, "observed_at": _now(),
                                            "evidence": evidence, "sent_message_ref": sent_message_ref})
            return self._save_turn(session_id, turn)

    def record_sent(self, session_id: str, turn_id: str, sent_message_ref: Any, evidence: Any) -> dict[str, Any]:
        return self._dispatch_outcome(session_id, turn_id, "sent", evidence, sent_message_ref)

    def mark_uncertain(self, session_id: str, turn_id: str, evidence: Any) -> dict[str, Any]:
        return self._dispatch_outcome(session_id, turn_id, "uncertain", evidence)

    def reconcile_dispatch(self, session_id: str, turn_id: str, outcome: str, evidence: Any,
                           sent_message_ref: Any = None) -> dict[str, Any]:
        """Record an actual history check; elapsed time alone proves no outcome."""
        if outcome not in {"sent", "not_sent", "uncertain"}:
            raise ValidationError("outcome must be sent, not_sent, or uncertain")
        return self._dispatch_outcome(session_id, turn_id, outcome, evidence, sent_message_ref)

    def record_response(self, session_id: str, turn_id: str, response_ref: Any,
                        exact_response: str, disposition: str,
                        candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Retain raw author response and an explicit operator-recorded disposition.

        Candidate extraction is separate from selection. ``source_ref`` records
        where each extracted candidate came from; no last/longest-text heuristic
        or automatic endorsement is applied.
        """
        _ref(response_ref, "response_ref")
        _string(exact_response, "exact_response")
        if disposition not in DISPOSITIONS:
            raise ValidationError("unsupported disposition")
        if candidates is None:
            candidates = []
        if not isinstance(candidates, list):
            raise ValidationError("candidates must be a list")
        checked, ids = [], set()
        for candidate in candidates:
            if not isinstance(candidate, dict) or set(candidate) - {"candidate_id", "text", "attachments", "action_data", "source_ref"}:
                raise ValidationError("candidate contains unsupported fields")
            candidate_id = _string(candidate.get("candidate_id"), "candidate_id")
            if candidate_id in ids:
                raise ValidationError("candidate_id values must be unique within a response")
            ids.add(candidate_id)
            item = {"candidate_id": candidate_id, "text": _string(candidate.get("text", ""), "candidate.text", empty=True),
                    "attachments": _attachments(candidate.get("attachments")),
                    "action_data": candidate.get("action_data", {}),
                    "source_ref": _ref(candidate.get("source_ref"), "candidate.source_ref"),
                    "response_ref": response_ref}
            if not isinstance(item["action_data"], dict):
                raise ValidationError("candidate.action_data must be an object")
            if not item["text"].strip() and not item["attachments"] and not item["action_data"]:
                raise ValidationError("empty candidate; record a non-output disposition instead")
            checked.append(item)
        if disposition in NON_OUTPUT and checked:
            raise ValidationError("non-output dispositions have no selected public candidates")
        response = {"response_ref": response_ref, "exact_response": exact_response,
                    "disposition": disposition, "candidates": checked, "recorded_at": _now()}
        _json(response)
        with self._write():
            turn = self._turn(session_id, turn_id)
            if turn["state"] != "sent":
                raise StateError("record the actual sent-message reference before its response")
            turn["response"] = response
            turn["state"] = "responded"
            return self._save_turn(session_id, turn)

    def select_candidate(self, session_id: str, turn_id: str, candidate_id: str,
                         selection_ref: Any) -> dict[str, Any]:
        _ref(selection_ref, "selection_ref")
        with self._write():
            turn = self._turn(session_id, turn_id)
            if turn["state"] != "responded":
                raise StateError("selection requires a recorded author response")
            if not any(item["candidate_id"] == candidate_id for item in turn["response"]["candidates"]):
                raise NotFoundError("candidate not found in this response")
            turn["selected_candidate_id"] = candidate_id
            turn["selections"].append({"candidate_id": candidate_id, "selection_ref": selection_ref,
                                       "selected_at": _now()})
            return self._save_turn(session_id, turn)

    @staticmethod
    def _selection(session):
        if not session["turns"]:
            raise StateError("session has no response or selected candidate")
        turn = session["turns"][-1]
        if turn["state"] != "responded" or turn["selected_candidate_id"] is None:
            raise StateError("latest turn has no explicit candidate selection")
        candidate = next(item for item in turn["response"]["candidates"]
                         if item["candidate_id"] == turn["selected_candidate_id"])
        return turn, candidate

    def record_result(self, session_id: str, result_id: str, outcome: str, observation: str,
                      source_ref: Any, action_authority: Any = "not_recorded") -> dict[str, Any]:
        """Record what the host actually observed, including uncertain outcomes."""
        _string(result_id, "result_id")
        if outcome not in {"verified", "uncertain", "failed_before_submit"}:
            raise ValidationError("result outcome must be verified, uncertain, or failed_before_submit")
        result = {"result_id": result_id, "outcome": outcome,
                  "observation": _string(observation, "observation"),
                  "source_ref": _ref(source_ref, "source_ref"), "recorded_at": _now(),
                  "action_authority": action_authority}
        with self._write():
            session = self.inspect(session_id)
            turn, candidate = self._selection(session)
            result.update({"turn_id": turn["turn_id"], "disposition": turn["response"]["disposition"],
                           "candidate": candidate, "selection_ref": turn["selections"][-1]["selection_ref"]})
            try:
                self._db.execute("INSERT INTO relay_session_results(session_id,result_id,document) VALUES (?,?,?)",
                                 (session_id, result_id, _json(result)))
            except sqlite3.IntegrityError as exc:
                raise ConflictError("result_id already exists; retain the earlier observation") from exc
        return result

    def reconcile_result(self, session_id: str, prior_result_id: str, result_id: str,
                         outcome: str, observation: str, source_ref: Any) -> dict[str, Any]:
        """Append actual outcome evidence to an uncertain result's original choice.

        Returning uncertainty to the author, or receiving HOLD, does not erase
        the selected payload behind that result. This method observes an earlier
        action; it does not resubmit it or create fresh action authority.
        """
        _string(result_id, "result_id")
        if outcome not in {"verified", "uncertain", "failed_before_submit"}:
            raise ValidationError("result outcome must be verified, uncertain, or failed_before_submit")
        _string(observation, "observation")
        _ref(source_ref, "source_ref")
        with self._write():
            session = self.inspect(session_id)
            prior = next((item for item in session["results"] if item["result_id"] == prior_result_id), None)
            if prior is None:
                raise NotFoundError("prior result not found for session")
            if prior["outcome"] != "uncertain":
                raise StateError("only an uncertain result needs outcome reconciliation")
            if any(item.get("reconciles_result_id") == prior_result_id for item in session["results"]):
                raise StateError("this result already has newer evidence; reconcile its latest result")
            result = {**prior, "result_id": result_id, "reconciles_result_id": prior_result_id,
                      "outcome": outcome, "observation": observation, "source_ref": source_ref,
                      "recorded_at": _now()}
            try:
                self._db.execute("INSERT INTO relay_session_results(session_id,result_id,document) VALUES (?,?,?)",
                                 (session_id, result_id, _json(result)))
            except sqlite3.IntegrityError as exc:
                raise ConflictError("result_id already exists; retain the earlier observation") from exc
        return result

    def return_result_packet(self, session_id: str, result_id: str) -> dict[str, Any]:
        session = self.inspect(session_id)
        result = next((item for item in session["results"] if item["result_id"] == result_id), None)
        if result is None:
            raise NotFoundError("result not found for session")
        returns = [turn for turn in session["turns"] if turn["result_id"] == result_id]
        return _packet("return_result", session, result=result, source=session["source"],
                       context=session["context"], addressee=session["addressee"], audience=session["audience"],
                       content_state="selected_candidate", action_authority=result["action_authority"],
                       execution_state=result["outcome"],
                       return_turn=returns[0] if returns else None,
                       host_step="prepare_return_turn" if not returns else "resume_existing_return_turn")

    def observe_followup(self, parent_session_id: str, parent_result_id: str, session_id: str,
                         source: Any, context: str, request: str, addressee: Any = None,
                         audience: Any = None, attachments: Any = None) -> dict[str, Any]:
        """Carry a newly observed reply/reaction into a linked author intake."""
        with self._write():
            parent = self.inspect(parent_session_id)
            result = next((item for item in parent["results"] if item["result_id"] == parent_result_id), None)
            if result is None:
                raise NotFoundError("parent result not found")
            document = self._session_document(session_id, parent["author_route"], "observed", request,
                                              source, context, addressee, audience, attachments)
            document["parent"] = {"session_id": parent_session_id, "result_id": parent_result_id,
                                  "result_source_ref": result["source_ref"], "result_outcome": result["outcome"],
                                  "candidate": result["candidate"]}
            self._insert_session(document)
        return document

    def next_step_packet(self, session_id: str) -> dict[str, Any]:
        session = self.inspect(session_id)
        if not session["turns"]:
            return self.author_intake_packet(session_id) | {"host_step": "prepare_author_intake"}
        turn = session["turns"][-1]
        packet = _packet("next_step", session, turn=turn, action_authority="not_recorded",
                         execution_state=turn["state"], content_state="awaiting_author")
        steps = {"prepared": "begin_dispatch", "not_sent": "begin_new_attempt",
                 "in_flight": "reconcile_before_retry", "uncertain": "reconcile_before_retry",
                 "sent": "retrieve_author_response"}
        if turn["state"] in steps:
            return packet | {"host_step": steps[turn["state"]]}
        superseded = {result["reconciles_result_id"] for result in session["results"] if "reconciles_result_id" in result}
        unreturned = [result for result in session["results"] if result["result_id"] not in superseded
                      and not any(item["result_id"] == result["result_id"] for item in session["turns"])]
        if unreturned:
            return packet | {"host_step": "prepare_return_result", "result_id": unreturned[-1]["result_id"]}
        if turn["state"] == "closed":
            return packet | {"host_step": "complete_without_response", "content_state": "receipt_sent",
                             "output_required": False}
        response = turn["response"]
        disposition = response["disposition"]
        packet.update(disposition=disposition, content_state="disposition_recorded")
        if disposition in NON_OUTPUT:
            step = {"NEED_CONTEXT": "gather_requested_context", "NEED_RESEARCH": "perform_scoped_research"}.get(
                disposition, "respect_disposition")
            return packet | {"host_step": step, "output_required": False}
        if not response["candidates"]:
            return packet | {"host_step": "request_candidate"}
        if turn["selected_candidate_id"] is None:
            return packet | {"host_step": "select_candidate_explicitly"}
        return packet | {"host_step": "carry_selected_candidate_under_applicable_authority",
                         "content_state": "selected_candidate", "candidate": self._selection(session)[1]}

    def export_to_core_request(self, session_id: str, case_id: str, revision_id: str,
                               binding: dict[str, Any], account_policy: Any = None) -> dict[str, Any]:
        """Bridge a chosen payload into strict core requests without executing them.

        Core metadata must already exist in attachment ``core_media`` fields.
        This bridge never invents file hashes, platform IDs, authority, or an
        endorsement. REACT/SHARE candidates remain in the session until a host
        supplies an appropriate action path; text/media core cannot model them.
        """
        from relay_core import _binding, _media, _text

        session = self.inspect(session_id)
        turn, candidate = self._selection(session)
        disposition = turn["response"]["disposition"]
        actions = {"REPLY": {"reply", "comment"}, "ORIGINAL_POST": {"post", "original_post"},
                   "IMAGE": {"post", "original_post", "reply", "comment"},
                   "SHARE_WITH_COMMENTARY": {"share_with_commentary"}}
        if disposition not in actions:
            raise StateError("this disposition has no text/media core export path; use its session packet")
        checked_binding, checked_policy = _binding(binding, {} if account_policy is None else account_policy)
        if checked_binding["action"] not in actions[disposition]:
            raise ValidationError("binding.action conflicts with the selected disposition")
        if checked_binding["author_route"] != session["author_route"]:
            raise ValidationError("binding.author_route must match the session's saved author route")
        media = _media([item.get("core_media") for item in candidate["attachments"]])
        text = _text(candidate["text"], allow_empty=bool(media))
        provenance = {"session_id": session_id, "turn_id": turn["turn_id"],
                      "response_ref": turn["response"]["response_ref"],
                      "candidate_source_ref": candidate["source_ref"],
                      "selection_ref": turn["selections"][-1]["selection_ref"]}
        return _packet("export_to_core", session, content_state="selected_candidate",
                       action_authority="not_recorded", execution_state="not_executed",
                       requests=[
                           {"method": "create_case", "arguments": {"case_id": case_id,
                            "binding": checked_binding, "account_policy": checked_policy}},
                           {"method": "add_revision", "arguments": {"case_id": case_id,
                            "revision_id": revision_id, "text": text, "media": media, "source_ref": provenance}},
                       ])


def apply_session_request(store: SessionStore, request: dict[str, Any]) -> Any:
    """Small allowlisted JSON dispatcher for CLI/host integration."""
    if not isinstance(request, dict) or set(request) - {"method", "arguments"}:
        raise ValidationError("request must contain only method and arguments")
    method, arguments = request.get("method"), request.get("arguments", {})
    if not isinstance(method, str) or method not in SESSION_METHODS or not isinstance(arguments, dict):
        raise ValidationError("unknown session method or invalid arguments")
    return getattr(store, method)(**arguments)
