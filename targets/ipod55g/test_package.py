"""Meaningful corruption tests of the independent package preservation audit."""
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from build import compose
from image import load_stock, repack, resources, word, ROOT
from verify_package import audit


class PackageAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.stock = load_stock(Path('iPod_25.1.3.ipsw'))
        image, cls.symbols, cls.elf = compose(cls.stock, Path(cls.tmp.name))
        cls.firmware = repack(cls.stock, image)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def audit(self, data): return audit(self.stock, data, self.elf, self.symbols)

    def mutate_osos(self, at):
        data = bytearray(self.firmware); data[0x5000+at] ^= 1
        size = word(data, 0x4210)
        struct.pack_into('<I', data, 0x421c, sum(data[0x5000:0x5000+size]) & 0xffffffff)
        return data

    def test_unmodified_candidate_passes(self):
        self.assertGreater(self.audit(self.firmware)['stock_scripts_preserved'], 1000)

    def test_clicker_cycle_output_and_refresh_corruption_is_rejected(self):
        for at in (0x192368, 0x192394, 0x192398, 0x1923a4, 0x1923a8,
                   0x193804, 0x19381c, 0x193e3c, 0x193e4c):
            with self.subTest(address=hex(at)), self.assertRaisesRegex(AssertionError, 'Clicker settings contract'):
                self.audit(self.mutate_osos(at))

    def test_clicker_installer_rejects_changed_input_before_any_edit(self):
        import clicker
        image = bytearray(self.stock.osos)
        image[0x193e4c] ^= 1  # Last contract: earlier sites must stay untouched.
        before = bytes(image)
        with self.assertRaisesRegex(ValueError, 'native Clicker instruction'):
            clicker.apply(image)
        self.assertEqual(bytes(image), before)

    def test_album_disc_hooks_are_in_the_complete_package(self):
        self.assertIn('discs_visible_count', self.symbols)
        self.assertIn('discs_header_draw', self.symbols)
        info = self.audit(self.firmware)['album_disc_headings']
        self.assertEqual(info['index_footer_bytes'], 1032)
        self.assertEqual(info['max_disc_runs'], 256)
        self.assertTrue(info['numeric_disc_quick_scroll'])
        self.assertEqual(info['quick_scroll_entry_steps'], 13)
        self.assertTrue(info['stock_cadence_and_acceleration_preserved'])

    def test_disc_native_branches_vtables_and_helpers_reject_corruption(self):
        for at, reason in ((0xcc810, 'disc branch'), (0xcc960, 'disc branch'),
                           (0x19eccc, 'disc branch'), (0x1e8508, 'disc branch'),
                           (0x23a164, 'disc branch'), (0x23a364, 'disc branch'),
                           (0x23a37c, 'disc branch'), (0x23a660, 'disc branch'),
                           (0x66b684, 'disc pointer'), (0x66fbe0, 'disc pointer'),
                           (0x6773dc, 'disc pointer'), (0x67741c, 'disc pointer'),
                           (0x239db8, 'disc branch'), (0x23a56c, 'disc branch'),
                           (0x23bab4, 'disc branch'), (0x6772c0, 'disc pointer'),
                           (0x677334, 'disc pointer'), (0x677438, 'disc pointer'),
                           (0x67744c, 'disc pointer'),
                           (self.symbols['discs_index_finish'], 'ELF bytes'),
                           (self.symbols['discs_header_draw'], 'ELF bytes'),
                           (self.symbols['discs_fast_publish'], 'ELF bytes')):
            with self.subTest(address=hex(at)), self.assertRaisesRegex(AssertionError, reason):
                self.audit(self.mutate_osos(at))

    def test_disc_entry_and_native_cadence_and_popup_contracts_reject_corruption(self):
        for at, reason in ((0x23a534, 'Quick Scroll entry comparison'),
                           (0x23a548, 'disc native continuation'),
                           (0x1c25d8, 'disc native contract'),
                           (0x239e60, 'disc native contract'),
                           (0x239da4, 'disc native continuation'),
                           (0x17f6a4, 'disc native contract'),
                           (0x23b718, 'disc native contract')):
            with self.subTest(address=hex(at)), self.assertRaisesRegex(AssertionError, reason):
                self.audit(self.mutate_osos(at))

    def test_unsorted_relocated_resource_directories_are_rejected(self):
        # Original ARM lookup 0x17ff94 binary-searches these record IDs. A
        # dictionary preserves every payload while hiding the broken order.
        for tag in (b'Menu', b'Str ', b'View'):
            data = bytearray(self.firmware)
            header = next(ROOT+12+16*i for i in range(8)
                          if data[0x5000+ROOT+12+16*i:0x5000+ROOT+16+16*i] == tag[::-1])
            count = word(data, 0x5000+header+4)
            start = 0x5000+ROOT+word(data, 0x5000+header+12)
            rows = [data[start+12*i:start+12*(i+1)] for i in range(count)]
            data[start:start+12*count] = b''.join(reversed(rows))
            size = word(data, 0x4210)
            struct.pack_into('<I', data, 0x421c, sum(data[0x5000:0x5000+size]) & 0xffffffff)
            with self.subTest(tag=tag), self.assertRaisesRegex(AssertionError, 'resource directory key order'):
                self.audit(data)

    def test_duplicate_relocated_resource_ids_are_rejected(self):
        data = bytearray(self.firmware)
        header = ROOT+12+16*3  # Pinned native Menu header.
        start = 0x5000+ROOT+word(data, 0x5000+header+12)
        data[start+12:start+16] = data[start:start+4]
        size = word(data, 0x4210)
        struct.pack_into('<I', data, 0x421c, sum(data[0x5000:0x5000+size]) & 0xffffffff)
        with self.assertRaises((AssertionError, ValueError)):
            self.audit(data)

    def test_relocated_resource_directory_bounds_are_checked(self):
        header = ROOT+12+16*3
        end = word(self.firmware, 0x5000+0xe8)
        relative = word(self.firmware, 0x5000+header+12)
        for field, value in ((4, 0), (4, 0xffffffff), (12, relative+1),
                             (12, end-4-ROOT), (12, end-8-ROOT)):
            data = bytearray(self.firmware)
            struct.pack_into('<I', data, 0x5000+header+field, value)
            size = word(data, 0x4210)
            struct.pack_into('<I', data, 0x421c, sum(data[0x5000:0x5000+size]) & 0xffffffff)
            with self.subTest(field=field, value=value), self.assertRaises((AssertionError, ValueError)):
                self.audit(data)

    def test_appended_resources_have_no_obsolete_payloads_or_directories(self):
        image = self.firmware[0x5000:0x5000+word(self.firmware, 0x4210)]
        start = self.symbols['__extension_end']
        spans = []
        for rows in resources(image).values():
            spans.extend((at, at+length) for at, length in rows.values() if at >= start)
        for index in range(8):
            header = ROOT+12+16*index
            at = ROOT+word(image, header+12)
            if at >= start: spans.append((at, at+12*word(image, header+4)))
        end = word(image, 0xe8)-4
        cursor = start
        for at, stop in sorted(spans)+[(end, end)]:
            self.assertGreaterEqual(at, cursor, 'overlapping appended resources')
            self.assertLess(at-cursor, 4, 'unreferenced resource output')
            self.assertFalse(any(image[cursor:at]), 'nonzero alignment padding')
            cursor = stop

    def test_unlisted_native_change_is_rejected_even_with_valid_checksum(self):
        with self.assertRaisesRegex(AssertionError, 'unexpected original'):
            self.audit(self.mutate_osos(0x28449c))  # watchdog excluded from alarm scope

    def test_wrong_hook_target_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'branch'):
            self.audit(self.mutate_osos(0x238070))

    def test_changed_extension_code_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'ELF bytes'):
            self.audit(self.mutate_osos(self.symbols['actions_album']))

    def test_playlist_branch_leaf_and_helper_corruption_are_rejected(self):
        for at, reason in ((0x1137c4, 'branch'), (0x1102f0, 'unexpected original'),
                           (self.symbols['ipod55g_playlist_invalidate'], 'ELF bytes')):
            with self.subTest(address=hex(at)), self.assertRaisesRegex(AssertionError, reason):
                self.audit(self.mutate_osos(at))

    def test_changed_stock_script_in_relocated_resource_is_rejected(self):
        image = self.firmware[0x5000:0x5000+word(self.firmware, 0x4210)]
        at, length = resources(image)['TCmd'][0x2380]
        # Last global declaration is outside every inserted command.
        with self.assertRaisesRegex(AssertionError, 'VM global footer'):
            self.audit(self.mutate_osos(at+length-1))

    def test_opaque_suffix_corruption_is_rejected(self):
        data = bytearray(self.firmware); data[-1] ^= 1
        with self.assertRaisesRegex(AssertionError, 'suffix preservation'): self.audit(data)


if __name__ == '__main__': unittest.main(verbosity=2)
