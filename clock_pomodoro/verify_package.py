"""Independent archive, live-resource, playback and allocation preservation checks."""
import argparse
import io
import json
import struct
import sys
import unittest
from pathlib import Path

from elftools.elf.elffile import ELFFile
from verify_runtime import BASELINE, BSS
from verify_resources import archive, payload, directory, word
from actions_patch import functions, call, integer, method
from verify_alert_package import HOOK_SITES, alert_caption, expected_alert_menu


class Package(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.preservation_image = cls.after
        if (cls.artifacts / 'alac.elf').exists():
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'gapless'))
            from verify_alac_package import validated_base
            # The ALAC validator checks every byte before these older tests
            # inspect the unchanged Pomodoro/alarm layer beneath it.
            cls.preservation_image, _ = validated_base(cls.candidate)

    def test_all_original_bytes_are_preserved_outside_declared_hooks_and_command_resource(self):
        edits = json.loads((self.artifacts / 'pomodoro-edits.json').read_text())
        # Independently enumerate allowed entry points/headers. Do not accept
        # whatever addresses the installer happens to report as legitimate.
        groups = directory(self.before)
        allowed = {0xe8, 0x2e8, 0x4a8, 0x4b0, 0x20428, 0x2e894, 0x8cf28,
                   0x2e9d4, 0x3cb00, 0x8cf80, 0xbe650, 0xcd298, 0x159adc,
                   0x8cf34, 0x159ad0, 0xcd118, 0xe21b8, 0x91418,
                   0x256c0, 0x25694, 0x3802b4, 0x37fbd0,
                   0x6b1d8, 0x73348, 0xcc1c4, 0x733d0,
                   0x6b224, 0x6b228, 0x6b22c, 0xcc228, 0xcc22c, 0xcc230}
        allowed |= {groups[tag][0] + offset for tag in ('View', 'Menu', 'Str ') for offset in (4, 12)}
        self.assertEqual(set(edits), allowed)
        if (self.artifacts / 'refinements.elf').exists():
            allowed |= {0x35ba78, 0x35c304, 0x35944c} | set(range(0x359d90, 0x359dd0, 4))
        if (self.artifacts / 'alarm_light.elf').exists():
            allowed |= HOOK_SITES
        restored = bytearray(self.preservation_image[:len(self.before) - 4])
        for at in allowed:
            restored[at:at + 4] = self.before[at:at + 4]
        start, end = self.layout['script_range']
        self.assertEqual(start, 0x3714e4)
        self.assertLessEqual(end, 0x37fbc8)
        restored[start:end] = self.before[start:end]
        self.assertEqual(restored, self.before[:-4])

    def test_all_unrelated_resource_payloads_and_existing_commands_are_unchanged(self):
        old, new = directory(self.before), directory(self.after)
        changed = {('View', 0x250d), ('View', 0x4582), ('Menu', 0x4581), ('TCmd', 0x2380),
                   ('Menu', 0x2512), ('Menu', 0x278f)}
        if (self.artifacts / 'alarm_light.elf').exists():
            self.assertEqual(new['Menu'][1][0x278d],
                             expected_alert_menu(old['Menu'][1][0x278d]))
            self.assertNotIn(0x6150, old['Str '][1])
            self.assertEqual(new['Str '][1][0x6150], alert_caption(self.after))
            changed.add(('Menu', 0x278d))
        for tag, (_, entries) in old.items():
            for key, data in entries.items():
                if (tag, key) not in changed:
                    self.assertEqual(new[tag][1][key], data, (tag, key))
        old_functions = functions(old['TCmd'][1][0x2380])[4]
        new_functions = functions(new['TCmd'][1][0x2380])[4]
        for old, new in zip(old_functions, new_functions):
            if old[0] == 1879:
                anchor = call(1878, integer(0x25f8))
                self.assertEqual(old[4].count(anchor), 1)
                self.assertEqual(new[:4], (*old[:3], 6))
                extra = b''.join(call(1877, integer(caption),
                                      method(0x153, integer(key), owner=0x9ba, keep=True))
                                 for key, caption in ((0x2607, -169), (0x2608, -170),
                                                      (0x2609, -178)))
                extra += call(1878, integer(0x25f4))
                expected = bytearray(old[4].replace(anchor, anchor + extra))
                self.assertEqual(old[4][14:17], b'\x07\x00\xe4')
                struct.pack_into('>H', expected, 15, 228 + len(extra))
                self.assertEqual(new[4], expected + bytes(len(expected) % 2))
            else:
                self.assertEqual(new, old)
        self.assertEqual(len(new_functions) - len(old_functions), 5)

    def test_owned_bss_extends_zero_fill_and_heap_equally(self):
        count = self.layout['bss_bytes']
        self.assertGreaterEqual(count, 120 + 32 + 40)
        self.assertEqual(count % 4, 0)
        self.assertEqual(word(self.preservation_image, 0x4b0), BSS + count)
        self.assertEqual(word(self.preservation_image, 0x20428), word(self.before, 0x20428) + count)
        elf = ELFFile(io.BytesIO((self.artifacts / 'pomodoro.elf').read_bytes()))
        state = elf.get_section_by_name('.bss')
        self.assertEqual((state['sh_addr'], state['sh_size'], state['sh_type']),
                         (BSS, count, 'SHT_NOBITS'))
        self.assertFalse(any(s['sh_size'] for s in elf.iter_sections() if s.name == '.data'))

    def test_review_elfs_match_candidate_and_only_one_old_helper_instruction_changes(self):
        for name in ('letters', 'actions', 'volume', 'hotpaths', 'otg', 'pomodoro'):
            raw = (self.artifacts / (name + '.elf')).read_bytes()
            section = ELFFile(io.BytesIO(raw)).get_section_by_name('.text')
            start, count = section['sh_addr'], section['sh_size']
            # The independently validated disc layer may redirect the sort
            # importer call in letters. Its carried ELF stays immutable.
            image = self.preservation_image if name == 'letters' else self.after
            self.assertEqual(image[start:start + count], section.data(), name)
            if name != 'pomodoro':
                old_raw = (BASELINE.with_suffix('.artifacts') / (name + '.elf')).read_bytes()
                if name == 'hotpaths':
                    expected = bytearray(old_raw)
                    at = section['sh_offset'] + 0x3802b4 - start
                    expected[at:at + 4] = self.after[0x3802b4:0x3802b8]
                    self.assertEqual(raw, expected)
                elif name == 'letters' and (self.artifacts / 'refinements.elf').exists():
                    expected = bytearray(old_raw)
                    for site in (0x35ba78, 0x35c304):
                        at = section['sh_offset'] + site - start
                        expected[at:at + 4] = self.after[site:site + 4]
                    self.assertEqual(raw, expected)
                else:
                    self.assertEqual(raw, old_raw)

    def test_archive_metadata_updater_and_image_bounds_remain_valid(self):
        self.assertEqual(self.old.keys(), self.new.keys())
        for name in self.old:
            if name != 'Firmware-4.3.1.1': self.assertEqual(self.old[name], self.new[name])
        old, new = self.old['Firmware-4.3.1.1'], self.new['Firmware-4.3.1.1']
        self.assertEqual(payload(old, 0x4228), payload(new, 0x4228))
        self.assertEqual(sum(self.after) & 0xffffffff, word(new, 0x421c))
        self.assertLessEqual(len(self.after), 0x390000)
        self.assertEqual(word(self.after, 0xe8), len(self.after))
        self.assertEqual(self.after[-4:], bytes.fromhex('55aa55aa'))
        for at in (0x2e8, 0x4a8):
            self.assertEqual(word(self.after, at), 0x04000000 + len(self.after) - 4)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    Package.candidate = args.candidate
    Package.artifacts = args.candidate.with_suffix('.artifacts')
    Package.layout = json.loads((Package.artifacts / 'pomodoro-layout.json').read_text())
    Package.old, Package.new = archive(BASELINE), archive(args.candidate)
    Package.before = payload(Package.old['Firmware-4.3.1.1'], 0x4200)
    Package.after = payload(Package.new['Firmware-4.3.1.1'], 0x4200)
    unittest.main(argv=[__file__], verbosity=2)
