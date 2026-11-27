"""Check native Albums indexing semantics and work against pinned r32.

Native index construction/deduplication and letter-cache storage execute ARM.
String storage, track lookup, heap and locks are supplied. Instruction/read
counts describe this fixture, not device timings or a physical disk.
"""
import argparse
from pathlib import Path
import unittest

from unicorn import UC_HOOK_CODE
from native_fixture import Library, QUERY, load_image

REFERENCE = Path('build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r32.ipsw')
OFFSET = {2: 0xa9c, 4: 0xa8c, 0x40: 0xa84, 0x4000000: 0xa94}


def build_index(image, names, mask=2, hints=None):
    m = Library(image, [(name, False) for name in names])
    for i, hint in enumerate(hints or []):
        m.cpu.mem_write(0x10040000 + i * 256 + 0xfd, bytes([hint]))
    count = [0]
    m.cpu.hook_add(UC_HOOK_CODE, lambda *args: count.__setitem__(0, count[0] + 1))
    m.instruction_limit = 1000000
    rows = m.build_index(mask)
    pointer, n = m.read(QUERY + OFFSET[mask]), m.read(QUERY + OFFSET[mask] + 4)
    groups = bytes(m.cpu.mem_read(pointer + n * 4, n)) if n else b''
    return m, rows, groups, count


class AlbumIndex(unittest.TestCase):
    def test_ascii_album_letters_keep_order_and_halve_added_index_work(self):
        names = [f'Album {i:03d}' for i in range(128)]
        old, rows, letters, old_count = build_index(self.reference, names)
        new, new_rows, new_letters, new_count = build_index(self.image, names)
        stock, stock_rows, _, stock_count = build_index(self.stock, names)
        self.assertEqual(new_rows, rows)
        self.assertEqual(new_rows, stock_rows)
        self.assertEqual(new_letters, b'A' * 128)
        self.assertEqual(letters, new_letters)
        self.assertEqual((new.lookups, new.storage_reads), (128, 128))
        self.assertLessEqual(new_count[0] - stock_count[0], (old_count[0] - stock_count[0]) // 2)
        print('128 unique ASCII albums, ARM instructions:',
              dict(stock=stock_count[0], r32=old_count[0], candidate=new_count[0]))

    def test_every_ascii_letter_case_and_duplicate_keeps_native_groups(self):
        names = [c + ' title' for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz']
        names += ['Alpha'] * 8
        _, rows, groups, _ = build_index(self.reference, names)
        _, actual_rows, actual_groups, _ = build_index(self.image, names)
        self.assertEqual((actual_rows, actual_groups), (rows, groups))
        self.assertEqual(actual_groups[:52], b'ABCDEFGHIJKLMNOPQRSTUVWXYZ' * 2)

    def test_host_hints_override_display_name_even_for_ascii(self):
        names = ['Alpha', 'beta', '#Goldrush', '(R)Evolution', '', '東京']
        hints = [ord(c) for c in 'ZYGR#!']
        _, rows, groups, _ = build_index(self.image, names, hints=hints)
        self.assertEqual(rows, [name for name in names if name])
        self.assertEqual(groups, b'ZYGR!')

    def test_prefix_unicode_empty_and_numeric_fallback_matches_r32(self):
        names = ['', ' ', '#Goldrush', '(R)Evolution', '  [Éclair]', '123',
                 'Été', '東京', '😀Alpha', '℃iel', '\u200bAlpha', 'İstanbul']
        _, rows, groups, _ = build_index(self.reference, names)
        _, actual_rows, actual_groups, _ = build_index(self.image, names)
        self.assertEqual((actual_rows, actual_groups), (rows, groups))

    def test_other_indexes_keep_native_results_with_bounded_dispatch_cost(self):
        names = [f'Name {i:03d}' for i in range(64)]
        for mask in (4, 0x40, 0x4000000):
            with self.subTest(mask=mask):
                _, rows, groups, before = build_index(self.reference, names, mask)
                _, actual_rows, actual_groups, after = build_index(self.image, names, mask)
                self.assertEqual((actual_rows, actual_groups), (rows, groups))
                self.assertLessEqual(after[0] - before[0], 2 * len(rows))

    def test_cached_album_query_does_no_new_name_reads_or_allocations(self):
        names = [f'Album {i // 8:03d}' for i in range(128)]
        m, rows, _, count = build_index(self.image, names)
        before = m.lookups, m.storage_reads, dict(m.allocations)
        count[0] = 0
        self.assertEqual(m.build_index(2), rows)
        self.assertEqual((m.lookups, m.storage_reads, m.allocations), before)
        self.assertEqual(count[0], 25)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    AlbumIndex.image = load_image(args.candidate)
    AlbumIndex.reference = load_image(REFERENCE)
    AlbumIndex.stock = load_image(Path('iPod_4.3.1.1.ipsw'))
    unittest.main(argv=[__file__], verbosity=2)
