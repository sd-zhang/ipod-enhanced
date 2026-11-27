"""Independently verify the alarm-light layer against the pinned r26 archive.

No installer manifest or production builder is imported. The only permitted
prefix changes are five fixed hooks, four resource-directory words and three
image-length words. Executable correspondence, the exact added Settings row,
resource tables, archive preservation and memory bounds are checked statically.
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
from verify_resources import archive, payload, directory, records, word, ROOT

SOURCE = REPO / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r26.ipsw'
SOURCE_SHA = '1e113058612a0b66dfc32ce436ea5388d725e0db0b757083b969ae0bb717246a'
MEMBER = 'Firmware-4.3.1.1'
CARRIED = ('letters', 'actions', 'volume', 'hotpaths', 'otg', 'pomodoro',
           'playback', 'refinements')
HOOKS = ((0x159a0c, 0x1bfd003a, 'alarm_wake', 0x1b),
         (0x159a84, 0x1bfd001c, 'alarm_wake', 0x1b),
         (0x73e20, 0xe3a00000, 'alarm_get_hook', 0xea),
         (0xccddc, 0xe3a08000, 'alarm_set_hook', 0xea),
         (0xbeb08, 0xe5900058, 'alarm_home_reset', 0xea))
HOOK_SITES = {row[0] for row in HOOKS}
RESOURCE_HEADER_SITES = {0x1a50ac, 0x1a50b4, 0x1a50bc, 0x1a50c4}
LENGTH_SITES = {0xe8, 0x2e8, 0x4a8}
ALERT_ROW = bytes.fromhex(
    '2400000026000000921d0000ae210000000000005061000050610000802700005061000000000000')
CAPTION = b'Backlight Alerts\0'
RENAMED_CAPTION = b'Alarm Backlight\0'


def alert_caption(image):
    """Accept only the two reviewed captions in the original 20-byte allocation."""
    header, entries = directory(image)['Str ']
    caption = entries[0x6150]
    assert caption in (CAPTION, RENAMED_CAPTION), 'unexpected alarm caption'
    assert ROOT + word(image, header + 12) == 0x38ad24, 'caption table moved'
    assert struct.unpack_from('<III', image, 0x38bdd4) == (0x6150, 0x1e27b0, len(caption)), \
        'caption directory entry moved'
    assert ROOT + word(image, ROOT + 4) + 0x1e27b0 == 0x38ad10, 'caption payload moved'
    assert image[0x38ad10:0x38ad24] == caption.ljust(20, b'\0'), 'caption allocation changed'
    return caption


def expected_alert_menu(original):
    rows = records(original)
    assert len(rows) == 16 and word(rows[5], 24) == 0x27a3
    # Fixed native immediate-setting row: Str/prID 6150, localized On/Off,
    # stock cycle command, inserted immediately after Backlight Timer.
    return struct.pack('<I', 17) + b''.join(rows[:6]) + ALERT_ROW + b''.join(rows[6:])


def expected_alert_resources(original, caption=CAPTION):
    assert caption in (CAPTION, RENAMED_CAPTION)
    groups = {tag: (header, dict(entries))
              for tag, (header, entries) in directory(original).items()}
    assert all(0x6150 not in entries for _, entries in groups.values())
    groups['Menu'][1][0x278d] = expected_alert_menu(groups['Menu'][1][0x278d])
    groups['Str '][1][0x6150] = caption
    return groups


def raw_entries(image, tag):
    header, entries = directory(image)[tag]
    table = ROOT + word(image, header + 12)
    return {key: (offset, size) for key, offset, size in struct.iter_unpack(
        '<III', image[table:table + 12 * len(entries)])}


class AlertPackage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layer = cls.after
        cls.layer_playback = (cls.artifacts / 'playback.elf').read_bytes()
        cls.layer_pomodoro = (cls.artifacts / 'pomodoro.elf').read_bytes()
        cls.has_pattern = (cls.artifacts / 'alarm_pattern.elf').exists()
        cls.has_lcd = (cls.artifacts / 'lcd.elf').exists()
        cls.has_wheel = (cls.artifacts / 'wheel_recovery.elf').exists()
        cls.has_timer = (cls.artifacts / 'timer_recovery.elf').exists()
        cls.has_performance = (cls.artifacts / 'stock_performance.elf').exists()
        cls.has_disc = (cls.artifacts / 'disc_separators.elf').exists()
        if cls.has_wheel:
            assert cls.has_lcd, 'wheel overlay requires validated r32 LCD layer'
        if cls.has_lcd:
            assert cls.has_pattern, 'LCD overlay requires validated r31 alarm layer'
        if cls.has_pattern:
            from verify_pattern_package import validated_base as validate_pattern
            cls.layer, cls.layer_pomodoro = validate_pattern(cls.candidate)
        cls.has_alac = (cls.artifacts / 'alac.elf').exists()
        if cls.has_alac:
            sys.path.insert(0, str(REPO / 'gapless'))
            from verify_alac_package import validated_base
            # The complete overlay must pass its independent contract before
            # any legacy check is allowed to inspect the reconstructed base.
            cls.layer, cls.layer_playback = validated_base(cls.candidate)

    def test_pinned_source_and_only_fixed_prefix_words_change(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), SOURCE_SHA)
        allowed = HOOK_SITES | RESOURCE_HEADER_SITES | LENGTH_SITES
        self.assertEqual({directory(self.before)[tag][0] + offset
                          for tag in ('Menu', 'Str ') for offset in (4, 12)},
                         RESOURCE_HEADER_SITES)
        restored = bytearray(self.layer[:len(self.before) - 4])
        for at in allowed:
            restored[at:at + 4] = self.before[at:at + 4]
        self.assertEqual(restored, self.before[:-4])
        symbols = {s.name: s['st_value']
                   for s in self.elf.get_section_by_name('.symtab').iter_symbols()}
        code = self.elf.get_section_by_name('.text')
        for at, original, name, opcode in HOOKS:
            self.assertEqual(word(self.before, at), original)
            instruction = word(self.after, at)
            self.assertEqual(instruction >> 24, opcode, (hex(at), name))
            delta = instruction & 0xffffff
            if delta & 0x800000:
                delta -= 0x1000000
            target = at + 8 + delta * 4
            self.assertEqual(target, symbols[name])
            self.assertEqual(target % 4, 0)
            self.assertGreaterEqual(target, code['sh_addr'])
            self.assertLess(target, code['sh_addr'] + code['sh_size'])

    def test_resource_payloads_change_only_for_exact_setting_row_and_caption(self):
        self.assertEqual(directory(self.after),
                         expected_alert_resources(self.before, alert_caption(self.after)))

    def test_exact_append_is_508_bytes_of_code_then_resources_and_terminator(self):
        code = self.elf.get_section_by_name('.text')
        start = len(self.before) - 4
        self.assertEqual(self.elf['e_machine'], 'EM_ARM')
        self.assertEqual((code['sh_addr'], code['sh_size']), (start, 508))
        allocated = [s for s in self.elf.iter_sections()
                     if s['sh_flags'] & 2 and s['sh_size']]
        self.assertEqual([s.name for s in allocated], ['.text'])
        self.assertFalse(any(s['sh_size'] for s in self.elf.iter_sections()
                             if s.name in ('.data', '.bss')))
        self.assertEqual(self.after[start:start + 508], code.data())

        # Construct an independent, exact append contract. Every pre-existing
        # table entry retains its original offset/size, except Settings 278d.
        expected = bytearray(code.data())
        base = ROOT + word(self.before, ROOT + 4)
        headers = directory(self.before)
        for tag, key, data in (
                ('Menu', 0x278d, expected_alert_menu(headers['Menu'][1][0x278d])),
                ('Str ', 0x6150, alert_caption(self.after))):
            expected.extend(bytes(-(start + len(expected)) % 4))
            entries = raw_entries(self.before, tag)
            entries[key] = (start + len(expected) - base, len(data))
            expected.extend(data)
            if tag == 'Str ':
                # The renamed caption is one byte shorter. Keep r27's entire
                # allocation so neither the table nor any resource relocates.
                expected.extend(bytes(20 - len(data)))
            expected.extend(bytes(-(start + len(expected)) % 4))
            table = start + len(expected)
            for resource, (offset, size) in sorted(entries.items()):
                expected.extend(struct.pack('<III', resource, offset, size))
            header = headers[tag][0]
            self.assertEqual(word(self.after, header + 4), len(entries))
            self.assertEqual(ROOT + word(self.after, header + 12), table)
        expected.extend(bytes.fromhex('55aa55aa'))
        self.assertEqual(self.layer[start:], expected)

    def test_eight_carried_elfs_are_exact_and_code_resources_do_not_overlap(self):
        self.assertEqual({p.stem for p in self.artifacts.glob('*.elf')},
                         set(CARRIED) | {'alarm_light'} | ({'alac'} if self.has_alac else set()) |
                         ({'alarm_pattern'} if self.has_pattern else set()) |
                         ({'lcd'} if self.has_lcd else set()) |
                         ({'wheel_recovery'} if self.has_wheel else set()) |
                         ({'timer_recovery'} if self.has_timer else set()) |
                         ({'stock_performance'} if self.has_performance else set()) |
                         ({'disc_separators'} if self.has_disc else set()))
        ranges = []
        for name in (*CARRIED, 'alarm_light', *(('alac',) if self.has_alac else ()),
                     *(('alarm_pattern',) if self.has_pattern else ()),
                     *(('lcd',) if self.has_lcd else ()),
                     *(('wheel_recovery',) if self.has_wheel else ()),
                     *(('timer_recovery',) if self.has_timer else ()),
                     *(('stock_performance',) if self.has_performance else ()),
                     *(('disc_separators',) if self.has_disc else ())):
            path = self.artifacts / (name + '.elf')
            raw = path.read_bytes()
            elf = ELFFile(io.BytesIO(raw))
            layer_raw = (self.layer_playback if name == 'playback' else
                         self.layer_pomodoro if name == 'pomodoro' else raw)
            layer_elf = ELFFile(io.BytesIO(layer_raw))
            if name in CARRIED:
                old = (SOURCE.with_suffix('.artifacts') / path.name).read_bytes()
                self.assertEqual(layer_raw, old, name)
            for section in elf.iter_sections():
                if not section['sh_flags'] & 2 or not section['sh_size']:
                    continue
                if section['sh_type'] == 'SHT_NOBITS':
                    continue
                at, count = section['sh_addr'], section['sh_size']
                self.assertGreaterEqual(at, 0)
                self.assertLessEqual(at + count, len(self.after) - 4)
                # validate_base has checked every newer hook and byte before
                # reconstructing this layer; letters' carried ELF is frozen.
                image = self.layer if name == 'letters' else self.after
                self.assertEqual(image[at:at + count], section.data(), name)
                if name in CARRIED:
                    self.assertEqual(self.before[at:at + count],
                                     layer_elf.get_section_by_name(section.name).data(), name)
                ranges.append((at, at + count, name + ':' + section.name))
        for left, right in zip(sorted(ranges), sorted(ranges)[1:]):
            self.assertLessEqual(left[1], right[0], (left, right))

        base = ROOT + word(self.after, ROOT + 4)
        for tag, (header, entries) in directory(self.after).items():
            table = ROOT + word(self.after, header + 12)
            table_end = table + 12 * len(entries)
            self.assertEqual(table % 4, 0, tag)
            self.assertLessEqual(table_end, len(self.after) - 4)
            locations = [(table, table_end, tag + ' table')]
            locations += [(base + offset, base + offset + size, (tag, key))
                          for key, (offset, size) in raw_entries(self.after, tag).items()
                          if size]
            for start, end, label in locations:
                self.assertLessEqual(end, len(self.after) - 4, label)
                for code_start, code_end, name in ranges:
                    self.assertTrue(end <= code_start or start >= code_end,
                                    (label, name, hex(start), hex(end)))
        for at in (0x4b0, 0x20428):
            self.assertEqual(word(self.layer, at), word(self.before, at))

    def test_archive_metadata_updater_padding_checksum_and_image_bounds(self):
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
                              'flag_bits', 'internal_attr', 'external_attr'):
                    self.assertEqual(getattr(old, field), getattr(new, field), field)
        old, new = self.old[MEMBER], self.new[MEMBER]
        image_start = 512 + word(old, 0x420c)
        restored = bytearray(new[:image_start])
        for at in (0x4210, 0x421c, 0x4234):
            restored[at:at + 4] = old[at:at + 4]
        self.assertEqual(restored, old[:image_start])
        old_updater, new_updater = 512 + word(old, 0x4234), 512 + word(new, 0x4234)
        self.assertEqual(new_updater % 512, 0)
        self.assertGreaterEqual(new_updater, image_start + len(self.after))
        self.assertEqual(len(new) - len(old), new_updater - old_updater)
        self.assertEqual(old[old_updater:], new[new_updater:])
        self.assertEqual(payload(old, 0x4228), payload(new, 0x4228))
        old_padding = old[image_start + len(self.before):old_updater]
        padding = new[image_start + len(self.after):new_updater]
        # This interval includes a stock opaque trailer, not only zeros.
        # Repacking can consume leading alignment zeros, but preserves every
        # byte of the original opaque suffix immediately before the updater.
        consumed = len(old_padding) - len(padding)
        self.assertGreaterEqual(consumed, 0)
        self.assertEqual(old_padding[:consumed], bytes(consumed))
        self.assertEqual(padding, old_padding[consumed:])
        self.assertEqual(sum(self.after) & 0xffffffff, word(new, 0x421c))
        self.assertEqual(word(new, 0x4210), len(self.after))
        self.assertLessEqual(len(self.after), 0x390000)
        self.assertEqual(len(self.after) % 4, 0)
        self.assertEqual(word(self.after, 0xe8), len(self.after))
        self.assertEqual(self.after[-4:], bytes.fromhex('55aa55aa'))
        for at in (0x2e8, 0x4a8):
            self.assertEqual(word(self.after, at), 0x04000000 + len(self.after) - 4)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    AlertPackage.candidate = args.candidate
    AlertPackage.artifacts = args.candidate.with_suffix('.artifacts')
    AlertPackage.old, AlertPackage.new = archive(SOURCE), archive(args.candidate)
    AlertPackage.before = payload(AlertPackage.old[MEMBER], 0x4200)
    AlertPackage.after = payload(AlertPackage.new[MEMBER], 0x4200)
    AlertPackage.elf = ELFFile(io.BytesIO((AlertPackage.artifacts / 'alarm_light.elf').read_bytes()))
    unittest.main(argv=[__file__], verbosity=2)
