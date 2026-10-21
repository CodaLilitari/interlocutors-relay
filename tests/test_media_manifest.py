import copy
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from media_manifest import MediaManifestError, inspect_media, verify_media_manifest


def png(width=2, height=3, fill=b""):
    """Header-complete synthetic PNG bytes; no real image or persona content."""
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00" + fill


def jpeg(width=4, height=5):
    return b"\xff\xd8\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width) + b"\x03" + b"\x00" * 9


def gif(width=6, height=7):
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x80\x00\x00"


def webp(width=8, height=9):
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00" * 4 + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


class MediaManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, payload):
        path = self.root / name
        path.write_bytes(payload)
        return str(path)

    def asset(self, file_id, path, alt_text="synthetic image"):
        return {"file_id": file_id, "path": path, "alt_text": alt_text}

    def test_inspects_supported_headers_and_public_list_has_no_paths(self):
        assets = [
            self.asset("p", self.write("one.png", png(2, 3)), "two by three"),
            self.asset("j", self.write("two.jpeg", jpeg(4, 5)), "four by five"),
            self.asset("g", self.write("three.gif", gif(6, 7)), "six by seven"),
            self.asset("w", self.write("four.webp", webp(8, 9)), "eight by nine"),
        ]
        captured = inspect_media(assets)
        self.assertEqual([item["file_id"] for item in captured["public_media"]], ["p", "j", "g", "w"])
        self.assertEqual(
            [item["mime_type"] for item in captured["public_media"]],
            ["image/png", "image/jpeg", "image/gif", "image/webp"],
        )
        self.assertTrue(all(set(item) == {"file_id", "sha256", "byte_size", "mime_type", "alt_text"}
                            for item in captured["public_media"]))
        self.assertTrue(all("path" not in item for item in captured["public_media"]))
        self.assertEqual(
            [(item["width"], item["height"]) for item in captured["private_manifest"]["assets"]],
            [(2, 3), (4, 5), (6, 7), (8, 9)],
        )

    def test_reopens_exact_captured_files(self):
        path = self.write("asset.png", png())
        captured = inspect_media([self.asset("asset-1", path, "a synthetic rectangle")])
        self.assertEqual(verify_media_manifest(captured), captured["public_media"])

    def test_content_swap_and_truncation_fail_reverification(self):
        path = self.write("asset.png", png(2, 3))
        captured = inspect_media([self.asset("asset-1", path)])
        Path(path).write_bytes(png(12, 13))
        with self.assertRaises(MediaManifestError):
            verify_media_manifest(captured)

        truncated = self.write("truncated.png", b"\x89PNG\r\n\x1a\n\x00")
        with self.assertRaises(MediaManifestError):
            inspect_media([self.asset("bad", truncated)])

    def test_path_binding_rejects_duplicate_copy_even_when_content_matches(self):
        original = self.write("original.png", png())
        duplicate = self.write("duplicate.png", Path(original).read_bytes())
        captured = inspect_media([self.asset("asset-1", original)])
        changed = copy.deepcopy(captured)
        private = changed["private_manifest"]["assets"][0]
        private["path"] = duplicate
        private["path_sha256"] = hashlib.sha256(duplicate.encode("utf-8")).hexdigest()
        with self.assertRaises(MediaManifestError):
            verify_media_manifest(changed)

    def test_hash_size_and_alt_text_bindings_are_checked(self):
        path = self.write("asset.png", png())
        captured = inspect_media([self.asset("asset-1", path, "first description")])

        bad_hash = copy.deepcopy(captured)
        bad_hash["public_media"][0]["sha256"] = "0" * 64
        bad_hash["private_manifest"]["assets"][0]["public"]["sha256"] = "0" * 64
        with self.assertRaises(MediaManifestError):
            verify_media_manifest(bad_hash)

        bad_size = copy.deepcopy(captured)
        bad_size["public_media"][0]["byte_size"] += 1
        bad_size["private_manifest"]["assets"][0]["public"]["byte_size"] += 1
        with self.assertRaises(MediaManifestError):
            verify_media_manifest(bad_size)

        bad_alt = copy.deepcopy(captured)
        bad_alt["public_media"][0]["alt_text"] = "different description"
        bad_alt["private_manifest"]["assets"][0]["public"]["alt_text"] = "different description"
        with self.assertRaises(MediaManifestError):
            verify_media_manifest(bad_alt)

    def test_order_duplicate_ids_duplicate_paths_and_invalid_paths_are_rejected(self):
        first = self.write("first.png", png())
        second = self.write("second.png", png(3, 4))
        captured = inspect_media([self.asset("b", second), self.asset("a", first)])
        self.assertEqual([item["file_id"] for item in captured["public_media"]], ["b", "a"])
        with self.assertRaises(MediaManifestError):
            inspect_media([self.asset("same", first), self.asset("same", second)])
        with self.assertRaises(MediaManifestError):
            inspect_media([self.asset("one", first), self.asset("two", first)])
        with self.assertRaises(MediaManifestError):
            inspect_media([self.asset("relative", "relative.png")])
        with self.assertRaises(MediaManifestError):
            inspect_media([self.asset("wildcard", str(self.root / "*.png"))])

    def test_rejects_directories_nonimages_and_bound_excess(self):
        with self.assertRaises(MediaManifestError):
            inspect_media([self.asset("directory", str(self.root))])
        text = self.write("not-image.png", b"not an image")
        with self.assertRaises(MediaManifestError):
            inspect_media([self.asset("text", text)])
        oversized = self.write("large.png", png(fill=b"x" * 50))
        with self.assertRaises(MediaManifestError):
            inspect_media([self.asset("large", oversized)], max_asset_bytes=32)


if __name__ == "__main__":
    unittest.main()
