"""Check deterministic r16 builds, pinned inputs and review-asset preservation."""
import io
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from elftools.elf.elffile import ELFFile
from build_hotpaths import build_candidate, SOURCE, REPO
from hotpaths_patch import install


class HotpathsBuild(unittest.TestCase):
    def test_reproducible_build_preserves_existing_output_and_artifacts(self):
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
            artifacts = output.with_suffix('.artifacts')
            artifacts.mkdir()
            marker = artifacts / 'review.json'
            marker.write_bytes(b'previous review')
            with self.assertRaisesRegex(ValueError, 'artifacts already exist'):
                build_candidate(SOURCE, REPO / 'iPod_4.3.1.1.ipsw', output)
            self.assertEqual(marker.read_bytes(), b'previous review')
            self.assertFalse(output.exists())

    def test_unrecognized_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'expected pinned'):
            install(b'unrecognized')

    def test_elf_with_code_different_from_firmware_is_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / 'source.ipsw', root / 'out.ipsw'
            shutil.copyfile(SOURCE, source)
            artifacts = source.with_suffix('.artifacts')
            artifacts.mkdir()
            for name in ('letters', 'actions', 'volume'):
                shutil.copyfile(SOURCE.with_suffix('.artifacts') / (name + '.elf'), artifacts / (name + '.elf'))
            path = artifacts / 'volume.elf'
            raw = bytearray(path.read_bytes())
            section = ELFFile(io.BytesIO(raw)).get_section_by_name('.text')
            raw[section['sh_offset']] ^= 1
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, 'volume review ELF differs'):
                build_candidate(source, REPO / 'iPod_4.3.1.1.ipsw', output)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix('.artifacts').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
