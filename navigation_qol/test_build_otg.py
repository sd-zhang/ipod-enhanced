"""Deterministic OTG packaging and no-overwrite/fail-closed guards."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from build_otg import build_candidate, SOURCE, REPO
from otg_patch import install


class OTGBuild(unittest.TestCase):
    def test_reproducible_build_preserves_existing_output_and_old_code_sidecars(self):
        with TemporaryDirectory() as temporary:
            first, second = (Path(temporary) / n for n in ('a.ipsw', 'b.ipsw'))
            digest = build_candidate(SOURCE, REPO / 'iPod_4.3.1.1.ipsw', first)
            self.assertEqual(build_candidate(SOURCE, REPO / 'iPod_4.3.1.1.ipsw', second), digest)
            old = first.read_bytes()
            with self.assertRaisesRegex(ValueError, 'output already exists'):
                build_candidate(SOURCE, REPO / 'iPod_4.3.1.1.ipsw', first)
            self.assertEqual(first.read_bytes(), old)
            for p in first.with_suffix('.artifacts').iterdir():
                self.assertEqual(p.read_bytes(), (second.with_suffix('.artifacts') / p.name).read_bytes())
                if not p.name.startswith('otg'):
                    self.assertEqual(p.read_bytes(), (SOURCE.with_suffix('.artifacts') / p.name).read_bytes())

    def test_existing_review_artifacts_are_preserved(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / 'candidate.ipsw'
            directory = output.with_suffix('.artifacts')
            directory.mkdir()
            marker = directory / 'review.json'
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
