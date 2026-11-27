"""The static app builds without firmware or release patch inputs."""

import hashlib
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


if __name__ == "__main__":
    unittest.main()
