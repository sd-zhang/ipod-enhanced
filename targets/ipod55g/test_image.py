"""5.5G packaging invariants; these tests require no JIT or device."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from zipfile import ZipFile

import image

ROOT = Path(__file__).resolve().parents[2]


class StockPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock = image.load_stock(ROOT / 'iPod_25.1.3.ipsw')

    def test_stock_identity_and_unchanged_roundtrip(self):
        self.assertEqual(hashlib.sha256(self.stock.osos).hexdigest(), image.OSOS_SHA256)
        self.assertEqual(image.repack(self.stock, self.stock.osos), self.stock.firmware)

    def test_rejects_other_model(self):
        with self.assertRaisesRegex(ValueError, 'stock 5.5G'):
            image.load_stock(ROOT / 'iPod_4.3.1.1.ipsw')

    def test_rejects_corrupted_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'damaged.ipsw'
            b = bytearray((ROOT / 'iPod_25.1.3.ipsw').read_bytes())
            b[-1] ^= 1
            p.write_bytes(b)
            with self.assertRaisesRegex(ValueError, 'stock 5.5G'):
                image.load_stock(p)

    def test_extension_preserves_every_byte_outside_osos_and_directory(self):
        changed = self.stock.osos + b'\x10\x20\x30\x40'
        packed = image.repack(self.stock, changed)
        shift = 0x800
        prefix = bytearray(packed[:0x5000])
        for at in (0x4210, 0x421c, 0x4234, 0x425c):
            prefix[at:at+4] = self.stock.firmware[at:at+4]
        self.assertEqual(prefix, self.stock.firmware[:0x5000])
        self.assertEqual(packed[0x73b000+shift:], self.stock.firmware[0x73b000:])
        self.assertEqual(packed[0x5000:0x5000+len(changed)], changed)
        self.assertEqual(struct.unpack_from('<I', packed, 0x4210)[0], len(self.stock.osos)+shift)
        self.assertEqual(struct.unpack_from('<I', packed, 0x421c)[0], sum(changed)&0xffffffff)
        self.assertEqual(packed[0x5000+len(changed):0x73b000+shift], bytes(shift-4))

    def test_rejects_short_or_unaligned_image(self):
        for changed in (self.stock.osos[:-4], self.stock.osos+b'x'):
            with self.subTest(size=len(changed)), self.assertRaises(ValueError):
                image.repack(self.stock, changed)

    def test_archive_preserves_manifest_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)/'candidate.ipsw'
            image.write_archive(self.stock, self.stock.firmware, dest)
            with ZipFile(dest) as z:
                self.assertEqual(z.read('manifest.plist'), self.stock.manifest)
                self.assertEqual(z.read(image.MEMBER), self.stock.firmware)
                self.assertIsNone(z.testzip())
            before = dest.read_bytes()
            with self.assertRaises(FileExistsError):
                image.write_archive(self.stock, self.stock.firmware, dest)
            self.assertEqual(dest.read_bytes(), before)

    def test_resource_and_script_records(self):
        groups = image.resources(self.stock.osos)
        self.assertEqual(len(groups['Menu']), 40)
        self.assertEqual(groups['TCmd'][0x2380], (0x4b441c, 91662))
        funcs = image.script_functions(self.stock.osos)
        self.assertEqual(len(funcs), 1064)
        self.assertEqual(struct.unpack_from('<H', funcs[4326])[0], 4326)
        self.assertEqual(struct.unpack_from('<H', funcs[4326],8)[0], 28)

    def test_exact_edits_reject_stale_bytes_and_overlaps(self):
        edit = image.Edit(0x100, self.stock.osos[0x100:0x104], b'TEST')
        result = image.apply_edits(self.stock.osos, [edit])
        self.assertEqual(result[0x100:0x104], b'TEST')
        with self.assertRaisesRegex(ValueError, 'expected'):
            image.apply_edits(result, [edit])
        with self.assertRaisesRegex(ValueError, 'overlap'):
            image.apply_edits(self.stock.osos, [edit, edit])


if __name__ == '__main__':
    unittest.main()
