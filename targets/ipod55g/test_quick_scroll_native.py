"""Exercise the shared 5.5G Quick Scroll entry threshold through native wheel events."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import test_disc_fast_scroll_native as disc
import test_disc_native as songs
from image import load_stock


class QuickScrollNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from build import compose
        stock = load_stock(songs.ROOT / 'iPod_25.1.3.ipsw')
        with TemporaryDirectory() as directory:
            cls.image, _, _ = compose(stock, Path(directory))

    def setUp(self):
        self.list_fixture = disc.DiscFastScrollNative()
        self.list_fixture.image = self.image

    def make_list(self, board, **options):
        m, listing = self.list_fixture.make_list(**options)
        # Use the stock board predicate and accelerator constructor. Supply
        # board identity before the first event creates the list accelerator.
        m.hooks.pop(0xa07a4)
        m.write(0x1081de40 + 0x28, board << 16)
        m.write(0x1081de40 + 0x40, -1)
        return m, listing

    def test_disc_entry_needs_thirteen_scaled_steps_in_either_direction(self):
        f = self.list_fixture
        for board in (4, 5):
            for raw_delta, selected, caption in (
                (51, 48, ''), (52, 43, '11'), (-51, 23, ''), (-52, 22, '10')
            ):
                with self.subTest(board=board, raw_delta=raw_delta):
                    m, listing = self.make_list(board)
                    f.select(m, listing, 35)
                    self.assertEqual(f.wheel(m, listing, raw_delta), 1)
                    self.assertEqual((f.selected(m, listing), f.popup(m)), (selected, caption))

    def test_contact_and_board_cadence_still_control_entry(self):
        f = self.list_fixture
        for board, limit in ((4, 10000), (5, 17000)):
            for flags, interval, selected, caption in (
                (disc.STROKE, limit - 1, 43, '11'),
                (disc.STROKE, limit, 49, ''),
                (0, 800, 49, ''),
            ):
                with self.subTest(board=board, flags=flags, interval=interval):
                    m, listing = self.make_list(board)
                    f.select(m, listing, 35)
                    self.assertEqual(f.wheel(m, listing, 52, flags=flags, velocity=interval), 1)
                    self.assertEqual((f.selected(m, listing), f.popup(m)), (selected, caption))

    def test_letter_entry_still_requires_crossing_the_initial_letter(self):
        f = self.list_fixture
        for direction, start, first, second, caption in (
            (1, 15, 28, 41, 'C'), (-1, 44, 31, 19, 'A')
        ):
            with self.subTest(direction=direction):
                m, listing = self.make_list(5, album='', screen=songs.ALL_SONGS)
                storage_copy = m.hooks[0x9eff8]

                def read_title():
                    if m.reg(0) != songs.DB + 0xb4:
                        return storage_copy()
                    title = ('Alpha', 'Beta', 'Charlie')[(m.reg(1) - 1) // 20].encode('utf-16le')
                    m.cpu.mem_write(m.reg(2), title)
                    m.write(m.reg(3), len(title))
                    return 0

                m.hook(0x9eff8, read_title)
                f.select(m, listing, start)
                for row, label in ((first, ''), (second, caption)):
                    self.assertEqual(f.wheel(m, listing, direction * 52), 1)
                    self.assertEqual((f.selected(m, listing), f.popup(m)), (row, label))


if __name__ == '__main__':
    unittest.main(verbosity=2)
