"""Local, manual-preparation state core for Interlocutor's Relay.

The store records local attestations and delivery observations.  It never
contacts a provider, authenticates an identity, or proves an external action.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


LOCAL_NOTICE = (
    "All bindings, attestations, and outcome evidence are local records only; "
    "they do not authenticate an external source or prove an external action."
)
OUTCOMES = {"verified", "uncertain", "failed_before_submit"}
SCHEMA_VERSION = 2
_MEDIA_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MIME = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")


class RelayError(Exception):
    pass


class ValidationError(RelayError):
    pass


class StateError(RelayError):
    pass


class ConflictError(RelayError):
    pass


class NotFoundError(RelayError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_timestamp(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError("expires_at must be a nonempty UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("expires_at is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValidationError("expires_at must use UTC (+00:00 or Z)")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _future(timestamp: str) -> bool:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")) > datetime.now(timezone.utc)


def _zoned_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{label} must be a nonempty timezone-aware ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{label} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{label} must include a timezone offset")
    return value


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a nonempty string")
    _reject_surrogates(value, label)
    return value


def _reject_surrogates(value: Any, label: str) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValidationError(f"{label} must not contain lone Unicode surrogates")
    elif isinstance(value, dict):
        for key, nested in value.items():
            _reject_surrogates(key, label)
            _reject_surrogates(nested, label)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_surrogates(nested, label)


def _json(value: Any, label: str) -> str:
    _reject_surrogates(value, label)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be JSON-serializable") from exc


def _source_ref(value: Any, label: str) -> str:
    if value is None or isinstance(value, (bool, int, float)) or value == "" or value == {} or value == []:
        raise ValidationError(f"{label} must be nonempty")
    return _json(value, label)


def _text(text: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(text, str):
        raise ValidationError("text must be a string")
    if not text.strip() and not allow_empty:
        raise ValidationError("public text must not be empty; quiet is a disposition, not a payload")
    if text.startswith("\ufeff"):
        raise ValidationError("text must not have a UTF-8 BOM")
    _reject_surrogates(text, "text")
    if "\r" in text:
        raise ValidationError("text must use LF, never CRLF or bare CR")
    if text.endswith("\n"):
        raise ValidationError("text must exclude the transport-boundary LF")
    return text


def _hashes(text: str) -> tuple[str, str]:
    content = text.encode("utf-8")
    return hashlib.sha256(content).hexdigest(), hashlib.sha256(content + b"\n").hexdigest()


def _alt_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("media alt_text must be a nonempty string")
    if value.startswith("\ufeff") or "\r" in value:
        raise ValidationError("media alt_text must use Unicode text without BOM or CR")
    _reject_surrogates(value, "media alt_text")
    return value


def _media(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("media must be an ordered list of metadata objects")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    required = {"file_id", "sha256", "byte_size", "mime_type", "alt_text"}
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != required:
            raise ValidationError("each media item must contain only file_id, sha256, byte_size, mime_type, alt_text")
        file_id = item["file_id"]
        if not isinstance(file_id, str) or not _MEDIA_ID.fullmatch(file_id):
            raise ValidationError(f"media[{index}].file_id must be an opaque non-path identifier")
        if file_id in seen:
            raise ValidationError("media file_id values must be unique")
        seen.add(file_id)
        sha256 = item["sha256"]
        if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
            raise ValidationError(f"media[{index}].sha256 must be lowercase SHA-256 hex")
        byte_size = item["byte_size"]
        if isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size <= 0:
            raise ValidationError(f"media[{index}].byte_size must be a positive integer")
        mime_type = item["mime_type"]
        if not isinstance(mime_type, str) or not _MIME.fullmatch(mime_type):
            raise ValidationError(f"media[{index}].mime_type must be a MIME type")
        normalized.append({"file_id": file_id, "sha256": sha256, "byte_size": byte_size,
                           "mime_type": mime_type, "alt_text": _alt_text(item["alt_text"])})
    return normalized


def _full_payload_sha256(text: str, media: list[dict[str, Any]]) -> str:
    payload = _json({"text": text, "media": media}, "full payload")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _string_list(value: Any, label: str, allowed: set[str] | None = None) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValidationError(f"{label} must be a list of nonempty strings")
    if allowed is not None and any(item not in allowed for item in value):
        raise ValidationError(f"{label} contains an unsupported value")
    return value


def _validate_policy(policy: Any) -> dict[str, Any]:
    if not isinstance(policy, dict):
        raise ValidationError("account_policy must be an object")
    known = {"forbidden_actor_ids", "allowed_actor_ids", "protected_destinations", "allowed_actor_kind", "allowed_actor_kinds"}
    if any(not isinstance(key, str) for key in policy):
        raise ValidationError("account_policy keys must be strings")
    unknown = set(policy) - known
    if unknown:
        raise ValidationError(f"account_policy has unknown key(s): {', '.join(sorted(unknown))}")
    for key in ("forbidden_actor_ids", "allowed_actor_ids"):
        if key in policy:
            _string_list(policy[key], f"account_policy.{key}")
    if "allowed_actor_kind" in policy and policy["allowed_actor_kind"] not in {"page", "profile"}:
        raise ValidationError("account_policy.allowed_actor_kind must be page or profile")
    if "allowed_actor_kinds" in policy:
        _string_list(policy["allowed_actor_kinds"], "account_policy.allowed_actor_kinds", {"page", "profile"})
    protected = policy.get("protected_destinations", {})
    if not isinstance(protected, (dict, list)):
        raise ValidationError("account_policy.protected_destinations must be an object or list")
    if isinstance(protected, list):
        _string_list(protected, "account_policy.protected_destinations")
    else:
        for destination, rule in protected.items():
            _id(destination, "protected destination ID")
            if isinstance(rule, str):
                if rule not in {"page", "profile"}:
                    raise ValidationError("protected destination kind must be page or profile")
            elif isinstance(rule, list):
                _string_list(rule, "protected destination actor kinds", {"page", "profile"})
            elif isinstance(rule, dict):
                rule_unknown = set(rule) - {"actor_kind", "actor_kinds", "actor_id", "actor_ids"}
                if rule_unknown or ("actor_kind" in rule and "actor_kinds" in rule) or ("actor_id" in rule and "actor_ids" in rule):
                    raise ValidationError("protected destination rule has invalid or ambiguous keys")
                if "actor_kind" in rule and rule["actor_kind"] not in {"page", "profile"}:
                    raise ValidationError("protected destination actor_kind must be page or profile")
                if "actor_kinds" in rule:
                    _string_list(rule["actor_kinds"], "protected destination actor_kinds", {"page", "profile"})
                if "actor_id" in rule:
                    _id(rule["actor_id"], "protected destination actor_id")
                if "actor_ids" in rule:
                    _string_list(rule["actor_ids"], "protected destination actor_ids")
            else:
                raise ValidationError("protected destination rule must be a kind, list, or object")
    return dict(policy)


def _binding(binding: Any, account_policy: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(binding, dict):
        raise ValidationError("binding must be an object")
    checked = dict(binding)
    for field in ("author_route", "actor_id", "destination_id", "action", "context_ref"):
        _id(checked.get(field), f"binding.{field}")
    if checked.get("actor_kind") not in {"page", "profile"}:
        raise ValidationError("binding.actor_kind must be page or profile")
    if "reply_parent_id" in checked and checked["reply_parent_id"] is not None:
        _id(checked["reply_parent_id"], "binding.reply_parent_id")
    if "audience" in checked:
        _id(checked["audience"], "binding.audience")
    policy = _validate_policy(account_policy)
    _json(checked, "binding")
    _json(policy, "account_policy")
    _enforce_policy(checked, policy)
    return checked, policy


def _enforce_policy(binding: dict[str, Any], policy: dict[str, Any]) -> None:
    """Apply a deliberately small protected-destination actor allowlist.

    ``protected_destinations`` may be a mapping from destination ID to a rule
    (``"page"``, a list of kinds, or ``{"actor_kinds": [...], "actor_ids": [...]}``)
    or a list of IDs; list entries default to requiring a Page actor.
    """
    if binding["actor_id"] in policy.get("forbidden_actor_ids", []):
        raise ValidationError("actor is forbidden by immutable account policy")
    allowed_ids = policy.get("allowed_actor_ids")
    if allowed_ids is not None and binding["actor_id"] not in allowed_ids:
        raise ValidationError("actor is not in immutable account-policy allowlist")

    protected = policy.get("protected_destinations", {})
    destination = binding["destination_id"]
    rule: Any = None
    if isinstance(protected, dict):
        rule = protected.get(destination)
    elif destination in protected:
        rule = {
            "actor_kinds": policy.get("allowed_actor_kinds", [policy.get("allowed_actor_kind", "page")]),
        }
    if rule is None:
        return
    if isinstance(rule, str):
        kinds, ids = [rule], None
    elif isinstance(rule, (list, tuple, set)):
        kinds, ids = list(rule), None
    elif isinstance(rule, dict):
        kinds = rule.get("actor_kinds", [rule["actor_kind"]] if "actor_kind" in rule else ["page"])
        ids = rule.get("actor_ids", [rule["actor_id"]] if "actor_id" in rule else None)
    else:
        raise ValidationError("protected destination policy rule is invalid")
    if binding["actor_kind"] not in kinds or (ids is not None and binding["actor_id"] not in ids):
        raise ValidationError("actor is not allowed for protected destination")


class Store:
    """SQLite-backed state machine for explicit human/agent-mediated relay steps."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._schema()

    def close(self) -> None:
        self._db.close()

    def _schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
              case_id TEXT PRIMARY KEY, binding_json TEXT NOT NULL, policy_json TEXT NOT NULL,
              selected_revision TEXT, current_authority_ref TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS revisions (
              case_id TEXT NOT NULL, revision_id TEXT NOT NULL, parent_revision_id TEXT,
              text TEXT NOT NULL, source_ref_json TEXT NOT NULL, content_sha256 TEXT NOT NULL,
              transport_lf_sha256 TEXT NOT NULL, media_json TEXT NOT NULL DEFAULT '[]',
              full_payload_sha256 TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
              PRIMARY KEY (case_id, revision_id),
              FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );
            CREATE TABLE IF NOT EXISTS endorsements (
              id INTEGER PRIMARY KEY, case_id TEXT NOT NULL, revision_id TEXT NOT NULL,
              source_ref_json TEXT NOT NULL, text_sha256 TEXT NOT NULL, full_payload_sha256 TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS authorities (
              authority_ref TEXT PRIMARY KEY, case_id TEXT NOT NULL, revision_id TEXT NOT NULL,
              full_payload_sha256 TEXT, expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
              used_at TEXT, invalidated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS deliveries (
              operation_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, revision_id TEXT NOT NULL,
              authority_ref TEXT NOT NULL, state TEXT NOT NULL, outcome TEXT, evidence_json TEXT,
              content_sha256 TEXT NOT NULL, transport_lf_sha256 TEXT NOT NULL,
              full_payload_sha256 TEXT, started_at TEXT NOT NULL, finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS delivery_guard (
              singleton INTEGER PRIMARY KEY CHECK (singleton = 1), operation_id TEXT NOT NULL,
              state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, event_type TEXT NOT NULL,
              case_id TEXT, revision_id TEXT, operation_id TEXT, detail_json TEXT NOT NULL
            );
            """
        )
        self._migrate_media_payload_schema()

    def _migrate_media_payload_schema(self) -> None:
        """Upgrade v1 rows without claiming their old attestations bound a payload digest."""
        columns = {
            table: {row["name"] for row in self._db.execute(f"PRAGMA table_info({table})")}
            for table in ("revisions", "endorsements", "authorities", "deliveries")
        }
        additions = {
            "revisions": [
                ("media_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("full_payload_sha256", "TEXT NOT NULL DEFAULT ''"),
            ],
            "endorsements": [("full_payload_sha256", "TEXT")],
            "authorities": [("full_payload_sha256", "TEXT")],
            "deliveries": [("full_payload_sha256", "TEXT")],
        }
        for table, needed in additions.items():
            for name, declaration in needed:
                if name not in columns[table]:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        for row in self._db.execute("SELECT case_id,revision_id,text,media_json,full_payload_sha256 FROM revisions").fetchall():
            if row["full_payload_sha256"]:
                continue
            media = json.loads(row["media_json"])
            self._db.execute("UPDATE revisions SET full_payload_sha256=? WHERE case_id=? AND revision_id=?",
                             (_full_payload_sha256(row["text"], media), row["case_id"], row["revision_id"]))
        # Old endorsement/authority/delivery rows intentionally remain NULL:
        # their pre-v2 records never attested a full caption+media payload.
        self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        # This serializes one Store object's one SQLite connection.  It is not
        # an external-account/session lock; cross-process safety is SQLite's job.
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    @staticmethod
    def _event(db: sqlite3.Connection, event_type: str, case_id: str | None = None,
               revision_id: str | None = None, operation_id: str | None = None, **detail: Any) -> None:
        db.execute(
            "INSERT INTO events(created_at,event_type,case_id,revision_id,operation_id,detail_json) VALUES(?,?,?,?,?,?)",
            (_now(), event_type, case_id, revision_id, operation_id, _json(detail, "event detail")),
        )

    @staticmethod
    def _case(db: sqlite3.Connection, case_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            raise NotFoundError("case not found")
        return row

    @staticmethod
    def _revision(db: sqlite3.Connection, case_id: str, revision_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM revisions WHERE case_id=? AND revision_id=?", (case_id, revision_id)).fetchone()
        if row is None:
            raise NotFoundError("revision not found for case")
        return row

    def create_case(self, case_id: str, binding: dict[str, Any], account_policy: dict[str, Any] | None = None) -> None:
        case_id = _id(case_id, "case_id")
        checked, policy = _binding(binding, {} if account_policy is None else account_policy)
        with self._tx() as db:
            if db.execute("SELECT 1 FROM cases WHERE case_id=?", (case_id,)).fetchone():
                raise ConflictError("case_id already exists")
            db.execute("INSERT INTO cases(case_id,binding_json,policy_json,created_at) VALUES(?,?,?,?)",
                       (case_id, _json(checked, "binding"), _json(policy, "account_policy"), _now()))
            self._event(db, "case_created", case_id, binding=checked, account_policy=policy)

    def add_revision(self, case_id: str, revision_id: str, text: str, source_ref: Any,
                     parent_revision_id: str | None = None, media: list[dict[str, Any]] | None = None) -> None:
        media_items = _media(media)
        case_id, revision_id, text = _id(case_id, "case_id"), _id(revision_id, "revision_id"), _text(text, allow_empty=bool(media_items))
        source = _source_ref(source_ref, "source_ref")
        if parent_revision_id is not None:
            parent_revision_id = _id(parent_revision_id, "parent_revision_id")
        content_sha, transport_sha = _hashes(text)
        full_payload_sha = _full_payload_sha256(text, media_items)
        with self._tx() as db:
            self._case(db, case_id)
            existing_count = db.execute("SELECT COUNT(*) FROM revisions WHERE case_id=?", (case_id,)).fetchone()[0]
            if existing_count and parent_revision_id is None:
                raise ValidationError("parent_revision_id is required after the root revision")
            if not existing_count and parent_revision_id is not None:
                raise ValidationError("root revision must not have a parent_revision_id")
            if parent_revision_id is not None:
                self._revision(db, case_id, parent_revision_id)
            if db.execute("SELECT 1 FROM revisions WHERE case_id=? AND revision_id=?", (case_id, revision_id)).fetchone():
                raise ConflictError("revision_id already exists; revisions are immutable")
            db.execute("""INSERT INTO revisions(case_id,revision_id,parent_revision_id,text,source_ref_json,content_sha256,transport_lf_sha256,media_json,full_payload_sha256,created_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?)""",
                       (case_id, revision_id, parent_revision_id, text, source, content_sha, transport_sha,
                        _json(media_items, "media"), full_payload_sha, _now()))
            db.execute("UPDATE authorities SET invalidated_at=? WHERE case_id=? AND used_at IS NULL AND invalidated_at IS NULL",
                       (_now(), case_id))
            db.execute("UPDATE cases SET selected_revision=NULL,current_authority_ref=NULL WHERE case_id=?", (case_id,))
            self._event(db, "revision_added", case_id, revision_id, parent_revision_id=parent_revision_id,
                        content_sha256=content_sha, transport_lf_sha256=transport_sha,
                        full_payload_sha256=full_payload_sha, media_count=len(media_items))

    def endorse(self, case_id: str, revision_id: str, source_ref: Any, exact_text: str,
                exact_full_payload_sha256: str | None = None) -> None:
        case_id, revision_id = _id(case_id, "case_id"), _id(revision_id, "revision_id")
        source = _source_ref(source_ref, "endorsement source_ref")
        with self._tx() as db:
            revision = self._revision(db, case_id, revision_id)
            media = json.loads(revision["media_json"])
            exact_text = _text(exact_text, allow_empty=bool(media))
            if revision["text"] != exact_text:
                raise ValidationError("endorsement exact_text does not match immutable revision text")
            if media and exact_full_payload_sha256 is None:
                raise ValidationError("media endorsement requires exact_full_payload_sha256")
            if exact_full_payload_sha256 is not None and exact_full_payload_sha256 != revision["full_payload_sha256"]:
                raise ValidationError("endorsement exact_full_payload_sha256 does not match immutable full payload")
            db.execute("INSERT INTO endorsements(case_id,revision_id,source_ref_json,text_sha256,full_payload_sha256,created_at) VALUES(?,?,?,?,?,?)",
                       (case_id, revision_id, source, revision["content_sha256"], revision["full_payload_sha256"], _now()))
            self._event(db, "revision_endorsed", case_id, revision_id, source_ref=json.loads(source))

    def stage(self, case_id: str, revision_id: str) -> None:
        case_id, revision_id = _id(case_id, "case_id"), _id(revision_id, "revision_id")
        with self._tx() as db:
            self._case(db, case_id)
            self._revision(db, case_id, revision_id)
            if db.execute("SELECT 1 FROM revisions WHERE case_id=? AND parent_revision_id=?", (case_id, revision_id)).fetchone():
                raise StateError("stale revision has newer descendant candidates; stage an explicit current candidate")
            if not db.execute(
                "SELECT 1 FROM endorsements WHERE case_id=? AND revision_id=? AND full_payload_sha256=(SELECT full_payload_sha256 FROM revisions WHERE case_id=? AND revision_id=?)",
                (case_id, revision_id, case_id, revision_id),
            ).fetchone():
                raise StateError("revision must have a local endorsement before staging")
            active = db.execute("SELECT 1 FROM deliveries WHERE case_id=? AND state IN ('in_flight','uncertain')", (case_id,)).fetchone()
            if active:
                raise StateError("case has a pending delivery outcome")
            db.execute("UPDATE authorities SET invalidated_at=? WHERE case_id=? AND used_at IS NULL AND invalidated_at IS NULL",
                       (_now(), case_id))
            db.execute("UPDATE cases SET selected_revision=?,current_authority_ref=NULL WHERE case_id=?", (revision_id, case_id))
            self._event(db, "revision_staged", case_id, revision_id)

    def authorize(self, case_id: str, revision_id: str, authority_ref: str, expires_at: str) -> None:
        case_id, revision_id, authority_ref = _id(case_id, "case_id"), _id(revision_id, "revision_id"), _id(authority_ref, "authority_ref")
        expires_at = _utc_timestamp(expires_at)
        if not _future(expires_at):
            raise ValidationError("authority is already expired")
        with self._tx() as db:
            case = self._case(db, case_id)
            revision = self._revision(db, case_id, revision_id)
            if case["selected_revision"] != revision_id:
                raise StateError("only the explicitly staged revision can be authorized")
            if not db.execute(
                "SELECT 1 FROM endorsements WHERE case_id=? AND revision_id=? AND full_payload_sha256=?",
                (case_id, revision_id, revision["full_payload_sha256"]),
            ).fetchone():
                raise StateError("authorization requires an endorsement bound to the full payload")
            if db.execute("SELECT 1 FROM authorities WHERE authority_ref=?", (authority_ref,)).fetchone():
                raise ConflictError("authority_ref was already recorded and cannot be replayed")
            current = case["current_authority_ref"]
            if current:
                current_row = db.execute("SELECT * FROM authorities WHERE authority_ref=?", (current,)).fetchone()
                if current_row and current_row["used_at"] is None and current_row["invalidated_at"] is None and _future(current_row["expires_at"]):
                    raise StateError("an unconsumed authority already exists for this staged revision")
            db.execute("INSERT INTO authorities(authority_ref,case_id,revision_id,full_payload_sha256,expires_at,created_at) VALUES(?,?,?,?,?,?)",
                       (authority_ref, case_id, revision_id, revision["full_payload_sha256"],
                        expires_at, _now()))
            db.execute("UPDATE cases SET current_authority_ref=? WHERE case_id=?", (authority_ref, case_id))
            self._event(db, "authority_recorded", case_id, revision_id, authority_ref=authority_ref, expires_at=expires_at)

    def begin_delivery(self, case_id: str, revision_id: str, operation_id: str) -> dict[str, Any]:
        case_id, revision_id, operation_id = _id(case_id, "case_id"), _id(revision_id, "revision_id"), _id(operation_id, "operation_id")
        with self._tx() as db:
            prior = db.execute("SELECT * FROM deliveries WHERE operation_id=?", (operation_id,)).fetchone()
            if prior:
                if prior["case_id"] != case_id or prior["revision_id"] != revision_id:
                    raise ConflictError("operation_id belongs to a different case or revision")
                return self._delivery_dict(prior, newly_claimed=False)
            case = self._case(db, case_id)
            revision = self._revision(db, case_id, revision_id)
            if case["selected_revision"] != revision_id or not case["current_authority_ref"]:
                raise StateError("revision is not currently staged and authorized")
            authority = db.execute("SELECT * FROM authorities WHERE authority_ref=?", (case["current_authority_ref"],)).fetchone()
            if authority is None or authority["case_id"] != case_id or authority["revision_id"] != revision_id:
                raise StateError("current authority does not bind this case and revision")
            if authority["full_payload_sha256"] != revision["full_payload_sha256"]:
                raise StateError("authority does not bind the current full payload")
            if authority["used_at"] is not None or authority["invalidated_at"] is not None:
                raise StateError("authority was already consumed or invalidated")
            if not _future(authority["expires_at"]):
                raise StateError("authority is expired")
            guard = db.execute("SELECT * FROM delivery_guard WHERE singleton=1").fetchone()
            if guard:
                raise StateError(f"account-wide delivery is blocked by {guard['state']} operation {guard['operation_id']}")
            started = _now()
            db.execute("""INSERT INTO deliveries(operation_id,case_id,revision_id,authority_ref,state,content_sha256,transport_lf_sha256,full_payload_sha256,started_at)
                          VALUES(?,?,?,?,?,?,?,?,?)""",
                       (operation_id, case_id, revision_id, authority["authority_ref"], "in_flight",
                        revision["content_sha256"], revision["transport_lf_sha256"], revision["full_payload_sha256"], started))
            db.execute("INSERT INTO delivery_guard(singleton,operation_id,state) VALUES(1,?,?)", (operation_id, "in_flight"))
            db.execute("UPDATE authorities SET used_at=? WHERE authority_ref=?", (started, authority["authority_ref"]))
            db.execute("UPDATE cases SET current_authority_ref=NULL WHERE case_id=?", (case_id,))
            self._event(db, "delivery_begun", case_id, revision_id, operation_id, authority_ref=authority["authority_ref"])
            return self._delivery_dict(
                db.execute("SELECT * FROM deliveries WHERE operation_id=?", (operation_id,)).fetchone(),
                newly_claimed=True,
            )

    def revoke_authority(self, case_id: str, reason: Any) -> dict[str, Any]:
        """Invalidate unused local authority; active attempts remain advisory-only."""
        case_id = _id(case_id, "case_id")
        reason_json = _source_ref(reason, "revocation reason/source_ref")
        with self._tx() as db:
            self._case(db, case_id)
            now = _now()
            revoked = db.execute(
                "UPDATE authorities SET invalidated_at=? WHERE case_id=? AND used_at IS NULL AND invalidated_at IS NULL",
                (now, case_id),
            ).rowcount
            db.execute("UPDATE cases SET current_authority_ref=NULL WHERE case_id=?", (case_id,))
            active = [row["operation_id"] for row in db.execute(
                "SELECT operation_id FROM deliveries WHERE case_id=? AND state IN ('in_flight','uncertain')", (case_id,)
            )]
            self._event(db, "authority_revoked", case_id, revoked=revoked, reason_or_source_ref=json.loads(reason_json),
                        advisory_operations=active)
            return {"revoked": revoked, "advisory_operations": active}

    def record_outcome(self, operation_id: str, outcome: str, evidence: Any) -> dict[str, Any]:
        operation_id = _id(operation_id, "operation_id")
        if outcome not in OUTCOMES:
            raise ValidationError("outcome must be verified, uncertain, or failed_before_submit")
        with self._tx() as db:
            delivery = db.execute("SELECT * FROM deliveries WHERE operation_id=?", (operation_id,)).fetchone()
            if delivery is None:
                raise NotFoundError("operation not found")
            if delivery["state"] != "in_flight":
                raise StateError("outcome is already final or uncertain")
            evidence_value, evidence_json = self._outcome_evidence(db, delivery, outcome, evidence)
            finished = _now()
            db.execute("UPDATE deliveries SET state=?,outcome=?,evidence_json=?,finished_at=? WHERE operation_id=?",
                       (outcome, outcome, evidence_json, finished, operation_id))
            if outcome == "uncertain":
                db.execute("UPDATE delivery_guard SET state='uncertain' WHERE singleton=1 AND operation_id=?", (operation_id,))
            else:
                db.execute("DELETE FROM delivery_guard WHERE singleton=1 AND operation_id=?", (operation_id,))
            self._event(db, "delivery_outcome_recorded", delivery["case_id"], delivery["revision_id"], operation_id,
                        outcome=outcome, evidence=evidence_value)
            return self._delivery_dict(db.execute("SELECT * FROM deliveries WHERE operation_id=?", (operation_id,)).fetchone())

    def reconcile(self, operation_id: str, outcome: str, evidence: Any, reconciliation_ref: Any) -> dict[str, Any]:
        """Close a recorded uncertainty with new local observation; never retry delivery."""
        operation_id = _id(operation_id, "operation_id")
        if outcome not in {"verified", "failed_before_submit"}:
            raise ValidationError("reconciliation outcome must be verified or failed_before_submit")
        ref_json = _source_ref(reconciliation_ref, "reconciliation_ref")
        with self._tx() as db:
            delivery = db.execute("SELECT * FROM deliveries WHERE operation_id=?", (operation_id,)).fetchone()
            if delivery is None:
                raise NotFoundError("operation not found")
            if delivery["state"] != "uncertain":
                raise StateError("only an uncertain operation can be reconciled")
            evidence_value, evidence_json = self._outcome_evidence(db, delivery, outcome, evidence)
            previous_evidence = json.loads(delivery["evidence_json"]) if delivery["evidence_json"] else None
            finished = _now()
            db.execute("UPDATE deliveries SET state=?,outcome=?,evidence_json=?,finished_at=? WHERE operation_id=?",
                       (outcome, outcome, evidence_json, finished, operation_id))
            db.execute("DELETE FROM delivery_guard WHERE singleton=1 AND operation_id=?", (operation_id,))
            self._event(db, "delivery_reconciled", delivery["case_id"], delivery["revision_id"], operation_id,
                        previous_state="uncertain", previous_evidence=previous_evidence,
                        outcome=outcome, evidence=evidence_value, reconciliation_ref=json.loads(ref_json))
            return self._delivery_dict(db.execute("SELECT * FROM deliveries WHERE operation_id=?", (operation_id,)).fetchone())

    def _outcome_evidence(self, db: sqlite3.Connection, delivery: sqlite3.Row, outcome: str,
                          evidence: Any) -> tuple[dict[str, Any], str]:
        """Validate local observation evidence; this deliberately is not source authentication."""
        evidence_json = _source_ref(evidence, "outcome evidence")
        evidence_value = json.loads(evidence_json)
        if not isinstance(evidence_value, dict):
            raise ValidationError("outcome evidence must be an object")
        has_observation = isinstance(evidence_value.get("observation"), str) and bool(evidence_value["observation"].strip())
        has_source = "source_ref" in evidence_value
        if has_source:
            _source_ref(evidence_value["source_ref"], "outcome evidence.source_ref")
        if not (has_observation or has_source):
            raise ValidationError("outcome evidence requires a nonempty observation or source_ref")
        if outcome == "failed_before_submit" and evidence_value.get("positive_no_submit") is not True:
            raise ValidationError("failed_before_submit requires evidence.positive_no_submit == true")
        if outcome == "verified":
            case = self._case(db, delivery["case_id"])
            binding = json.loads(case["binding_json"])
            for field, expected in (
                ("actor_id", binding["actor_id"]),
                ("destination_id", binding["destination_id"]),
                ("content_sha256", delivery["content_sha256"]),
            ):
                if evidence_value.get(field) != expected:
                    raise ValidationError(f"verified evidence.{field} does not match bound delivery")
            for field in ("reply_parent_id", "audience"):
                if field in binding and (field not in evidence_value or evidence_value[field] != binding[field]):
                    raise ValidationError(f"verified evidence.{field} does not match bound delivery")
            revision = self._revision(db, delivery["case_id"], delivery["revision_id"])
            media = json.loads(revision["media_json"])
            if media and evidence_value.get("full_payload_sha256") != delivery["full_payload_sha256"]:
                raise ValidationError("verified evidence.full_payload_sha256 does not match media payload")
            if "full_payload_sha256" in evidence_value and evidence_value["full_payload_sha256"] != delivery["full_payload_sha256"]:
                raise ValidationError("verified evidence.full_payload_sha256 does not match bound delivery")
            _zoned_timestamp(evidence_value.get("observed_at"), "verified evidence.observed_at")
        return evidence_value, evidence_json

    def recover(self) -> list[str]:
        """Conservatively turn durable in-flight attempts into uncertainty; never retry."""
        with self._tx() as db:
            rows = db.execute("SELECT * FROM deliveries WHERE state='in_flight'").fetchall()
            ids = [row["operation_id"] for row in rows]
            if ids:
                recovered_at = _now()
                for row in rows:
                    evidence = {"recovery": "process_restart", "recovered_at": recovered_at}
                    db.execute("UPDATE deliveries SET state='uncertain',outcome='uncertain',evidence_json=?,finished_at=? WHERE operation_id=?",
                               (_json(evidence, "recovery evidence"), recovered_at, row["operation_id"]))
                    self._event(db, "delivery_recovered_as_uncertain", row["case_id"], row["revision_id"], row["operation_id"], **evidence)
                db.execute("UPDATE delivery_guard SET state='uncertain' WHERE singleton=1")
            return ids

    @staticmethod
    def _delivery_dict(row: sqlite3.Row, newly_claimed: bool = False) -> dict[str, Any]:
        result = {
            "operation_id": row["operation_id"], "case_id": row["case_id"], "revision_id": row["revision_id"],
            "authority_ref": row["authority_ref"], "state": row["state"], "outcome": row["outcome"],
            "content_sha256": row["content_sha256"], "transport_lf_sha256": row["transport_lf_sha256"],
            "full_payload_sha256": row["full_payload_sha256"],
            "started_at": row["started_at"], "finished_at": row["finished_at"],
            "evidence": json.loads(row["evidence_json"]) if row["evidence_json"] else None,
        }
        result["newly_claimed"] = newly_claimed
        return result

    def inspect(self, case_id: str) -> dict[str, Any]:
        case_id = _id(case_id, "case_id")
        with self._tx() as db:
            case = self._case(db, case_id)
            revisions = db.execute("SELECT * FROM revisions WHERE case_id=? ORDER BY created_at,revision_id", (case_id,)).fetchall()
            authorities = db.execute("SELECT * FROM authorities WHERE case_id=? ORDER BY created_at", (case_id,)).fetchall()
            deliveries = db.execute("SELECT * FROM deliveries WHERE case_id=? ORDER BY started_at", (case_id,)).fetchall()
            endorsements = db.execute("SELECT case_id,revision_id,source_ref_json,text_sha256,full_payload_sha256,created_at FROM endorsements WHERE case_id=? ORDER BY id", (case_id,)).fetchall()
            return {
                "notice": LOCAL_NOTICE,
                "case_id": case_id,
                "binding": json.loads(case["binding_json"]), "account_policy": json.loads(case["policy_json"]),
                "selected_revision": case["selected_revision"], "current_authority_ref": case["current_authority_ref"],
                "revisions": [{"revision_id": r["revision_id"], "parent_revision_id": r["parent_revision_id"], "text": r["text"],
                               "source_ref": json.loads(r["source_ref_json"]), "content_sha256": r["content_sha256"],
                               "transport_lf_sha256": r["transport_lf_sha256"], "media": json.loads(r["media_json"]),
                               "full_payload_sha256": r["full_payload_sha256"]} for r in revisions],
                "endorsements": [{"revision_id": r["revision_id"], "source_ref": json.loads(r["source_ref_json"]),
                                  "text_sha256": r["text_sha256"], "full_payload_sha256": r["full_payload_sha256"]} for r in endorsements],
                "authorities": [{key: r[key] for key in ("authority_ref", "revision_id", "full_payload_sha256", "expires_at", "used_at", "invalidated_at")} for r in authorities],
                "deliveries": [self._delivery_dict(r) for r in deliveries],
            }
