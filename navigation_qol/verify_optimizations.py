"""Independent preservation and memory-layout checks for the r25 refinements."""
import argparse
import hashlib
import io
import struct
import sys
import unittest
from pathlib import Path

from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'menu_scroll'))
from verify_resources import archive, payload, directory, word, ROOT as RESOURCE_ROOT
sys.path.insert(0, str(ROOT / 'clock_pomodoro'))
from verify_alert_package import HOOK_SITES, RESOURCE_HEADER_SITES, expected_alert_resources, alert_caption

SOURCE = ROOT / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r25.ipsw'


class Refinements(unittest.TestCase):
    def test_only_declared_helper_bytes_and_length_headers_change(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                         'ed00b7b0dea5cc36d3fdb9f285addc901d85bb16c156918f076d0984f94d0c65')
        expected = bytearray(self.layer_image[:len(self.before) - 4])
        for at, count in ((0xe8, 4), (0x2e8, 4), (0x4a8, 4), (0x35ba78, 4),
                          (0x35c304, 4), (0x359d90, 64), (0x35944c, 4)):
            expected[at:at + count] = self.before[at:at + count]
        if (self.artifacts / 'alarm_light.elf').exists():
            for at in HOOK_SITES | RESOURCE_HEADER_SITES:
                expected[at:at + 4] = self.before[at:at + 4]
        self.assertEqual(expected, self.before[:-4])
        self.assertEqual(self.after[-4:], bytes.fromhex('55aa55aa'))

    def test_resources_and_allocation_boundaries_are_unchanged(self):
        expected = (expected_alert_resources(self.before, alert_caption(self.after))
                    if (self.artifacts / 'alarm_light.elf').exists()
                    else directory(self.before))
        self.assertEqual(directory(self.after), expected)
        for at in (0x4b0, 0x20428):
            self.assertEqual(word(self.layer_image, at), word(self.before, at))
        playback = ELFFile(io.BytesIO((self.artifacts / 'playback.elf').read_bytes()))
        pomo = ELFFile(io.BytesIO((self.artifacts / 'pomodoro.elf').read_bytes()))
        first, second = playback.get_section_by_name('.bss'), pomo.get_section_by_name('.bss')
        self.assertEqual(first['sh_addr'] + first['sh_size'], second['sh_addr'])
        self.assertEqual(second['sh_addr'] + second['sh_size'], word(self.layer_image, 0x4b0))
        self.assertLessEqual(len(self.after), 0x390000)

    def test_every_review_elf_matches_shipped_code_without_overlap(self):
        ranges = []
        for path in self.artifacts.glob('*.elf'):
            elf = ELFFile(io.BytesIO(path.read_bytes()))
            section = elf.get_section_by_name('.text')
            at, count = section['sh_addr'], section['sh_size']
            # The later, independently validated title-sort hook redirects
            # one letters call; compare its frozen ELF to the checked layer.
            image = self.layer_image if path.stem == 'letters' else self.after
            self.assertEqual(image[at:at + count], section.data(), path.name)
            ranges.append((at, at + count, path.name))
            self.assertFalse(any(s['sh_size'] for s in elf.iter_sections() if s.name == '.data'))
        for left, right in zip(sorted(ranges), sorted(ranges)[1:]):
            self.assertLessEqual(left[1], right[0], (left, right))
        # No live resource payload may land in executable extension storage.
        base = RESOURCE_ROOT + word(self.after, RESOURCE_ROOT + 4)
        for tag, (header, _) in directory(self.after).items():
            table = RESOURCE_ROOT + word(self.after, header + 12)
            for i in range(word(self.after, header + 4)):
                key, offset, size = struct.unpack_from('<3I', self.after, table + 12 * i)
                if not size: continue
                for start, end, name in ranges:
                    self.assertTrue(base + offset + size <= start or base + offset >= end,
                                    (tag, key, name, hex(base + offset)))
        extension = ELFFile(io.BytesIO((self.artifacts / 'refinements.elf').read_bytes()))
        code = extension.get_section_by_name('.text')
        self.assertEqual(code['sh_addr'], len(self.before) - 4)
        alarm_path = self.artifacts / 'alarm_light.elf'
        end = len(self.after) - 4
        if alarm_path.exists():
            alarm = ELFFile(io.BytesIO(alarm_path.read_bytes())).get_section_by_name('.text')
            end = alarm['sh_addr']
        self.assertEqual(code['sh_addr'] + code['sh_size'], end)
        self.assertFalse(any(s['sh_size'] for s in extension.iter_sections() if s.name == '.bss'))
        symbols = {s.name: s['st_value'] for s in extension.get_section_by_name('.symtab').iter_symbols()}
        for name, at in (('letters_index_compact', 0x35ba78), ('letters_draw', 0x35c304),
                         ('gapless_publish_overflow_guard', 0x35944c)):
            instruction = word(self.layer_image, at)
            self.assertEqual(instruction >> 24, 0xea)
            delta = (instruction & 0xffffff) << 2
            if delta & 0x2000000: delta -= 0x4000000
            self.assertEqual(at + 8 + delta, symbols[name])

    def test_pcm_elf_changes_only_at_copy_helper_and_publisher_entry(self):
        old = (ROOT / 'build/gapless-complete-drain/playback.elf').read_bytes()
        new = self.layer_playback
        section = ELFFile(io.BytesIO(old)).get_section_by_name('.text')
        at = section['sh_offset'] + 0x359d90 - section['sh_addr']
        expected = bytearray(old)
        expected[at:at + 64] = new[at:at + 64]
        at = section['sh_offset'] + 0x35944c - section['sh_addr']
        expected[at:at + 4] = new[at:at + 4]
        self.assertEqual(new, expected)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    Refinements.artifacts = args.candidate.with_suffix('.artifacts')
    Refinements.before = payload(archive(SOURCE)['Firmware-4.3.1.1'], 0x4200)
    Refinements.after = payload(archive(args.candidate)['Firmware-4.3.1.1'], 0x4200)
    Refinements.layer_image = Refinements.after
    Refinements.layer_playback = (Refinements.artifacts / 'playback.elf').read_bytes()
    if (Refinements.artifacts / 'alac.elf').exists():
        sys.path.insert(0, str(ROOT / 'gapless'))
        from verify_alac_package import validated_base
        Refinements.layer_image, Refinements.layer_playback = validated_base(args.candidate)
    unittest.main(argv=[__file__], verbosity=2)
