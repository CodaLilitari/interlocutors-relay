import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from export_release import EXPORT_MANIFEST_NAME, ReleaseExportError, export_release, load_release_allowlist


class ExportReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.source.mkdir()
        self.output_parent = self.base / "exports"
        self.output_parent.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, content):
        path = self.source.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return path

    def allow(self, files):
        (self.source / "release-files.json").write_text(
            json.dumps({"version": 1, "files": files}), encoding="utf-8"
        )

    def export(self, name="clean", archive=None):
        return export_release(self.source, self.output_parent / name, archive=archive)

    def test_exports_only_reviewed_files_and_omits_unlisted_private_material(self):
        self.write("README.md", "# Coda and Lilitari\n\nContributor credit remains public.\n")
        self.write("src/tool.py", "VALUE = 'safe'\n")
        self.write("research/private-notes.txt", "not for release\n")
        self.write("private/account.txt", "not for release\n")
        self.allow(["README.md", "src/tool.py"])

        result = self.export()
        destination = Path(result["destination"])
        self.assertEqual((destination / "README.md").read_text(encoding="utf-8"), "# Coda and Lilitari\n\nContributor credit remains public.\n")
        self.assertTrue((destination / "src" / "tool.py").is_file())
        self.assertFalse((destination / "research").exists())
        self.assertFalse((destination / "private").exists())
        self.assertEqual(
            [item["path"] for item in result["manifest"]["files"]], ["README.md", "src/tool.py"]
        )
        self.assertIn("defense-in-depth", result["manifest"]["scan_notice"])

    def test_allowlist_rejects_traversal_absolute_duplicate_case_and_private_formats(self):
        invalid_lists = [
            ["../outside.py"],
            ["C:/outside.py"],
            ["/outside.py"],
            ["src\\tool.py"],
            ["README.md", "readme.md"],
            ["state.sqlite"],
            ["media/image.png"],
            [".env"],
        ]
        for files in invalid_lists:
            with self.subTest(files=files):
                self.allow(files)
                with self.assertRaises(ReleaseExportError):
                    load_release_allowlist(self.source / "release-files.json")

    def test_missing_and_symlink_listed_files_are_rejected(self):
        self.allow(["missing.py"])
        with self.assertRaises(ReleaseExportError):
            self.export()

        target = self.write("target.py", "VALUE = 1\n")
        link = self.source / "linked.py"
        try:
            os.symlink(target, link)
        except (NotImplementedError, OSError):
            self.skipTest("this platform does not permit test symlink creation")
        self.allow(["linked.py"])
        with self.assertRaises(ReleaseExportError):
            self.export("link-output")

    def test_changed_listed_bytes_produce_new_manifest_hash(self):
        source_file = self.write("relay.py", "VALUE = 'first'\n")
        self.allow(["relay.py"])
        first = self.export("first")
        first_record = first["manifest"]["files"][0]
        self.assertEqual(first_record["sha256"], hashlib.sha256(source_file.read_bytes()).hexdigest())

        source_file.write_text("VALUE = 'second'\n", encoding="utf-8", newline="\n")
        second = self.export("second")
        second_record = second["manifest"]["files"][0]
        self.assertNotEqual(first_record["sha256"], second_record["sha256"])
        self.assertEqual(second_record["size_bytes"], len(source_file.read_bytes()))
        self.assertEqual((Path(second["destination"]) / "relay.py").read_bytes(), source_file.read_bytes())

    def test_archive_is_deterministic_and_matches_manifest(self):
        self.write("README.md", "# Release\n")
        self.write("src/module.py", "ANSWER = 42\n")
        self.allow(["README.md", "src/module.py"])
        first_archive = self.output_parent / "first.zip"
        second_archive = self.output_parent / "second.zip"
        first = self.export("one", archive=first_archive)
        self.export("two", archive=second_archive)

        self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
        with zipfile.ZipFile(first_archive) as bundle:
            self.assertEqual(bundle.namelist(), ["README.md", "src/module.py", EXPORT_MANIFEST_NAME])
            manifest = json.loads(bundle.read(EXPORT_MANIFEST_NAME))
            self.assertEqual(manifest, first["manifest"])
            self.assertEqual(bundle.read("src/module.py"), b"ANSWER = 42\n")
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in bundle.infolist()))

    def test_never_overwrites_destination_or_archive_and_requires_export_outside_package(self):
        self.write("README.md", "# Release\n")
        self.allow(["README.md"])
        existing_destination = self.output_parent / "exists"
        existing_destination.mkdir()
        with self.assertRaises(ReleaseExportError):
            self.export("exists")

        occupied_archive = self.output_parent / "occupied.zip"
        occupied_archive.write_bytes(b"existing")
        with self.assertRaises(ReleaseExportError):
            self.export("fresh", archive=occupied_archive)
        with self.assertRaises(ReleaseExportError):
            export_release(self.source, self.source / "nested-export")

    def test_scan_rejects_real_home_path_but_does_not_ban_contributor_names(self):
        self.write("credit.md", "Coda and Lilitari contributed the credited review.\n")
        self.allow(["credit.md"])
        self.export("credit")

        home_path = str(Path.home())
        self.write("leak.md", f"local path: {home_path}\\private-note.txt\n")
        self.allow(["leak.md"])
        with self.assertRaises(ReleaseExportError):
            self.export("leak")

    def test_scan_rejects_token_headers_and_private_account_identifiers(self):
        # Construct the intentionally unsafe fixture at runtime so this test
        # source itself remains safely releasable under the same scan.
        token_fixture = '"Authorization": "Bear' + 'er ' + "secret-value-which-is-long\"\n"
        self.write("token.md", token_fixture)
        self.allow(["token.md"])
        with self.assertRaises(ReleaseExportError):
            self.export("token")

        account_fixture = '"page_id": "' + "123456789012345\"\n"
        self.write("account.md", account_fixture)
        self.allow(["account.md"])
        with self.assertRaises(ReleaseExportError):
            self.export("account")

    def test_credential_scan_allows_session_call_expressions_in_exported_source(self):
        expressions = [
            'session = commands.add_parser("session")',
            'session = self.inspect(session_id)',
            'session = self._session(session_id)',
            'session = commands.add_parser ("session")',
            'session = self.sessions[session_id]',
        ]
        source = "\n".join(expressions) + "\n"
        self.write("runtime.py", source)
        self.allow(["runtime.py"])
        result = self.export("session-expressions")
        self.assertEqual((Path(result["destination"]) / "runtime.py").read_text(encoding="utf-8"), source)

    def test_credential_scan_still_rejects_quoted_and_complete_bare_values(self):
        # Synthetic literals are assembled so the test file can itself be
        # exported through the same scanner without carrying a flagged secret.
        value = "synthetic-" + "credential-value-0123"
        names = ("password", "access_token", "refresh-token", "client_secret", "session", "session_id")
        forms = (value, value + " # trailing note", value + ";", value + "/suffix=", '"' + value + '"', "'" + value + "'")
        for index, (name, form) in enumerate((name, form) for name in names for form in forms):
            with self.subTest(name=name, form=form):
                self.write("settings.cfg", name + " = " + form + "\n")
                self.allow(["settings.cfg"])
                with self.assertRaisesRegex(ReleaseExportError, "credential assignment"):
                    self.export(f"credential-{index}")


if __name__ == "__main__":
    unittest.main()
