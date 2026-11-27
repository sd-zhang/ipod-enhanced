"""Verify the in-place Alarm Backlight caption against pinned r27, statically.

This deliberately imports neither the label builder nor its edit manifest.
The entire OSOS and firmware container are compared against a fixed contract.
"""
import argparse
import hashlib
import io
import struct
import sys
import unittest
from pathlib import Path
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'menu_scroll'))
from verify_resources import archive, payload, directory, word, ROOT

SOURCE = REPO / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r27.ipsw'
SOURCE_SHA = 'd398d8be76cc3386319d6898d796b7491b67b10c03b03ef67ae55b9f742d9452'
MEMBER = 'Firmware-4.3.1.1'
CAPTION_AT, ENTRY_AT, LENGTH_AT = 0x38ad10, 0x38bdd4, 0x38bddc
OLD_CAPTION, NEW_CAPTION = b'Backlight Alerts\0', b'Alarm Backlight\0'
ELFS = {'letters', 'actions', 'volume', 'hotpaths', 'otg', 'pomodoro',
        'playback', 'refinements', 'alarm_light'}
ARTIFACT_PATTERNS = ('*.elf', '*-symbols.json', '*-layout.json', '*-edits.json')


def carried_files(path):
    return {p.name: p.read_bytes() for pattern in ARTIFACT_PATTERNS
            for p in path.glob(pattern)}


def caption_entry(image):
    header, entries = directory(image)['Str ']
    table = ROOT + word(image, header + 12)
    found = []
    for index in range(len(entries)):
        at = table + 12 * index
        key, offset, length = struct.unpack_from('<III', image, at)
        if key == 0x6150:
            found.append((at, ROOT + word(image, ROOT + 4) + offset, length))
    return found


class LabelPackage(unittest.TestCase):
    def test_source_pin_and_caption_stays_at_its_existing_address(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), SOURCE_SHA)
        self.assertEqual(caption_entry(self.before), [(ENTRY_AT, CAPTION_AT, 17)])
        self.assertEqual(self.before[CAPTION_AT:CAPTION_AT + 17], OLD_CAPTION)
        self.assertEqual(caption_entry(self.after), [(ENTRY_AT, CAPTION_AT, 16)])
        self.assertEqual(self.after[CAPTION_AT:CAPTION_AT + 17], NEW_CAPTION + b'\0')

    def test_entire_osos_changes_only_caption_and_its_length_word(self):
        self.assertEqual(len(self.before), 0x38c810)
        self.assertEqual(len(self.after), len(self.before))
        expected = bytearray(self.before)
        expected[0x38ad10:0x38ad21] = b'Alarm Backlight\0\0'
        struct.pack_into('<I', expected, 0x38bddc, 16)
        self.assertTrue(self.after == expected,
                        'OSOS differs outside the exact caption/length replacement')

    def test_all_resource_payloads_and_directory_locations_are_preserved(self):
        expected = directory(self.before)
        expected['Str '][1][0x6150] = NEW_CAPTION
        self.assertEqual(directory(self.after), expected)
        for tag, (header, entries) in expected.items():
            table = ROOT + word(self.before, header + 12)
            count = len(entries) * 12
            old_table = bytearray(self.before[table:table + count])
            if tag == 'Str ':
                struct.pack_into('<I', old_table, LENGTH_AT - table, 16)
            self.assertTrue(self.after[table:table + count] == old_table, tag)
            self.assertEqual(self.after[header:header + 16], self.before[header:header + 16], tag)

    def test_all_elfs_symbols_and_layout_metadata_are_carried_verbatim(self):
        original = carried_files(SOURCE.with_suffix('.artifacts'))
        candidate = carried_files(self.artifacts)
        self.assertEqual(candidate.keys(), original.keys())
        self.assertEqual({Path(name).stem for name in candidate if name.endswith('.elf')}, ELFS)
        self.assertTrue({'pomodoro-layout.json', 'pomodoro-edits.json',
                         'alarm-light-edits.json'} <= candidate.keys())
        for name, raw in original.items():
            self.assertTrue(candidate[name] == raw, name)
            if not name.endswith('.elf'):
                continue
            elf = ELFFile(io.BytesIO(raw))
            self.assertEqual(elf['e_machine'], 'EM_ARM', name)
            for section in elf.iter_sections():
                if not section['sh_flags'] & 2 or section['sh_type'] == 'SHT_NOBITS':
                    continue
                at, size = section['sh_addr'], section['sh_size']
                for image in (self.before, self.after):
                    self.assertTrue(image[at:at + size] == section.data(),
                                    (name, section.name, hex(at)))

    def test_firmware_container_changes_only_osos_and_checksum(self):
        old, new = self.old[MEMBER], self.new[MEMBER]
        self.assertEqual(len(new), len(old))
        self.assertEqual(word(old, 0x420c), 0x4400)
        self.assertEqual(word(new, 0x420c), word(old, 0x420c))
        start = 512 + word(old, 0x420c)
        restored = bytearray(new)
        restored[start:start + len(self.before)] = self.before
        restored[0x421c:0x4220] = old[0x421c:0x4220]
        self.assertTrue(restored == old, 'unrelated firmware container byte changed')
        self.assertEqual(word(new, 0x421c), sum(self.after) & 0xffffffff)
        self.assertEqual(word(new, 0x4210), len(self.after))
        self.assertEqual(word(new, 0x4234), word(old, 0x4234))
        self.assertEqual(payload(new, 0x4228), payload(old, 0x4228))
        self.assertEqual(word(self.after, 0xe8), len(self.after))
        self.assertEqual(self.after[-4:], bytes.fromhex('55aa55aa'))
        self.assertLessEqual(len(self.after), 0x390000)
        for at in (0x2e8, 0x4a8):
            self.assertEqual(word(self.after, at), 0x04000000 + len(self.after) - 4)

    def test_zip_metadata_and_unrelated_members_are_preserved(self):
        self.assertEqual(self.old.keys(), self.new.keys())
        for name in self.old:
            if name != MEMBER:
                self.assertEqual(self.old[name], self.new[name], name)
        with ZipFile(SOURCE) as old_zip, ZipFile(self.candidate) as new_zip:
            self.assertEqual(old_zip.comment, new_zip.comment)
            self.assertEqual(old_zip.namelist(), new_zip.namelist())
            for old, new in zip(old_zip.infolist(), new_zip.infolist()):
                for field in ('filename', 'date_time', 'compress_type', 'comment', 'extra',
                              'create_system', 'create_version', 'extract_version',
                              'flag_bits', 'internal_attr', 'external_attr', 'volume', 'reserved'):
                    self.assertEqual(getattr(old, field), getattr(new, field),
                                     (old.filename, field))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    LabelPackage.candidate = args.candidate
    LabelPackage.artifacts = args.candidate.with_suffix('.artifacts')
    LabelPackage.old, LabelPackage.new = archive(SOURCE), archive(args.candidate)
    LabelPackage.before = payload(LabelPackage.old[MEMBER], 0x4200)
    LabelPackage.after = payload(LabelPackage.new[MEMBER], 0x4200)
    unittest.main(argv=[__file__], verbosity=2)
