"""Check pinned inputs, reproducibility and preservation of Volume artifacts."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from build_volume import build_candidate, SOURCE, REPO
from volume_patch import install


class VolumeBuild(unittest.TestCase):
    def test_reproducible_build_preserves_existing_output(self):
        with TemporaryDirectory() as temporary:
            first, second = (Path(temporary) / name for name in ('a.ipsw', 'b.ipsw'))
            digest = build_candidate(SOURCE, REPO / 'iPod_4.3.1.1.ipsw', first)
            self.assertEqual(build_candidate(SOURCE, REPO / 'iPod_4.3.1.1.ipsw', second), digest)
            old = first.read_bytes()
            with self.assertRaisesRegex(ValueError, 'output already exists'):
                build_candidate(SOURCE, REPO / 'iPod_4.3.1.1.ipsw', first)
            self.assertEqual(first.read_bytes(), old)
            for path in first.with_suffix('.artifacts').iterdir():
                self.assertEqual(path.read_bytes(), (second.with_suffix('.artifacts') / path.name).read_bytes())

    def test_existing_review_artifacts_are_preserved(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / 'candidate.ipsw'
            directory = output.with_suffix('.artifacts')
            directory.mkdir()
            marker = directory / 'volume.elf'
            marker.write_bytes(b'previous review')
            with self.assertRaisesRegex(ValueError, 'artifacts already exist'):
                build_candidate(SOURCE, REPO / 'iPod_4.3.1.1.ipsw', output)
            self.assertEqual(marker.read_bytes(), b'previous review')
            self.assertFalse(output.exists())

    def test_unrecognized_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'expected pinned'):
            install(b'unrecognized')


if __name__ == '__main__':
    unittest.main(verbosity=2)
