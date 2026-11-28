"""The static app builds without firmware or release patch inputs."""

import hashlib
import html
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_web


class WebBuildTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_build_without_release_inputs_is_deterministic(self):
        first = self.root / "first"
        result = build_web.build_site(first)
        build_web.build_site(self.root / "second")
        page = (first / "index.html").read_bytes()
        self.assertEqual(page, (self.root / "second/index.html").read_bytes())
        self.assertEqual(set(p.name for p in first.iterdir()), {"index.html"})
        self.assertEqual(hashlib.sha256(page).hexdigest(), result["sha256"])

    def test_existing_site_is_preserved(self):
        output = self.root / "site"
        output.mkdir()
        (output / "index.html").write_text("keep me")
        with self.assertRaises(FileExistsError):
            build_web.build_site(output)
        self.assertEqual((output / "index.html").read_text(), "keep me")

    def test_failed_file_sync_leaves_no_partial_page(self):
        output = self.root / "site"
        with patch("build_web.os.fsync", side_effect=OSError("simulated disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                build_web.build_site(output)
        self.assertFalse(output.exists())

    def test_changed_vendor_code_is_refused(self):
        source = self.root / "web"
        shutil.copytree(build_web.WEB, source)
        (source / "vendor/BinFile.js").write_text("unreviewed replacement")
        with patch.object(build_web, "WEB", source):
            with self.assertRaisesRegex(ValueError, "Vendored source hash mismatch"):
                build_web.build_site(self.root / "site")
        self.assertFalse((self.root / "site").exists())

    def test_embedded_json_cannot_close_script_element(self):
        encoded = build_web.json_for_html({"text": "</script><script>alert(1)</script>"})
        self.assertNotIn("<", encoded)
        self.assertEqual(json.loads(encoded)["text"], "</script><script>alert(1)</script>")

    def test_consolidated_licenses_preserve_bundled_attribution_and_terms(self):
        page = build_web.render_page().decode()
        panel = html.unescape(
            page.split("<details><summary>Licenses</summary>", 1)[1].split("</details>", 1)[0]
        )
        for notice in [
            "Copyright (c) 2026 sd-zhang",
            "Copyright (c) 2017-2024 Marc Robledo",
            "Copyright (c) 2014-2024 Marc Robledo",
            "Copyright (C) 2014-2017 by Vitaly Puzrin and Andrei Tuputcyn",
            "bz2 (C) 2019-present SheetJS LLC",
            "Copyright 2019 SheetJS LLC",
            "(C) 1995-2013 Jean-loup Gailly and Mark Adler",
            "(C) 2014-2017 Vitaly Puzrin and Andrey Tupitsin",
        ]:
            with self.subTest(notice=notice):
                self.assertIn(notice, panel)
        terms = (build_web.HERE.parent / "LICENSE").read_text().split("Permission is hereby", 1)[1]
        self.assertEqual(panel.count("Permission is hereby"), 1)
        self.assertIn(" ".join(terms.split()), " ".join(panel.split()))
        zlib = (build_web.WEB / "vendor/pako-zlib-LICENSE.txt").read_text()
        zlib = "\n".join(line.removeprefix("//").removeprefix(" ") for line in zlib.splitlines())
        self.assertIn(zlib.strip(), panel)
        self.assertNotIn("Octicons", panel)
        self.assertNotIn("Huffman", panel)
        self.assertIn("modified", panel)

    def test_different_mit_terms_are_not_silently_merged(self):
        source = self.root / "web"
        shutil.copytree(build_web.WEB, source)
        license_path = source / "vendor/pako-LICENSE.txt"
        license_path.write_text(license_path.read_text() + "An additional license condition.\n")
        manifest_path = source / "vendor/sources.json"
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest["files"]:
            if entry["file"] == "pako-LICENSE.txt":
                entry["sha256"] = hashlib.sha256(license_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        with patch.object(build_web, "WEB", source):
            with self.assertRaisesRegex(ValueError, "MIT terms differ"):
                build_web.build_site(self.root / "site")
        self.assertFalse((self.root / "site").exists())


if __name__ == "__main__":
    unittest.main()
