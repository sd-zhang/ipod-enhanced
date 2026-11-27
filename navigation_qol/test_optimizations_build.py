"""Candidate determinism and preservation of older packages/evidence."""
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from elftools.elf.elffile import ELFFile
import optimize_build as builder


class Build(unittest.TestCase):
    def test_rebuild_is_identical_and_outputs_cannot_be_overwritten(self):
        with TemporaryDirectory() as directory:
            first, second = (Path(directory) / name for name in ('a.ipsw', 'b.ipsw'))
            stock = builder.REPO / 'iPod_4.3.1.1.ipsw'
            one = builder.build_candidate(builder.SOURCE, stock, first)
            two = builder.build_candidate(builder.SOURCE, stock, second)
            self.assertEqual(one, two)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            for p in first.with_suffix('.artifacts').iterdir():
                self.assertEqual(p.read_bytes(), (second.with_suffix('.artifacts') / p.name).read_bytes())
            with self.assertRaisesRegex(ValueError, 'output already exists'):
                builder.build_candidate(builder.SOURCE, stock, first)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            orphan = Path(directory) / 'previous.ipsw'
            orphan.with_suffix('.artifacts').mkdir()
            marker = orphan.with_suffix('.artifacts') / 'review'
            marker.write_bytes(b'preserve')
            with self.assertRaisesRegex(ValueError, 'artifacts already exist'):
                builder.build_candidate(builder.SOURCE, stock, orphan)
            self.assertEqual(marker.read_bytes(), b'preserve')
            self.assertFalse(orphan.exists())

    def test_wrong_source_or_review_elf_produces_no_output(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            output = path / 'bad.ipsw'
            source = path / 'source.ipsw'
            source.write_bytes(b'not the pinned release')
            with self.assertRaisesRegex(ValueError, 'expected pinned r25'):
                builder.build_candidate(source, builder.REPO / 'iPod_4.3.1.1.ipsw', output)
            old = builder.PLAYBACK.read_bytes()
            at = ELFFile(io.BytesIO(old)).get_section_by_name('.text')['sh_offset']
            damaged = bytearray(old)
            damaged[at] ^= 1
            elf = path / 'wrong.elf'
            elf.write_bytes(damaged)
            with self.assertRaisesRegex(ValueError, 'playback ELF differs'):
                builder.build_candidate(builder.SOURCE, builder.REPO / 'iPod_4.3.1.1.ipsw', output, elf)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix('.artifacts').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
