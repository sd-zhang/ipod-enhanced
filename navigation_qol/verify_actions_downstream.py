"""Read-only downstream review checks; run repository Python with JIT access.

Usage: .venv/bin/python navigation_qol/verify_actions_downstream.py candidate.ipsw
No source or firmware files are changed. Native UI property getters, album
selection, query building/predicates, playback queue traversal and row selection
execute unchanged. Disk create/append/delete, audio start, notifications and
shuffle flag update (shuffle is disabled) are supplied service boundaries.
"""
import argparse
import struct
import unittest
from pathlib import Path

from verify_actions import library, method, UI, pascal
from native_fixture import QUERY, OUT, DB, PLAYLIST, load_image


def fixture(image):
    # iTunes album order: X/X/X/Y. Artist A owns first, second and last songs;
    # artist B owns the third. First two songs are compilation tracks.
    machine = library(image, 'A', [('A', True), ('X', True), ('B', False), ('Y', False)])
    for index, (artist, album) in enumerate(((1, 2), (1, 2), (3, 2), (1, 4))):
        track = machine.read(machine.wrappers[index] + 0x20)
        machine.write(track + 0x24, index + 1)
        machine.write(track + 0x28, artist)
        machine.write(track + 0x30, album)

    def copy_string():
        assert machine.reg(0) - DB in (0xb0, 0xdc, 0x134, 0x18c)
        _, data, _ = machine.strings[machine.reg(1)]
        machine.cpu.mem_write(machine.reg(2), struct.pack('<H', len(data) // 2) + data)
        return 0

    machine.hook(0x76c90, copy_string)
    pascal(machine, UI + 0x628, 'X')
    machine.write(UI, OUT)
    machine.write(OUT + 0x48, 0x10018000 - OUT)
    machine.write(OUT + 0x68, 0x6f290 - OUT)
    machine.events = []
    machine.hook(0x10018000, lambda: machine.events.append(
        tuple(machine.reg(i) for i in (1, 2))))
    machine.hook(0x6e400, lambda: machine.wrappers[machine.reg(3)])
    return machine


def check_counts_and_reset(image):
    cases = ((0xea, None, [0, 1, 2]), (0xe8, None, None),
             (0xe8, 0, [0, 1, 3]), (0xe8, 1, [0, 1]), (0xe8, 2, [3]))
    for selector, album_row, expected in cases:
        machine = fixture(image)
        assert machine.call(method(image, selector), UI, 0xfffffffd) == 1
        if selector == 0xe8:
            machine.call(0xe033c, UI)
            assert machine.read(UI + 0x1504) == 3  # All + X + Y
            assert machine.call(0x7492c, UI, 0x564d6178, 0x3302, OUT + 0x100) == 1
            assert machine.read(OUT + 0x100) == 3
            if album_row is not None:
                assert machine.call(method(image, 0xea), UI, album_row) == 1
        if expected is not None:
            machine.call(0xe2d40, UI)
            assert machine.read(UI + 0x1500) == len(expected)
            assert machine.call(0x7492c, UI, 0x564d6178, 0x3301, OUT + 0x100) == 1
            assert machine.read(OUT + 0x100) == len(expected)
            cache = machine.read(QUERY + 0xaac)
            actual = [machine.wrappers.index(machine.read(cache + i * 4))
                      for i in range(len(expected))]
            assert actual == expected, (actual, expected)
            assert machine.call(0x92ce8, UI, len(expected) - 1) == 1
            assert machine.call(0x92ce8, UI, len(expected)) == 0
        # The same original reset is called by Music activation2072 and
        # main-menu Albums2089/Artists2105 before ordinary browsing.
        machine.call(0xc6198, UI)
        assert [machine.call(0x4e028, QUERY, w) for w in machine.wrappers] == [1] * 4
        machine.call(0xe033c, UI)
        assert machine.read(UI + 0x1504) == 3
    return len(cases)


def check_playback(image):
    checks = 0
    cases = ((0xea, None, [0, 1, 2]), (0xe8, 0, [0, 1, 3]),
             (0xe8, 1, [0, 1]), (0xe8, 2, [3]))
    for selector, album_row, expected in cases:
        for song_row in [-1] + list(range(len(expected))):
            machine = fixture(image)
            queue, audio = [], []
            new_queue, header = 0x1001a000, 0x1001b000
            machine.write(new_queue + 0x24, header)

            def lookup():
                assert machine.reg(0) in (PLAYLIST, new_queue)
                tracks = machine.wrappers if machine.reg(0) == PLAYLIST else queue
                return tracks[machine.reg(3)]

            machine.hook(0x6e400, lookup)
            assert machine.call(method(image, selector), UI, 0xfffffffd) == 1
            if album_row is not None:
                machine.call(0xe033c, UI)
                assert machine.call(method(image, 0xea), UI, album_row) == 1
            machine.call(0xe2d40, UI)
            # Full UI indexed property getter, then original conversion to text.
            for row, expected_id in enumerate(expected):
                assert machine.call(0x6f290, UI, 0x53747220, 0x3367,
                                    row, OUT + 0x100) == 1
                at = machine.read(OUT + 0x100)
                title = bytes(machine.cpu.mem_read(at, 12)).split(b'\0')[0].decode()
                assert title == 'AXBY'[expected_id], (title, expected_id)

            def create_queue():
                machine.write(machine.reg(1), new_queue)
                return 0

            def append_track():
                assert machine.reg(1) == new_queue
                queue.append(machine.reg(0))
                machine.cpu.mem_write(header + 0x2e, struct.pack('<H', len(queue)))
                return 0

            machine.hook(0x9d92c, create_queue)
            machine.hook(0x47ea4, append_track)
            machine.hook(0x537bc, lambda: 0)  # disk queue deletion
            machine.hook(0xcbb54, lambda: 0)  # shuffle disabled
            machine.hook(0xa91b0, lambda: 0)  # audio preliminary validation
            machine.hook(0xa8be8, lambda: audio.append(machine.reg(1)) or 0)
            machine.hook(0xc7d44, lambda: 0)  # UI notification
            machine.hook(0xe0de4, lambda: 0)  # metadata refresh after audio start
            assert machine.call(0xc6b54, UI, song_row & 0xffffffff, 0) == 1
            assert [machine.wrappers.index(w) for w in queue] == expected
            assert audio == [machine.wrappers[expected[max(song_row, 0)]]]
            checks += 1
    return checks


class DownstreamActions(unittest.TestCase):
    def test_native_counts_row_bounds_properties_and_reset(self):
        self.assertEqual(check_counts_and_reset(self.image), 5)

    def test_native_playback_queue_and_selected_track(self):
        self.assertEqual(check_playback(self.image), 13)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    DownstreamActions.image = load_image(args.candidate)
    unittest.main(argv=[__file__], verbosity=2)
