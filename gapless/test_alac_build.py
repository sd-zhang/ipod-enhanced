"""ALAC packaging reproducibility, rejection, and independent mutation checks."""
import io
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile
import alac_build as builder
from verify_alac_package import validated_base


class ALACBuild(unittest.TestCase):
    def test_identical_rebuild_and_overwrite_refusal(self):
        with TemporaryDirectory() as temporary:
            first, second = (Path(temporary) / name for name in ('a.ipsw', 'b.ipsw'))
            stock = builder.REPO / 'iPod_4.3.1.1.ipsw'
            self.assertEqual(builder.build_candidate(builder.SOURCE, stock, first),
                             builder.build_candidate(builder.SOURCE, stock, second))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            validated_base(first)
            for path in first.with_suffix('.artifacts').iterdir():
                self.assertEqual(path.read_bytes(), (second.with_suffix('.artifacts') / path.name).read_bytes())
            with self.assertRaisesRegex(ValueError, 'output already exists'):
                builder.build_candidate(builder.SOURCE, stock, first)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            orphan = Path(temporary) / 'orphan.ipsw'
            orphan.with_suffix('.artifacts').mkdir()
            with self.assertRaisesRegex(ValueError, 'output already exists'):
                builder.build_candidate(builder.SOURCE, stock, orphan)
            self.assertFalse(orphan.exists())

    def test_source_pin_and_carried_code_mismatch_rejected(self):
        with TemporaryDirectory() as temporary:
            source, output = (Path(temporary) / name for name in ('source.ipsw', 'out.ipsw'))
            stock = builder.REPO / 'iPod_4.3.1.1.ipsw'
            source.write_bytes(b'incorrect source')
            with self.assertRaisesRegex(ValueError, 'expected pinned'):
                builder.build_candidate(source, stock, output)
            shutil.copyfile(builder.SOURCE, source)
            shutil.copytree(builder.SOURCE.with_suffix('.artifacts'), source.with_suffix('.artifacts'))
            path = source.with_suffix('.artifacts') / 'playback.elf'
            raw = bytearray(path.read_bytes())
            offset = ELFFile(io.BytesIO(raw)).get_section_by_name('.text')['sh_offset']
            raw[offset] ^= 1
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, 'playback.elf ELF differs'):
                builder.build_candidate(source, stock, output)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix('.artifacts').exists())

    def test_independent_validator_rejects_undeclared_byte_and_wrong_hook(self):
        with TemporaryDirectory() as temporary:
            candidate = Path(temporary) / 'candidate.ipsw'
            stock = builder.REPO / 'iPod_4.3.1.1.ipsw'
            builder.build_candidate(builder.SOURCE, stock, candidate)
            infos, members, comment = builder.read_archive(candidate)
            firmware = members[builder.MEMBER]
            start = 512 + builder.word(firmware, 0x420c)
            image = firmware[start:start + builder.word(firmware, 0x4210)]
            original = builder.read_archive(stock)[1][builder.MEMBER]
            for at in (0x1000, 0x4f540, 0x358b20, 0x38ad10):
                with self.subTest(address=hex(at)):
                    damaged = bytearray(image)
                    damaged[at] ^= 1
                    changed = dict(members)
                    changed[builder.MEMBER] = bytes(builder.repack(original, damaged))
                    with ZipFile(candidate, 'w') as archive:
                        archive.comment = comment
                        for info in infos:
                            archive.writestr(info, changed[info.filename])
                    with self.assertRaises(AssertionError):
                        validated_base(candidate)


if __name__ == '__main__':
    unittest.main(verbosity=2)
