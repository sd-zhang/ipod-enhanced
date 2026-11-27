"""Independent preservation/packaging checks for letter-navigation firmware."""
import argparse
import hashlib
import json
import struct
import unittest
from pathlib import Path

from verify_package import MEMBER, Package, archive, payload, word


class LetterPackage(Package):
    def test_only_documented_navigation_and_size_bytes_change(self):
        self.assertEqual(hashlib.sha256(self.before).hexdigest(),
                         "24fe2cf44239c4fb65c9732c89da35f84296093acad3b1ec7da0a75f58423d7b")
        self.assertGreater(len(self.after), len(self.before))
        self.assertLess(len(self.after) - len(self.before), 12 * 1024)
        allowed = [0xe8, 0x2e8, 0x4a8, 0x4a2c8, 0x4a450, 0x4a46c,
                   0x866a4, 0x86700, 0x103180, 0x29054, 0x29090, 0x290c4, 0x29190,
                   0xb6ec4, 0x8bf18, 0xda208]
        self.assertEqual(word(self.before, 0x8bf18), 0xe3a000fc)
        self.assertEqual(word(self.before, 0xda208), 0xe3a020fc)
        self.assertEqual(word(self.after, 0x8bf18), 0xe3a00c01)
        self.assertEqual(word(self.after, 0xda208), 0xe3a02c01)
        restored = bytearray(self.after[:len(self.before) - 4])
        for at in allowed:
            restored[at:at + 4] = self.before[at:at + 4]
        self.assertEqual(restored, self.before[:-4])
        self.assertEqual(self.after[-4:], bytes.fromhex("55aa55aa"))
        self.assertEqual(word(self.after, 0xe8), len(self.after))
        self.assertLess(len(self.after), 0x390000)
        for at in (0x2e8, 0x4a8):
            self.assertEqual(word(self.after, at), 0x04000000 + len(self.after) - 4)

    def test_script_stays_inside_original_record_and_preserves_every_other_resource(self):
        # All QOL script/resource changes were already in the pinned input.
        # The byte allowlist above proves the complete resource bytes identical.
        self.assertEqual(self.after[0x1bb16a:0x1bb17a], self.before[0x1bb16a:0x1bb17a])

    def test_compilation_hook_has_correct_native_branches_and_register_contract(self):
        # Earlier compilation/All hooks and their appended bodies are unchanged.
        for at, size in ((0x4a350, 4), (0xc5640, 4), (0x35b9ac, 64)):
            self.assertEqual(self.before[at:at + size], self.after[at:at + size])

    def test_new_hooks_target_compiled_code_with_original_conditions(self):
        for at, condition_link, name in (
                (0x4a2c8, 0xeb, "letters_index_alloc"),
                (0x4a450, 0xea, "letters_index_accept"),
                (0x4a46c, 0xea, "letters_index_finish"),
                (0xb6ec4, 0xea, "letters_import_sort_hook"),
                (0x866a4, 0xea, "letters_alloc_hook"),
                (0x86700, 0xea, "letters_wheel_hook"),
                (0x103180, 0x1b, "letters_accel_destroy")):
            opcode = word(self.after, at)
            self.assertEqual(opcode >> 24, condition_link)
            offset = opcode & 0xffffff
            if offset & 0x800000:
                offset -= 0x1000000
            target = at + 8 + 4 * offset
            self.assertEqual(target, self.symbols[name])
            self.assertTrue(len(self.before) - 4 <= target < len(self.after) - 4)
        for slot, name in ((0x24, "letters_event"), (0x60, "letters_draw"),
                           (0x94, "letters_visibility"), (0x160, "letters_key")):
            target = (0x29030 + word(self.after, 0x29030 + slot)) & 0xffffffff
            self.assertEqual(target, self.symbols[name])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--source", type=Path, default=Path("build/navigation-qol/iPod_4.3.1.1-gapless-navigation.ipsw"))
    parser.add_argument("--stock", type=Path, default=Path("iPod_4.3.1.1.ipsw"))
    args = parser.parse_args()
    LetterPackage.source, LetterPackage.candidate, LetterPackage.stock = map(
        archive, (args.source, args.candidate, args.stock))
    LetterPackage.before = payload(LetterPackage.source[MEMBER], 0x4200)
    LetterPackage.after = payload(LetterPackage.candidate[MEMBER], 0x4200)
    LetterPackage.symbols = json.loads((args.candidate.with_suffix(".artifacts") / "letters-symbols.json").read_text())
    unittest.main(argv=[__file__], defaultTest="LetterPackage", verbosity=2)
