"""Exercise ordinary playback entry through native queue and history code.

Runs Songs Center (2076 -> 2012 -> 2011) and Albums Play/Pause (2095 -> 2011),
including native library filtering, queue traversal, VM dispatch, history push,
key snapshot and Now Playing activation2015. Storage create/append/delete,
audio status/start and metadata notifications, selected-row lookup, rendering
and widget restoration are supplied boundaries. Audio assertions establish
dispatch and its selected track, not actual playback. Run repository Python
outside the macOS sandbox because Unicorn needs executable JIT memory.
"""
import argparse
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

from unicorn import UC_HOOK_CODE

import verify_actions
from native_fixture import APP, DB, Library, PLAYLIST, QUERY, ScriptVM, load_image
from verify_actions_widgets import ActionsDisplayFixture
from verify_resources import directory

UI = 0x10014000
NEW_QUEUE, QUEUE_HEADER = 0x1001a000, 0x1001b000
KEYS = (0x10, 0x57, 0x58, 0x59, 0x5a, 0x6e, 0x6f)


class PlaybackVM(Library, ScriptVM):
    """Combine the real VM with Library's ordered disk-storage boundary."""

    def copy_string(self):
        assert self.reg(0) - DB in (0xb0, 0xdc, 0x134, 0x18c)
        _, data, _ = self.strings[self.reg(1)]
        self.cpu.mem_write(self.reg(2), struct.pack('<H', len(data) // 2) + data)
        return 0


def playback_machine(image, activation):
    # Substitute only the fixture constructor. Unlike native_key_machine, do
    # not enter Now Playing during setup: its first activation must occur in
    # the actual playback transition under test.
    factory = lambda firmware: PlaybackVM(
        firmware, [('A', False), ('B', False), ('C', False)])
    with patch.object(verify_actions, 'ScriptVM', factory):
        m = verify_actions.menu_machine(image)
    m.instruction_limit = 1000000
    m.hook(0xd97b8, m.allocate)
    m.hook(0xdda9c, lambda: ActionsDisplayFixture.reallocate(m))
    m.write(0x1041a200, APP)
    m.call(0xeb410, APP + 0x28)
    comparer = m.call(0xf6140, 0)
    m.call(0xc9cd0, APP + 0x28, comparer)
    m.call(0xc9698, APP + 0x28, 1)
    array = m.call(0xd9a60, len(KEYS) * 20)
    m.write(APP + 0x54, array, len(KEYS) * 20)
    for i, key in enumerate(KEYS):
        entry = array + i * 20
        m.write(entry, key, 0, 0, 0, 0)
        m.write(0x10010400, entry)
        m.call(0x30d3c, APP + 0x28, 0x10010400)
    for address in (0x1b964, 0x1b538, 0xc3590):
        del m.hooks[address]
    # cb0a8 is also native here; it writes the OS's per-key long-press flag.
    m.binding = lambda kind, key: m.read(
        m.read(APP + 0x54) + KEYS.index(key) * 20 + 4 + 4 * kind)

    m.write(UI, 0x26294)
    m.write(UI + 0x1c, QUERY)
    m.write(QUERY + 0x44, PLAYLIST)
    m.write(QUERY + 0xa7c, 0x1000f000, 1)
    m.write(0x1000f000, PLAYLIST)
    m.source_node = m.read(APP + 0x104)
    m.write(m.source_node + 8, activation * 4 + 2)
    # Both stock browse constructors install suspension1950, with no leave
    # callback. Their activation and the subsequent history push remain real.
    m.write(m.source_node + 0x10, 0, 1950 * 4 + 2)
    m.queue, m.audio_requests, m.playback_entries = [], [], []
    m.write(NEW_QUEUE + 0x24, QUEUE_HEADER)

    def lookup():
        assert m.reg(0) in (PLAYLIST, NEW_QUEUE)
        tracks = m.wrappers if m.reg(0) == PLAYLIST else m.queue
        return tracks[m.reg(3)]

    def create_queue():
        m.write(m.reg(1), NEW_QUEUE)
        return 0

    def append_track():
        assert m.reg(1) == NEW_QUEUE
        assert m.reg(0) in m.wrappers
        m.queue.append(m.reg(0))
        m.cpu.mem_write(QUEUE_HEADER + 0x2e, struct.pack('<H', len(m.queue)))
        return 0

    def start_audio():
        assert m.reg(1) in m.queue
        m.audio_requests.append(m.reg(1))
        return 0

    def trace(cpu, address, size, context):
        if address == 0xc6b54:
            assert m.reg(0) == UI
            m.playback_entries.append((m.reg(1), m.reg(2)))

    m.hook(0x6e400, lookup)
    m.hook(0x9d92c, create_queue)
    m.hook(0x47ea4, append_track)
    m.hook(0x537bc, lambda: 0)  # disk queue deletion
    m.hook(0xcbb54, lambda: 0)  # Shuffle is disabled
    m.hook(0xa91b0, lambda: 0)  # audio preliminary validation
    m.hook(0xa8be8, start_audio)
    m.hook(0xc7d44, lambda: 0)  # UI notification
    m.hook(0xe0de4, lambda: 0)  # metadata refresh after audio dispatch
    m.hook(m.method_address(3, 0xaa), lambda: 1)  # highlighted row
    m.hook(m.method_address(26, 0x10c), lambda: 0)  # transport not started
    m.cpu.hook_add(UC_HOOK_CODE, trace)
    return m


class NativePlaybackEntry(unittest.TestCase):
    def test_shortcuts_preserve_playing_queue_and_replace_old_browse_history(self):
        for command in (2538, 2539):
            with self.subTest(command=command):
                m = playback_machine(self.image, 2075)
                m.hook(m.method_address(3, 0xaa), lambda: 0)
                m.call(0xe2d40, UI)
                m.run(2075)
                m.run(2076, m.event)
                for offset, text in ((0x28, 'A song'), (0x428, 'A'), (0x628, 'A')):
                    verify_actions.pascal(m, UI + offset, text)
                playing = bytes(m.cpu.mem_read(QUERY + 0x44, 0x14))
                queue, audio = list(m.queue), list(m.audio_requests)
                old_browse = m.source_node
                m.run(2536, m.event)
                m.run(1888, m.event)
                self.assertEqual(m.run(command, m.event), 0xc0000002)
                self.assertIn(old_browse, m.freed)
                self.assertEqual(bytes(m.cpu.mem_read(QUERY + 0x44, 0x14)), playing)
                self.assertEqual(m.queue, queue)
                self.assertEqual(m.audio_requests, audio)
                self.assertEqual([m.call(0x4e028, QUERY, w) for w in m.wrappers], [1, 0, 0])
                for expected in (2537, 2015, 1882):
                    m.run(m.binding(3, 0x6e) >> 2, m.event)
                    self.assertEqual(m.read(m.read(APP + 0x104) + 8) >> 2, expected)
                self.assertEqual(m.audio_requests, audio)
                self.assertTrue(all(status == 1 for status in m.callback_status))

    def check_now_playing(self, m, expected_tracks, selected, playback_row):
        self.assertEqual([m.wrappers.index(w) for w in m.queue], expected_tracks)
        self.assertEqual(m.audio_requests, [m.wrappers[selected]])
        self.assertEqual(m.playback_entries, [(playback_row & 0xffffffff, 0)])
        node = m.read(APP + 0x104)
        self.assertEqual(m.read(node + 8), 2015 * 4 + 2)
        self.assertEqual(m.read(node + 4), m.source_node)
        self.assertIn(2015 * 4 + 2, m.commands)
        self.assertTrue(m.callback_status)
        self.assertTrue(all(status == 1 for status in m.callback_status))
        self.assertEqual(m.binding(3, 0x6f), 2029 * 4 + 2)
        has_actions = 0x6100 in directory(self.image)['View'][1]
        self.assertEqual(m.binding(2, 0x6f), 2536 * 4 + 2 if has_actions else 0)

    def test_song_center_builds_queue_then_activates_now_playing(self):
        m = playback_machine(self.image, 2075)
        m.call(0xe2d40, UI)
        self.assertEqual(m.read(UI + 0x1500), 3)
        self.assertEqual(m.run(2075), 0xc0000002)
        self.assertEqual(m.run(2076, m.event), 0xc0000002)
        self.check_now_playing(m, [0, 1, 2], 1, 1)

    def test_album_play_builds_filtered_queue_then_activates_now_playing(self):
        m = playback_machine(self.image, 2092)
        # Album A owns the first two tracks; C owns the third. Row zero is All.
        m.write(m.read(m.wrappers[1] + 0x20) + 0x30, 1)
        m.call(0xe033c, UI)
        self.assertEqual(m.read(UI + 0x1504), 3)
        self.assertEqual(m.run(2092), 0xc0000002)
        self.assertEqual(m.binding(3, 0x10), 2095 * 4 + 2)
        self.assertEqual(m.run(m.binding(3, 0x10) >> 2, m.event), 0xc0000002)
        self.check_now_playing(m, [0, 1], 0, -1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    NativePlaybackEntry.image = load_image(args.candidate)
    unittest.main(argv=[__file__], verbosity=2)
