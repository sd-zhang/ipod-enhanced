"""Check deterministic alarm builds and refusal to overwrite old evidence."""
import io
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from elftools.elf.elffile import ELFFile
import alert_build as builder


class AlertBuild(unittest.TestCase):
    def test_rebuild_is_identical_and_preserves_existing_outputs(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = root / 'a.ipsw', root / 'b.ipsw'
            stock = builder.REPO / 'iPod_4.3.1.1.ipsw'
            self.assertEqual(builder.build_candidate(builder.SOURCE, stock, first),
                             builder.build_candidate(builder.SOURCE, stock, second))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            for path in first.with_suffix('.artifacts').iterdir():
                self.assertEqual(path.read_bytes(), (second.with_suffix('.artifacts') / path.name).read_bytes())
            with self.assertRaisesRegex(ValueError, 'output already exists'):
                builder.build_candidate(builder.SOURCE, stock, first)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            orphan = root / 'older.ipsw'
            orphan.with_suffix('.artifacts').mkdir()
            evidence = orphan.with_suffix('.artifacts') / 'review'
            evidence.write_bytes(b'previous review')
            with self.assertRaisesRegex(ValueError, 'artifacts already exist'):
                builder.build_candidate(builder.SOURCE, stock, orphan)
            self.assertEqual(evidence.read_bytes(), b'previous review')
            self.assertFalse(orphan.exists())

    def test_wrong_source_or_mismatched_carried_elf_produces_no_output(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / 'source.ipsw', root / 'bad.ipsw'
            stock = builder.REPO / 'iPod_4.3.1.1.ipsw'
            source.write_bytes(b'not r26')
            with self.assertRaisesRegex(ValueError, 'expected pinned r26'):
                builder.build_candidate(source, stock, output)
            shutil.copyfile(builder.SOURCE, source)
            shutil.copytree(builder.SOURCE.with_suffix('.artifacts'), source.with_suffix('.artifacts'),
                            ignore=shutil.ignore_patterns('sources', 'review', '*.log'))
            path = source.with_suffix('.artifacts') / 'playback.elf'
            damaged = bytearray(path.read_bytes())
            offset = ELFFile(io.BytesIO(damaged)).get_section_by_name('.text')['sh_offset']
            damaged[offset] ^= 1
            path.write_bytes(damaged)
            with self.assertRaisesRegex(ValueError, 'playback ELF differs'):
                builder.build_candidate(source, stock, output)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix('.artifacts').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
