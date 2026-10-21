"""Bounded, local-only inspection of explicitly named image files.

This module does not copy, upload, render, or otherwise share media.  It
accepts an ordered list of exact absolute paths and returns two deliberately
separate records:

* ``private_manifest`` retains the path, a path binding, local file identity,
  and header-derived dimensions for a later local continuity check.
* ``public_media`` is the only list suitable for relay-core metadata.  Every
  item has *exactly* ``file_id``, ``sha256``, ``byte_size``, ``mime_type``, and
  ``alt_text``.  It contains no local path or dimensions.

Input format::

    [{"file_id": "image-1", "path": r"C:\\exact\\image.png",
      "alt_text": "A concise description."}]

``inspect_media`` preserves that input order and accepts at most sixteen
items.  ``verify_media_manifest`` reopens the exact previously recorded files
and raises ``MediaManifestError`` if their path binding, file identity,
content hash, size, type, header dimensions, ID, or alt-text binding differs.
It does not make a captured record tamper-proof: callers must retain the
private manifest as a trusted local record.
"""

from __future__ import annotations

import hashlib
import os
import stat
import struct
from pathlib import Path
from typing import Any, Mapping, Sequence


MAX_ASSETS = 16
MAX_ASSET_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = MAX_ASSETS * MAX_ASSET_BYTES
MAX_ALT_TEXT_CHARS = 1_000
MANIFEST_VERSION = 1
_HASH_CHUNK_BYTES = 64 * 1024
_WILDCARD_CHARACTERS = frozenset("*?[]")
_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


class MediaManifestError(ValueError):
    """The supplied local media is unsafe, malformed, or no longer identical."""


