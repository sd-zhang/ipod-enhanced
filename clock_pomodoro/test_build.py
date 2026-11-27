"""Deterministic packaging, source pinning and preservation of existing outputs."""
import unittest
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory


class CandidateBuild(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('pomo_builder', Path(__file__).parent / 'build.py')
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)

    def test_build_is_deterministic_and_existing_archive_is_never_overwritten(self):
        b = self.builder
        with TemporaryDirectory() as temporary:
            first, second = (Path(temporary) / name for name in ('a.ipsw', 'b.ipsw'))
            digest = b.build_candidate(b.SOURCE, b.REPO / 'iPod_4.3.1.1.ipsw', first)
            self.assertEqual(b.build_candidate(b.SOURCE, b.REPO / 'iPod_4.3.1.1.ipsw', second), digest)
            old = first.read_bytes()
            with self.assertRaisesRegex(ValueError, 'output already exists'):
                b.build_candidate(b.SOURCE, b.REPO / 'iPod_4.3.1.1.ipsw', first)
            self.assertEqual(first.read_bytes(), old)
            for p in first.with_suffix('.artifacts').iterdir():
                self.assertEqual(p.read_bytes(), (second.with_suffix('.artifacts') / p.name).read_bytes())

    def test_existing_artifacts_are_preserved_and_bad_source_is_rejected(self):
        b = self.builder
        with TemporaryDirectory() as temporary:
            out = Path(temporary) / 'candidate.ipsw'
            artifacts = out.with_suffix('.artifacts')
            artifacts.mkdir()
            marker = artifacts / 'review.json'
            marker.write_bytes(b'previous review')
            with self.assertRaisesRegex(ValueError, 'artifacts already exist'):
                b.build_candidate(b.SOURCE, b.REPO / 'iPod_4.3.1.1.ipsw', out)
            self.assertEqual(marker.read_bytes(), b'previous review')
            self.assertFalse(out.exists())
        from pomodoro_patch import install
        with self.assertRaisesRegex(ValueError, 'expected pinned r19'):
            install(b'unrecognized')


if __name__ == '__main__':
    unittest.main(verbosity=2)
