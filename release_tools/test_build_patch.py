"""Distribution packaging tests using synthetic archives and native bspatch."""

import hashlib
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

import apply_patch
import build_patch
from test_patch import archive, identity


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = bytes(range(256)) * 16
        self.new = self.old[17:] + b"project extension"
        self.stock = self.root / "stock.ipsw"
        self.release = self.root / "release.ipsw"
        self.output = self.root / "patch.zip"
        self.stock.write_bytes(archive(self.old))
        self.release.write_bytes(archive(self.new))
        self.profile = {
            "model": "Test model",
            "revision": "r1",
            "member": "Firmware-test",
            "stock_name": "stock.ipsw",
            "release_name": "release.ipsw",
            "source_sha256": identity(self.stock.read_bytes())["sha256"],
            "target_sha256": identity(self.release.read_bytes())["sha256"],
        }

    def build(self):
        return build_patch.build_bundle(
            self.profile, self.stock, self.release, self.output, include_tools=True
        )

    def test_lean_patch_contains_only_data_and_reconstructs_exact_firmware(self):
        self.output = self.root / "release.ipepatch"
        build_patch.build_bundle(self.profile, self.stock, self.release, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(set(archive.namelist()), {"manifest.json", "firmware.bsdiff"})
            archive.extractall(self.root / "lean")
        applied = self.root / "lean.ipsw"
        apply_patch.apply_bundle(self.root / "lean", self.stock, applied)
        self.assertEqual(applied.read_bytes(), self.release.read_bytes())

    def test_bundle_roundtrip_allowlist_and_checksums(self):
        self.build()
        bundle = self.root / "unpacked"
        with zipfile.ZipFile(self.output) as result:
            self.assertEqual(
                set(result.namelist()),
                {
                    "apply_patch.py",
                    "firmware.bsdiff",
                    "manifest.json",
                    "requirements.txt",
                    "README.txt",
                    "LICENSE",
                    "SHA256SUMS",
                },
            )
            result.extractall(bundle)
            for line in result.read("SHA256SUMS").decode().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256(result.read(name)).hexdigest(), digest)
            self.assertNotIn(str(self.root), result.read("manifest.json").decode())
            self.assertNotIn(str(self.root), result.read("README.txt").decode())
        output = self.root / "applied.ipsw"
        apply_patch.apply_bundle(bundle, self.stock, output)
        self.assertEqual(output.read_bytes(), self.release.read_bytes())

    def test_stock_and_release_pins_reject_wrong_inputs(self):
        for key in ("source_sha256", "target_sha256"):
            with self.subTest(key=key):
                saved = self.profile[key]
                self.profile[key] = "0" * 64
                with self.assertRaises(ValueError):
                    self.build()
                self.assertFalse(self.output.exists())
                self.profile[key] = saved

    def test_unrelated_updater_change_is_rejected_even_with_matching_pin(self):
        self.release.write_bytes(archive(self.new, manifest=b"changed updater"))
        self.profile["target_sha256"] = identity(self.release.read_bytes())["sha256"]
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_changed_zip_metadata_is_rejected(self):
        with zipfile.ZipFile(self.release, "a") as output:
            output.comment = b"new archive comment"
        self.profile["target_sha256"] = identity(self.release.read_bytes())["sha256"]
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_existing_bundle_is_immutable_and_build_is_deterministic(self):
        self.build()
        original = self.output.read_bytes()
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(self.output.read_bytes(), original)
        self.output = self.root / "second.zip"
        self.build()
        self.assertEqual(self.output.read_bytes(), original)

    @unittest.skipUnless(shutil.which("bspatch"), "independent native bspatch unavailable")
    def test_generated_delta_applies_with_independent_native_bspatch(self):
        self.build()
        old = self.root / "old.bin"
        new = self.root / "new.bin"
        delta = self.root / "patch.bsdiff"
        old.write_bytes(self.old)
        with zipfile.ZipFile(self.output) as bundle:
            delta.write_bytes(bundle.read("firmware.bsdiff"))
        executable = shutil.which("bspatch")
        if executable is None:
            self.skipTest("independent native bspatch unavailable")
        result = subprocess.run(
            [executable, str(old), str(new), str(delta)], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(new.read_bytes(), self.new)


if __name__ == "__main__":
    unittest.main()
