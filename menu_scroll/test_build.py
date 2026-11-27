"""Rejection checks: bad inputs must not publish or overwrite firmware."""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build import REPO, MEMBER, build, read_archive
from patch import install
from verify_resources import payload

SOURCE = REPO / "build/gapless-complete-drain/iPod_4.3.1.1-gapless.ipsw"
STOCK = REPO / "iPod_4.3.1.1.ipsw"


class BuildGuards(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.output = self.directory / "candidate.ipsw"

    def test_existing_output_is_never_overwritten(self):
        self.output.write_bytes(b"keep existing firmware")
        with self.assertRaisesRegex(ValueError, "already exists"):
            build(SOURCE, STOCK, self.output)
        self.assertEqual(self.output.read_bytes(), b"keep existing firmware")

    def test_bad_zip_reports_input_error_without_output(self):
        source = self.directory / "bad.ipsw"
        source.write_bytes(b"not a zip archive")
        with self.assertRaisesRegex(ValueError, "archive"):
            build(source, STOCK, self.output)
        self.assertFalse(self.output.exists())

    def test_wrong_model_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "4G"):
            build(REPO / "iPod_25.1.3.ipsw", STOCK, self.output)
        self.assertFalse(self.output.exists())

    def test_duplicate_members_are_rejected(self):
        import warnings
        source = self.directory / "duplicate.ipsw"
        with warnings.catch_warnings(), ZipFile(source, "w") as archive:
            warnings.simplefilter("ignore", UserWarning)
            archive.writestr("manifest.plist", b"first")
            archive.writestr("manifest.plist", b"second")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build(source, STOCK, self.output)
        self.assertFalse(self.output.exists())

    def test_bad_container_checksum_is_rejected(self):
        infos, members = read_archive(SOURCE)
        damaged = bytearray(members[MEMBER])
        damaged[0x421c] ^= 1
        members[MEMBER] = bytes(damaged)
        source = self.directory / "damaged.ipsw"
        with ZipFile(source, "w") as archive:
            for info in infos:
                archive.writestr(info, members[info.filename])
        with self.assertRaisesRegex(ValueError, "container"):
            build(source, STOCK, self.output)
        self.assertFalse(self.output.exists())

    def test_unknown_and_already_patched_images_are_rejected(self):
        _, members = read_archive(SOURCE)
        image = payload(members[MEMBER], 0x4200)
        with self.assertRaisesRegex(ValueError, "unsupported OSOS"):
            install(image[:-1])
        with self.assertRaisesRegex(ValueError, "unsupported OSOS"):
            install(install(image))


if __name__ == "__main__":
    unittest.main(verbosity=2)
