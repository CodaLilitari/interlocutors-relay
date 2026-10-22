"""Create a clean, local release export from a reviewed explicit allowlist.

The allowlist is a UTF-8 JSON file named ``release-files.json`` at the source
root, with this exact schema::

    {"version": 1, "files": ["README.md", "relay_core.py"]}

Every entry is a relative POSIX path.  Files not named there are not inspected
or copied.  The exporter accepts only small text/source/document/license
formats, never databases, media, environment files, credentials, research, or
private runtime material.  It writes a newly created destination directory
plus ``EXPORT-MANIFEST.json`` and may create a deterministic ZIP *outside*
that directory.

The textual safety scan is defense in depth, not a privacy proof.  The
allowlist and human review remain the release boundary.  This module has no
network, Git, upload, or external-account behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import struct
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


RELEASE_SCHEMA_VERSION = 1
EXPORT_MANIFEST_NAME = "EXPORT-MANIFEST.json"
MAX_RELEASE_FILE_BYTES = 5 * 1024 * 1024
MAX_RELEASE_TOTAL_BYTES = 30 * 1024 * 1024

_TEXT_SUFFIXES = frozenset({
    ".bat", ".cfg", ".css", ".editorconfig", ".gitignore", ".html", ".ini",
    ".js", ".json", ".lic", ".license", ".md", ".ps1", ".py", ".rst",
    ".sh", ".toml", ".ts", ".txt", ".yaml", ".yml",
})
_TEXT_DOTFILENAMES = frozenset({".editorconfig", ".gitignore"})
_LICENSE_FILENAMES = frozenset({"LICENSE", "COPYING", "NOTICE", "AUTHORS", "CONTRIBUTORS"})
_FORBIDDEN_SUFFIXES = frozenset({
    ".bmp", ".db", ".gif", ".jpeg", ".jpg", ".mp3", ".mp4", ".png", ".sqlite",
    ".sqlite3", ".wal", ".webm", ".webp", ".zip",
})
_FORBIDDEN_PATH_PARTS = frozenset({
    ".env", ".git", ".venv", "__pycache__", "credentials", "media", "private", "research",
    "secrets", "state", "venv",
})
_WILDCARD_CHARACTERS = frozenset("*?[]")
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

_HOME_PATH = Path.home()
_HOME_TEXT_MARKERS = tuple(
    marker for marker in {
        str(_HOME_PATH),
        os.environ.get("USERPROFILE", ""),
        os.environ.get("HOME", ""),
    } if marker
)
_SUSPICIOUS_TEXT_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("OpenAI-style secret", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("authorization bearer value", re.compile(
        r"(?im)(?:^\s*authorization\s*:\s*bearer\s+\S+|[\"']authorization[\"']\s*:\s*[\"']bearer\s+[A-Za-z0-9._-]{12,})"
    )),
    ("API key value", re.compile(
        r"(?im)(?:^\s*|[\"'])(?:x[-_]?api[-_]?key|api[-_]?key)[\"']?\s*[:=]\s*['\"]?[A-Za-z0-9_-]{16,}"
    )),
    ("credential assignment", re.compile(
        r"(?im)^\s*(?:password|access[_-]?token|refresh[_-]?token|client[_-]?secret|session(?:id|_id)?)\s*[:=]\s*"
        # Quoted values retain the literal-prefix check. For bare values, do
        # not mistake a full call/subscript receiver for a credential prefix
        # (commands.add_parser(...) or self.sessions[...]). The first negative
        # lookahead also prevents backtracking to a partial receiver match.
        r"(?:['\"][A-Za-z0-9._-]{12,}|[A-Za-z0-9._-]{12,}(?![A-Za-z0-9._-]|[ \t]*[([]))"
    )),
    ("ChatGPT conversation URL", re.compile(r"https?://(?:chatgpt\.com|chat\.openai\.com)/c/[A-Za-z0-9_-]+", re.I)),
    ("Facebook URL", re.compile(r"https?://(?:www\.)?(?:facebook|fb)\.com/[A-Za-z0-9._/?=&%-]+", re.I)),
    ("private account or conversation identifier", re.compile(
        r"(?im)(?:^\s*|[\"'])(?:account|profile|page|actor|destination|thread|conversation)[_-]?id[\"']?\s*[:=]\s*['\"]?(?:\d{5,}|[A-Za-z0-9_-]{16,})"
    )),
)


class ReleaseExportError(ValueError):
    """The requested release source or destination is not safe to export."""


def export_release(
    source_root: str | Path,
    destination: str | Path,
    *,
    allowlist_name: str = "release-files.json",
    archive: str | Path | None = None,
) -> dict[str, Any]:
    """Copy exactly reviewed allowlisted text files into a new clean export.

    ``source_root`` must contain the strict JSON allowlist.  ``destination``
    must not exist and must be outside the source package.  If ``archive`` is
    supplied, it too must not exist and must lie outside the export directory.
    Returns locally computed file records and paths; it does not publish them.
    """

    root = _existing_directory(source_root, "source root")
    allowlist_path = _resolve_allowlist(root, allowlist_name)
    relative_paths = load_release_allowlist(allowlist_path)
    destination_path = _prepare_new_destination(root, destination)

    prepared: list[tuple[str, bytes, dict[str, Any]]] = []
    total_bytes = 0
    for relative_path in relative_paths:
        source_path = _source_path(root, relative_path)
        data = _read_checked_text_file(source_path)
        total_bytes += len(data)
        if total_bytes > MAX_RELEASE_TOTAL_BYTES:
            raise ReleaseExportError("combined release content exceeds the total byte bound")
        _scan_text(relative_path, data)
        prepared.append((relative_path, data, _file_record(relative_path, data)))

    archive_path = _prepare_archive(destination_path, archive) if archive is not None else None
    try:
        destination_path.mkdir()
        for relative_path, data, _ in prepared:
            output = _destination_path(destination_path, relative_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            _write_new_bytes(output, data)
        manifest = {
            "version": RELEASE_SCHEMA_VERSION,
            "files": [record for _, _, record in prepared],
            "scan_notice": "defense-in-depth text scan only; reviewed allowlist is the release boundary.",
        }
        manifest_bytes = _canonical_json(manifest)
        _write_new_bytes(destination_path / EXPORT_MANIFEST_NAME, manifest_bytes)
        if archive_path is not None:
            _write_deterministic_zip(archive_path, prepared, manifest_bytes)
    except Exception:
        # The output may be useful forensic evidence of an interrupted export;
        # do not delete it here and never overwrite it on a retry.
        raise

    result: dict[str, Any] = {
        "source_root": str(root),
        "destination": str(destination_path),
        "manifest": manifest,
        "archive": str(archive_path) if archive_path is not None else None,
        "local_only": True,
    }
    return result


def load_release_allowlist(path: str | Path) -> list[str]:
    """Read and validate the exact v1 allowlist schema without exporting it."""

    allowlist = Path(path)
    data = _read_checked_text_file(allowlist)
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseExportError("release allowlist must be UTF-8 JSON") from exc
    if not isinstance(parsed, Mapping) or set(parsed) != {"version", "files"}:
        raise ReleaseExportError("release allowlist must contain only version and files")
    if parsed["version"] != RELEASE_SCHEMA_VERSION:
        raise ReleaseExportError("release allowlist version is unsupported")
    files = parsed["files"]
    if not isinstance(files, list) or not files:
        raise ReleaseExportError("release allowlist files must be a nonempty ordered list")
    if len(files) > 512:
        raise ReleaseExportError("release allowlist has too many entries")
    checked: list[str] = []
    collision_keys: set[str] = set()
    for entry in files:
        checked_path = _validate_relative_posix_path(entry)
        collision_key = checked_path.casefold()
        if collision_key in collision_keys:
            raise ReleaseExportError("release allowlist contains duplicate or case-colliding paths")
        collision_keys.add(collision_key)
        checked.append(checked_path)
    return checked


def _existing_directory(value: str | Path, label: str) -> Path:
    path = Path(value)
    try:
        file_stat = os.lstat(path)
    except OSError as exc:
        raise ReleaseExportError(f"{label} is unavailable") from exc
    _reject_link_or_reparse(path, file_stat, label)
    if not stat.S_ISDIR(file_stat.st_mode):
        raise ReleaseExportError(f"{label} must be a directory")
    return path.resolve(strict=True)


def _resolve_allowlist(root: Path, allowlist_name: str) -> Path:
    if not isinstance(allowlist_name, str) or allowlist_name != "release-files.json":
        raise ReleaseExportError("allowlist name must be release-files.json")
    path = root / allowlist_name
    _source_path(root, allowlist_name)
    return path


def _validate_relative_posix_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ReleaseExportError("release allowlist paths must be nonempty strings")
    if "\\" in value or any(character in value for character in _WILDCARD_CHARACTERS):
        raise ReleaseExportError("release allowlist paths must be exact relative POSIX paths")
    if value.startswith("/") or value.startswith("~") or re.match(r"^[A-Za-z]:", value):
        raise ReleaseExportError("release allowlist paths must be relative")
    pieces = value.split("/")
    if any(piece in {"", ".", ".."} for piece in pieces):
        raise ReleaseExportError("release allowlist paths must not traverse or normalize directories")
    path = PurePosixPath(value)
    if path.is_absolute() or path.name in {"", EXPORT_MANIFEST_NAME}:
        raise ReleaseExportError("release allowlist path is invalid")
    lower_parts = {piece.casefold() for piece in path.parts}
    if lower_parts & _FORBIDDEN_PATH_PARTS:
        raise ReleaseExportError("release allowlist includes a prohibited private/runtime directory")
    filename = path.name
    suffix = Path(filename).suffix.lower()
    if suffix in _FORBIDDEN_SUFFIXES:
        raise ReleaseExportError("release allowlist includes a prohibited binary or private format")
    if filename not in _LICENSE_FILENAMES and filename not in _TEXT_DOTFILENAMES and suffix not in _TEXT_SUFFIXES:
        raise ReleaseExportError("release allowlist includes a format outside the text/source/document/license allowlist")
    return value


def _source_path(root: Path, relative_path: str) -> Path:
    # ``relative_path`` is already strict POSIX.  This second check protects
    # direct internal callers and confirms that the path cannot escape root.
    checked = _validate_relative_posix_path(relative_path)
    candidate = root.joinpath(*PurePosixPath(checked).parts)
    try:
        resolved_parent = candidate.parent.resolve(strict=True)
    except OSError as exc:
        raise ReleaseExportError("listed file parent is unavailable") from exc
    if not _is_within(resolved_parent, root):
        raise ReleaseExportError("listed file parent escapes the source root")
    return candidate


def _read_checked_text_file(path: Path) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise ReleaseExportError(f"listed file is missing or unreadable: {path.name}") from exc
    _reject_link_or_reparse(path, before, "listed file")
    if not stat.S_ISREG(before.st_mode):
        raise ReleaseExportError("listed path must be a regular file")
    if before.st_size > MAX_RELEASE_FILE_BYTES:
        raise ReleaseExportError("listed file exceeds the per-file byte bound")
    content = bytearray()
    try:
        with path.open("rb") as source:
            opened = os.fstat(source.fileno())
            if not stat.S_ISREG(opened.st_mode) or not _same_file(before, opened):
                raise ReleaseExportError("listed file changed while it was opened")
            while True:
                chunk = source.read(64 * 1024)
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > MAX_RELEASE_FILE_BYTES:
                    raise ReleaseExportError("listed file exceeds the per-file byte bound")
    except ReleaseExportError:
        raise
    except OSError as exc:
        raise ReleaseExportError(f"cannot read listed file: {path.name}") from exc
    try:
        after = os.lstat(path)
    except OSError as exc:
        raise ReleaseExportError("listed file disappeared during read") from exc
    _reject_link_or_reparse(path, after, "listed file")
    if not _same_file(before, after) or before.st_size != len(content) or after.st_size != len(content):
        raise ReleaseExportError("listed file changed while it was read")
    try:
        bytes(content).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseExportError("listed file is not UTF-8 text") from exc
    return bytes(content)


def _reject_link_or_reparse(path: Path, file_stat: os.stat_result, label: str) -> None:
    if stat.S_ISLNK(file_stat.st_mode):
        raise ReleaseExportError(f"{label} must not be a symbolic link")
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if reparse_flag and getattr(file_stat, "st_file_attributes", 0) & reparse_flag:
        raise ReleaseExportError(f"{label} must not be a Windows reparse point")


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _file_record(relative_path: str, data: bytes) -> dict[str, Any]:
    return {
        "path": relative_path,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _scan_text(relative_path: str, data: bytes) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:  # guarded earlier; retain explicit local boundary.
        raise ReleaseExportError("listed file is not UTF-8 text") from exc
    normalized_text = text.replace("/", "\\").casefold()
    for home_marker in _HOME_TEXT_MARKERS:
        normalized_marker = home_marker.replace("/", "\\").casefold()
        if normalized_marker and normalized_marker in normalized_text:
            raise ReleaseExportError(f"defense-in-depth scan found a local home path in {relative_path}")
    for label, pattern in _SUSPICIOUS_TEXT_PATTERNS:
        if pattern.search(text):
            raise ReleaseExportError(f"defense-in-depth scan found {label} in {relative_path}")


def _prepare_new_destination(root: Path, destination: str | Path) -> Path:
    output = Path(destination)
    if not output.is_absolute():
        raise ReleaseExportError("destination must be an absolute new directory path")
    if os.path.lexists(output):
        raise ReleaseExportError("destination already exists; exports never overwrite")
    parent = _existing_directory(output.parent, "destination parent")
    candidate = parent / output.name
    if _is_within(candidate, root):
        raise ReleaseExportError("destination must be outside the source package")
    return candidate


def _prepare_archive(destination: Path, archive: str | Path) -> Path:
    path = Path(archive)
    if not path.is_absolute() or path.suffix.lower() != ".zip":
        raise ReleaseExportError("archive must be an absolute new .zip path")
    if os.path.lexists(path):
        raise ReleaseExportError("archive already exists; exports never overwrite")
    parent = _existing_directory(path.parent, "archive parent")
    candidate = parent / path.name
    if _is_within(candidate, destination):
        raise ReleaseExportError("archive must be outside the export directory")
    return candidate


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        # The new export directory itself does not exist yet while validating
        # an optional archive path, so neither side may require existence.
        candidate.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def _destination_path(destination: Path, relative_path: str) -> Path:
    path = destination.joinpath(*PurePosixPath(relative_path).parts)
    if not _is_within(path, destination):
        raise ReleaseExportError("export path escapes destination")
    return path


def _write_new_bytes(path: Path, data: bytes) -> None:
    try:
        with path.open("xb") as target:
            target.write(data)
    except FileExistsError as exc:
        raise ReleaseExportError("export output already exists; refusing overwrite") from exc
    except OSError as exc:
        raise ReleaseExportError(f"cannot write export output: {path.name}") from exc


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_deterministic_zip(archive: Path, prepared: Iterable[tuple[str, bytes, Mapping[str, Any]]], manifest_bytes: bytes) -> None:
    try:
        with zipfile.ZipFile(archive, mode="x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for relative_path, data, _ in prepared:
                _zip_write(bundle, relative_path, data)
            _zip_write(bundle, EXPORT_MANIFEST_NAME, manifest_bytes)
    except FileExistsError as exc:
        raise ReleaseExportError("archive already exists; refusing overwrite") from exc
    except OSError as exc:
        raise ReleaseExportError("cannot write deterministic archive") from exc


def _zip_write(bundle: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(filename=name, date_time=_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    bundle.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=str(Path(__file__).resolve().parent),
                        help="absolute package root containing release-files.json")
    parser.add_argument("--destination", required=True, help="absolute new export directory outside source root")
    parser.add_argument("--archive", help="optional absolute new ZIP path outside export directory")
    arguments = parser.parse_args(argv)
    try:
        result = export_release(arguments.source_root, arguments.destination, archive=arguments.archive)
    except ReleaseExportError as exc:
        print(json.dumps({"ok": False, "local_only": True, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "local_only": True, "result": result}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
