"""Independent byte/container/resource checks; deliberately does not import patch.py."""
import argparse
import hashlib
import struct
import sys
import unittest
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "menu_scroll"))
from verify_resources import archive, directory, payload, word

MEMBER = "Firmware-4.3.1.1"


def branch_target(image, address, condition):
    opcode = word(image, address)
    assert opcode >> 24 == (condition << 4 | 0xa)
    displacement = opcode & 0xffffff
    if displacement & 0x800000:
        displacement -= 0x1000000
    return address + 8 + displacement * 4


class Package(unittest.TestCase):
    def test_only_documented_navigation_and_size_bytes_change(self):
        self.assertEqual(hashlib.sha256(self.before).hexdigest(),
                         "e0b9634a6aff0b3cfb466ec1a07308fa8bfe724f172511864f0cf52cf8254466")
        self.assertEqual(len(self.after), len(self.before) + 64)
        restored = bytearray(self.after[:len(self.before) - 4])
        for offset, length in ((0xe8, 4), (0x2e8, 4), (0x4a8, 4), (0x4a350, 4), (0xc5640, 4), (0x1bb16a, 16)):
            restored[offset:offset + length] = self.before[offset:offset + length]
        self.assertEqual(restored, self.before[:-4])
        self.assertEqual(self.after[-4:], bytes.fromhex("55 aa 55 aa"))
        self.assertEqual(word(self.after, 0xe8), len(self.after))
        self.assertLessEqual(len(self.after), 0x390000)
        for offset in (0x2e8, 0x4a8):
            self.assertEqual(word(self.after, offset), 0x04000000 + len(self.after) - 4)

    def test_script_stays_inside_original_record_and_preserves_every_other_resource(self):
        before, after = directory(self.before), directory(self.after)
        self.assertEqual(before.keys(), after.keys())
        for tag in before:
            old, new = before[tag][1], after[tag][1]
            self.assertEqual(old.keys(), new.keys())
            for key in old:
                if (tag, key) == ("TCmd", 0x2380):
                    data = bytearray(new[key])
                    offset = 0x1bb16a - 0x1b56d0
                    self.assertEqual(data[offset:offset + 16], bytes.fromhex("3b074c393b073f3a0001024305000000"))
                    data[offset:offset + 16] = old[key][offset:offset + 16]
                    self.assertEqual(data, old[key])
                    # Native record's bytecode length still spans precisely 16 bytes.
                    self.assertEqual(struct.unpack_from("<H", self.after, 0x1bb168)[0], 16)
                else:
                    self.assertEqual(old[key], new[key], (tag, key))

    def test_compilation_hook_has_correct_native_branches_and_register_contract(self):
        start = len(self.before) - 4
        self.assertEqual(branch_target(self.after, 0x4a350, 14), start)
        self.assertEqual(branch_target(self.after, start + 8, 1), start + 28)
        self.assertEqual(branch_target(self.after, start + 24, 1), 0x4a45c)
        self.assertEqual(branch_target(self.after, start + 32, 14), 0x4a354)
        self.assertEqual([word(self.after, start + i) for i in (0, 4, 12, 16, 20, 28)],
                         [0xe59d0218, 0xe3500004, 0xe5960020, 0xe5d00011, 0xe3100002, 0xe28d2008])
        self.assertEqual(branch_target(self.after, 0xc5640, 0), start + 36)
        self.assertEqual(branch_target(self.after, start + 40, 1), 0xc567c)
        self.assertEqual(branch_target(self.after, start + 56, 0), 0xc567c)
        self.assertEqual(branch_target(self.after, start + 60, 14), 0xc5644)
        self.assertEqual([word(self.after, start + i) for i in (36, 44, 48, 52)],
                         [0xe3560000, 0xe595001c, 0xe5900a8c, 0xe3500000])

    def test_updater_padding_headers_and_other_archive_members_preserved(self):
        self.assertEqual(self.source.keys(), self.candidate.keys())
        for name in self.source:
            if name != MEMBER:
                self.assertEqual(self.source[name], self.candidate[name])
        old, new = self.source[MEMBER], self.candidate[MEMBER]
        self.assertEqual(payload(old, 0x4228), payload(new, 0x4228))
        self.assertEqual(word(new, 0x421c), sum(self.after) & 0xffffffff)
        header = bytearray(new[:0x4400])
        for offset in (0x4210, 0x421c, 0x4234):
            header[offset:offset + 4] = old[offset:offset + 4]
        self.assertEqual(header, old[:0x4400])
        # Reconstruct independently from stock, including opaque trailing data.
        stock = self.stock[MEMBER]
        self.assertEqual(hashlib.sha256(stock).hexdigest(),
                         "5b5ce4e560ce03859a2ec30ca7f36ada01dd68e15ce0ca62b3bf847158378d58")
        start = 512 + word(stock, 0x420c)
        original_size = word(stock, 0x4210)
        shift = (len(self.after) - original_size + 511) // 512 * 512
        expected = bytearray(stock[:start] + self.after + bytes(shift - len(self.after) + original_size)
                             + stock[start + original_size:])
        for offset, value in ((0x4210, len(self.after)), (0x421c, sum(self.after) & 0xffffffff),
                              (0x4234, word(stock, 0x4234) + shift)):
            struct.pack_into("<I", expected, offset, value)
        self.assertEqual(new, expected)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--stock", type=Path, default=Path(__file__).resolve().parent.parent / "iPod_4.3.1.1.ipsw")
    args = parser.parse_args()
    for path in (args.source, args.candidate, args.stock):
        with ZipFile(path) as zipfile:
            assert len(zipfile.namelist()) == len(set(zipfile.namelist())), "duplicate ZIP members"
    Package.source, Package.candidate, Package.stock = archive(args.source), archive(args.candidate), archive(args.stock)
    Package.before, Package.after = payload(Package.source[MEMBER], 0x4200), payload(Package.candidate[MEMBER], 0x4200)
    unittest.main(argv=[__file__], verbosity=2)