def inspect_media(
    assets: Sequence[Mapping[str, Any]],
    *,
    max_asset_bytes: int = MAX_ASSET_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> dict[str, Any]:
    """Inspect explicit image paths and return private and public metadata.

    ``assets`` must be an ordered list/tuple of objects with exactly
    ``file_id``, ``path``, and ``alt_text``.  The supplied byte limits may
    narrow the built-in limits for tests or a stricter caller; they cannot
    widen them.
    """

    checked_assets = _validate_input_assets(assets)
    per_asset_limit = _bounded_limit(max_asset_bytes, MAX_ASSET_BYTES, "max_asset_bytes")
    total_limit = _bounded_limit(max_total_bytes, MAX_TOTAL_BYTES, "max_total_bytes")
    private_assets: list[dict[str, Any]] = []
    public_media: list[dict[str, Any]] = []
    total_bytes = 0

    for index, asset in enumerate(checked_assets):
        private, public = _inspect_one(asset, per_asset_limit)
        total_bytes += public["byte_size"]
        if total_bytes > total_limit:
            raise MediaManifestError("combined media size exceeds the configured bound")
        private_assets.append(private)
        public_media.append(public)

    return {
        "private_manifest": {"version": MANIFEST_VERSION, "assets": private_assets},
        "public_media": public_media,
    }


def verify_media_manifest(captured: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Reinspect a trusted prior ``inspect_media`` result without copying media.

    On success, returns a fresh public metadata list compatible with relay
    core.  It rejects changed contents and ordinary one-field edits to the
    captured record.  A caller that deliberately rewrites every correlated
    private field must be treated as having replaced the trusted manifest.
    """

    if not isinstance(captured, Mapping) or set(captured) != {"private_manifest", "public_media"}:
        raise MediaManifestError("captured manifest must contain only private_manifest and public_media")
    private_manifest = captured["private_manifest"]
    public_media = captured["public_media"]
    _validate_captured_shape(private_manifest, public_media)

    source_assets = [
        {
            "file_id": entry["public"]["file_id"],
            "path": entry["path"],
            "alt_text": entry["public"]["alt_text"],
        }
        for entry in private_manifest["assets"]
    ]
    observed = inspect_media(source_assets)
    if observed["private_manifest"] != private_manifest or observed["public_media"] != public_media:
        raise MediaManifestError("captured media manifest no longer matches the exact local files")
    return observed["public_media"]


def _bounded_limit(value: Any, ceiling: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > ceiling:
        raise MediaManifestError(f"{label} must be a positive integer no larger than {ceiling}")
    return value


def _validate_input_assets(assets: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(assets, (list, tuple)):
        raise MediaManifestError("assets must be an ordered list or tuple")
    if len(assets) > MAX_ASSETS:
        raise MediaManifestError(f"at most {MAX_ASSETS} assets are allowed")

    checked: list[dict[str, str]] = []
    file_ids: set[str] = set()
    paths: set[str] = set()
    for item in assets:
        if not isinstance(item, Mapping) or set(item) != {"file_id", "path", "alt_text"}:
            raise MediaManifestError("each asset must contain only file_id, path, and alt_text")
        file_id = _nonempty_text(item["file_id"], "file_id", 256)
        path = _nonempty_text(item["path"], "path", 32_767)
        alt_text = _nonempty_text(item["alt_text"], "alt_text", MAX_ALT_TEXT_CHARS)
        if file_id in file_ids:
            raise MediaManifestError("duplicate file_id is not allowed")
        # Windows paths are ordinarily case-insensitive; normalizing here also
        # rejects spelling variants such as ``a\\..\\b.png`` as duplicate
        # explicit paths.  This is not used for public metadata.
        path_key = os.path.normcase(os.path.normpath(path))
        if path_key in paths:
            raise MediaManifestError("duplicate path is not allowed")
        file_ids.add(file_id)
        paths.add(path_key)
        checked.append({"file_id": file_id, "path": path, "alt_text": alt_text})
    return checked


def _nonempty_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise MediaManifestError(f"{label} must be a nonempty string within its size bound")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise MediaManifestError(f"{label} must not contain a lone Unicode surrogate")
    return value


def _inspect_one(asset: Mapping[str, str], max_asset_bytes: int) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_path = asset["path"]
    if not os.path.isabs(raw_path):
        raise MediaManifestError("media path must be absolute")
    if any(character in raw_path for character in _WILDCARD_CHARACTERS):
        raise MediaManifestError("media path must not contain wildcard characters")
    suffix = Path(raw_path).suffix.lower()
    expected_mime = _EXTENSIONS.get(suffix)
    if expected_mime is None:
        raise MediaManifestError("media path must have a PNG, JPEG, WebP, or GIF extension")

    before = _safe_regular_stat(raw_path)
    if before.st_size > max_asset_bytes:
        raise MediaManifestError("media file exceeds the per-file byte bound")
    digest, byte_size, payload, opened = _read_bounded_and_hash(raw_path, max_asset_bytes)
    after = _safe_regular_stat(raw_path)
    if not _same_file(before, opened) or not _same_file(before, after) or before.st_size != byte_size or after.st_size != byte_size:
        raise MediaManifestError("media file changed while it was being inspected")

    mime_type, width, height = _detect_image(payload)
    if mime_type != expected_mime:
        raise MediaManifestError("media file extension does not match its image header")
    public = {
        "file_id": asset["file_id"],
        "sha256": digest,
        "byte_size": byte_size,
        "mime_type": mime_type,
        "alt_text": asset["alt_text"],
    }
    private = {
        "path": raw_path,
        "path_sha256": hashlib.sha256(raw_path.encode("utf-8")).hexdigest(),
        "alt_text_sha256": hashlib.sha256(asset["alt_text"].encode("utf-8")).hexdigest(),
        "file_identity": _identity(before),
        "width": width,
        "height": height,
        "public": public,
    }
    return private, public


def _safe_regular_stat(raw_path: str) -> os.stat_result:
    try:
        file_stat = os.lstat(raw_path)
    except OSError as exc:
        raise MediaManifestError(f"cannot inspect media path: {exc}") from exc
    if stat.S_ISLNK(file_stat.st_mode):
        raise MediaManifestError("symbolic-link media paths are not allowed")
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(file_stat, "st_file_attributes", 0)
    if reparse_flag and attributes & reparse_flag:
        raise MediaManifestError("Windows reparse-point media paths are not allowed")
    if not stat.S_ISREG(file_stat.st_mode):
        raise MediaManifestError("media path must name a regular file")
    return file_stat


def _read_bounded_and_hash(raw_path: str, maximum: int) -> tuple[str, int, bytes, os.stat_result]:
    digest = hashlib.sha256()
    payload = bytearray()
    total = 0
    try:
        with open(raw_path, "rb") as source:
            opened = os.fstat(source.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise MediaManifestError("opened media is not a regular file")
            while True:
                chunk = source.read(_HASH_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise MediaManifestError("media file exceeds the per-file byte bound")
                digest.update(chunk)
                payload.extend(chunk)
    except MediaManifestError:
        raise
    except OSError as exc:
        raise MediaManifestError(f"cannot read media file: {exc}") from exc
    return digest.hexdigest(), total, bytes(payload), opened


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _identity(file_stat: os.stat_result) -> dict[str, int]:
    return {"device": int(file_stat.st_dev), "inode": int(file_stat.st_ino)}


def _detect_image(data: bytes) -> tuple[str, int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", *_png_dimensions(data)
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg", *_jpeg_dimensions(data)
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", *_gif_dimensions(data)
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp", *_webp_dimensions(data)
    raise MediaManifestError("media file is not a supported image with a parseable header")


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR" or data[8:12] != b"\x00\x00\x00\r":
        raise MediaManifestError("PNG header is truncated or malformed")
    width, height = struct.unpack(">II", data[16:24])
    return _valid_dimensions(width, height, "PNG")


def _gif_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 10:
        raise MediaManifestError("GIF header is truncated")
    width, height = struct.unpack("<HH", data[6:10])
    return _valid_dimensions(width, height, "GIF")


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    position = 2
    while position < len(data):
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if position + 2 > len(data):
            break
        segment_length = struct.unpack(">H", data[position:position + 2])[0]
        if segment_length < 2 or position + segment_length > len(data):
            raise MediaManifestError("JPEG header is truncated or malformed")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if segment_length < 8:
                raise MediaManifestError("JPEG frame header is malformed")
            height, width = struct.unpack(">HH", data[position + 3:position + 7])
            return _valid_dimensions(width, height, "JPEG")
        position += segment_length
    raise MediaManifestError("JPEG dimensions could not be read from its header")


def _webp_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 20:
        raise MediaManifestError("WebP header is truncated")
    declared_size = struct.unpack("<I", data[4:8])[0] + 8
    if declared_size > len(data):
        raise MediaManifestError("WebP RIFF data is truncated")
    position = 12
    limit = declared_size
    while position + 8 <= limit:
        chunk_type = data[position:position + 4]
        chunk_size = struct.unpack("<I", data[position + 4:position + 8])[0]
        start = position + 8
        end = start + chunk_size
        if end > limit:
            raise MediaManifestError("WebP chunk is truncated")
        payload = data[start:end]
        if chunk_type == b"VP8X":
            if len(payload) < 10:
                raise MediaManifestError("WebP VP8X header is truncated")
            width = 1 + int.from_bytes(payload[4:7], "little")
            height = 1 + int.from_bytes(payload[7:10], "little")
            return _valid_dimensions(width, height, "WebP")
        if chunk_type == b"VP8 ":
            if len(payload) < 10 or payload[3:6] != b"\x9d\x01\x2a":
                raise MediaManifestError("WebP VP8 header is malformed")
            width = struct.unpack("<H", payload[6:8])[0] & 0x3FFF
            height = struct.unpack("<H", payload[8:10])[0] & 0x3FFF
            return _valid_dimensions(width, height, "WebP")
        if chunk_type == b"VP8L":
            if len(payload) < 5 or payload[0] != 0x2F:
                raise MediaManifestError("WebP VP8L header is malformed")
            b1, b2, b3, b4 = payload[1:5]
            width = 1 + b1 + ((b2 & 0x3F) << 8)
            height = 1 + ((b2 >> 6) | (b3 << 2) | ((b4 & 0x0F) << 10))
            return _valid_dimensions(width, height, "WebP")
        position = end + (chunk_size & 1)
    raise MediaManifestError("WebP dimensions could not be read from its header")


def _valid_dimensions(width: int, height: int, image_type: str) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise MediaManifestError(f"{image_type} dimensions must be positive")
    return width, height


def _validate_captured_shape(private_manifest: Any, public_media: Any) -> None:
    if not isinstance(private_manifest, Mapping) or set(private_manifest) != {"version", "assets"}:
        raise MediaManifestError("private_manifest has an unsupported shape")
    if private_manifest["version"] != MANIFEST_VERSION:
        raise MediaManifestError("private_manifest version is unsupported")
    if not isinstance(public_media, list):
        raise MediaManifestError("public_media must be an ordered list")
    private_assets = private_manifest["assets"]
    if not isinstance(private_assets, list) or len(private_assets) != len(public_media) or len(private_assets) > MAX_ASSETS:
        raise MediaManifestError("captured asset lists are inconsistent")
    for private, public in zip(private_assets, public_media, strict=True):
        if not isinstance(private, Mapping) or set(private) != {"path", "path_sha256", "alt_text_sha256", "file_identity", "width", "height", "public"}:
            raise MediaManifestError("private asset has an unsupported shape")
        if not isinstance(public, Mapping) or set(public) != {"file_id", "sha256", "byte_size", "mime_type", "alt_text"}:
            raise MediaManifestError("public media item has an unsupported shape")
        if private["public"] != public:
            raise MediaManifestError("private and public media records do not match")
        _validate_input_assets([{"file_id": public["file_id"], "path": private["path"], "alt_text": public["alt_text"]}])
        if not isinstance(private["path_sha256"], str) or len(private["path_sha256"]) != 64:
            raise MediaManifestError("private path binding is invalid")
        if hashlib.sha256(private["path"].encode("utf-8")).hexdigest() != private["path_sha256"]:
            raise MediaManifestError("private path binding does not match path")
        if (not isinstance(private["alt_text_sha256"], str) or len(private["alt_text_sha256"]) != 64
                or hashlib.sha256(public["alt_text"].encode("utf-8")).hexdigest() != private["alt_text_sha256"]):
            raise MediaManifestError("private alt-text binding does not match public media")
        identity = private["file_identity"]
        if not isinstance(identity, Mapping) or set(identity) != {"device", "inode"} or any(
            isinstance(value, bool) or not isinstance(value, int) for value in identity.values()
        ):
            raise MediaManifestError("private file identity is invalid")
        if (not isinstance(private["width"], int) or not isinstance(private["height"], int)
                or private["width"] <= 0 or private["height"] <= 0):
            raise MediaManifestError("private dimensions are invalid")
