"""Offline release behavior; synthetic IPSWs contain no Apple firmware."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import apply_patch
import bsdiff4


def identity(data):
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def archive(firmware, manifest=b"<plist>unchanged</plist>"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as result:
        result.comment = b"fixture"
        for name, data in [("Firmware-test", firmware), ("manifest.plist", manifest)]:
            info = zipfile.ZipInfo(name, (2006, 1, 2, 3, 4, 6))
            info.compress_type = zipfile.ZIP_DEFLATED
            result.writestr(info, data)
    return output.getvalue()


class ApplyTests(unittest.TestCase):
    tool = apply_patch

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.old = bytes(range(256)) * 32
        self.new = self.old[:100] + b"new firmware code" + self.old[101:] + b"extension"
        self.source = self.root / "stock.ipsw"
        self.source.write_bytes(archive(self.old))
        self.expected = archive(self.new)
        self.output = self.root / "enhanced.ipsw"
        self.delta = bsdiff4.diff(self.old, self.new)
        self.manifest = {
            "format": "ipod-enhanced-bsdiff-v1",
            "model": "test",
            "revision": "r1",
            "member": "Firmware-test",
            "source_archive": identity(self.source.read_bytes()),
            "source_firmware": identity(self.old),
            "target_archive": identity(self.expected),
            "target_firmware": identity(self.new),
            "patch": identity(self.delta),
        }
        self.save()

    def save(self):
        (self.bundle / "manifest.json").write_text(json.dumps(self.manifest))
        (self.bundle / "firmware.bsdiff").write_bytes(self.delta)

    def apply(self, **kwargs):
        return self.tool.apply_bundle(self.bundle, self.source, self.output, **kwargs)

    def test_exact_archive_and_unchanged_source(self):
        before = self.source.read_bytes()
        self.apply()
        self.assertEqual(self.output.read_bytes(), self.expected)
        self.assertEqual(self.source.read_bytes(), before)
        with zipfile.ZipFile(self.output) as result:
            self.assertEqual(result.read("manifest.plist"), b"<plist>unchanged</plist>")
            self.assertEqual(result.read("Firmware-test"), self.new)

    def test_raw_mode_emits_firmware_member_only(self):
        self.apply(firmware_only=True)
        self.assertEqual(self.output.read_bytes(), self.new)

    def test_wrong_or_already_patched_input_leaves_no_output(self):
        for data in (b"not an IPSW", archive(b"wrong model"), self.expected):
            with self.subTest(data_size=len(data)):
                self.source.write_bytes(data)
                with self.assertRaises(ValueError):
                    self.apply()
                self.assertFalse(self.output.exists())

    def test_corrupt_or_truncated_patch_leaves_no_output(self):
        for data in (self.delta[:-5], b"broken" + self.delta[6:]):
            with self.subTest(size=len(data)):
                (self.bundle / "firmware.bsdiff").write_bytes(data)
                with self.assertRaises(ValueError):
                    self.apply()
                self.assertFalse(self.output.exists())

    def test_malformed_patch_with_matching_digest_is_rejected(self):
        self.delta = self.delta[:-10]
        self.manifest["patch"] = identity(self.delta)
        self.save()
        with self.assertRaises(ValueError):
            self.apply()
        self.assertFalse(self.output.exists())

    def test_source_member_target_member_and_archive_hashes_are_enforced(self):
        for key in ("source_firmware", "target_firmware", "target_archive"):
            with self.subTest(key=key):
                original = self.manifest[key]["sha256"]
                self.manifest[key]["sha256"] = "0" * 64
                self.save()
                with self.assertRaises(ValueError):
                    self.apply()
                self.assertFalse(self.output.exists())
                self.manifest[key]["sha256"] = original

    def test_malformed_manifest_and_large_output_are_rejected(self):
        for mutate in (
            lambda m: m.update(format="unknown"),
            lambda m: m.update(member="../outside"),
            lambda m: m["target_firmware"].update(size=2**40),
            lambda m: m["target_firmware"].update(size=True),
            lambda m: m["target_firmware"].update(sha256="bad"),
        ):
            with self.subTest(mutate=mutate):
                original = json.dumps(self.manifest)
                mutate(self.manifest)
                self.save()
                with self.assertRaises(ValueError):
                    self.apply()
                self.assertFalse(self.output.exists())
                self.manifest = json.loads(original)

    def test_bsdiff_declared_output_size_is_checked_before_decoder(self):
        self.delta = self.delta[:24] + (2**40).to_bytes(8, "little") + self.delta[32:]
        self.manifest["patch"] = identity(self.delta)
        self.save()
        with self.assertRaises(ValueError):
            self.apply()
        self.assertFalse(self.output.exists())

    def test_existing_file_directory_and_symlink_are_preserved(self):
        self.output.write_bytes(b"keep me")
        with self.assertRaises(FileExistsError):
            self.apply()
        self.assertEqual(self.output.read_bytes(), b"keep me")
        self.output.unlink()
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.apply()
        self.assertTrue(self.output.is_dir())
        self.output.rmdir()
        self.output.symlink_to(self.root / "absent")
        with self.assertRaises(FileExistsError):
            self.apply()
        self.assertTrue(self.output.is_symlink())
        self.assertFalse((self.root / "absent").exists())

    def test_output_equal_source_never_overwrites(self):
        self.output = self.source
        before = self.source.read_bytes()
        with self.assertRaises(FileExistsError):
            self.apply()
        self.assertEqual(self.source.read_bytes(), before)

    def test_nonregular_source_and_symlink_are_rejected(self):
        original = self.source
        link = self.root / "source-link"
        link.symlink_to(original)
        for source in (link, self.bundle):
            self.source = source
            with self.assertRaises((ValueError, OSError)):
                self.apply()
            self.assertFalse(self.output.exists())

    def test_fsync_failure_cleans_temporary_and_final_output(self):
        before = set(self.root.iterdir())
        with patch.object(self.tool.os, "fsync", side_effect=OSError("injected disk failure")):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(set(self.root.iterdir()), before)

    def test_exclusive_publication_does_not_replace_racing_file(self):
        real_link = os.link

        def race(source, destination):
            Path(destination).write_bytes(b"other writer")
            return real_link(source, destination)

        with patch.object(self.tool.os, "link", side_effect=race):
            with self.assertRaises(FileExistsError):
                self.apply()
        self.assertEqual(self.output.read_bytes(), b"other writer")

    def test_cli_operates_outside_repository_and_fails_without_traceback(self):
        script = Path(__file__).with_name("apply_patch.py").resolve()
        command = [
            sys.executable,
            "-B",
            str(script),
            str(self.source),
            "--bundle",
            str(self.bundle),
            "--output",
            str(self.output),
        ]
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), self.expected)
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.output.read_bytes(), self.expected)


if __name__ == "__main__":
    unittest.main()
