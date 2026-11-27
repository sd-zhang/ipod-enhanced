"""Non-device build preservation checks; no Unicorn execution."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from build import REPO, build
from letters_patch import install

SOURCE = REPO / "build/navigation-qol/iPod_4.3.1.1-gapless-navigation.ipsw"
STOCK = REPO / "iPod_4.3.1.1.ipsw"


def candidate(output):
    return build(SOURCE, STOCK, output,
                 installer=lambda image: install(image, output.with_suffix(".artifacts")))


class PreserveBuilds(unittest.TestCase):
    def test_rebuild_is_reproducible_and_preserves_existing_artifacts(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first, second = directory / "first.ipsw", directory / "second.ipsw"
            digest = candidate(first)
            snapshot = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
            with self.assertRaisesRegex(ValueError, "output already exists"):
                candidate(first)
            self.assertEqual(candidate(second), digest)
            for path, data in snapshot.items():
                self.assertEqual((directory / path).read_bytes(), data)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_existing_review_directory_is_never_overwritten(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "candidate.ipsw"
            artifacts = output.with_suffix(".artifacts")
            artifacts.mkdir()
            marker = artifacts / "letters.elf"
            marker.write_bytes(b"previous reviewed binary")
            with self.assertRaisesRegex(ValueError, "artifact directory already exists"):
                candidate(output)
            self.assertEqual(marker.read_bytes(), b"previous reviewed binary")
            self.assertFalse(output.exists())

    def test_unknown_or_already_patched_input_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "expected pinned"):
            install(b"unknown firmware")


if __name__ == "__main__":
    unittest.main(verbosity=2)
