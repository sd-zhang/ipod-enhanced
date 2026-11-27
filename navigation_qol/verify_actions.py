"""Execute the packaged Now Playing shortcuts and native library predicates.

Use .venv/bin/python outside the macOS sandbox (Unicorn needs executable JIT).
"""
import argparse
import struct
import unittest
from pathlib import Path

from native_fixture import Library, QUERY, PLAYLIST, ScriptVM, APP, load_image

UI = 0x10011000


def method(image, selector):
    table = 0x1e10ec + 34 * 26
    half = lambda at: struct.unpack_from('<H', image, table + at)[0]
    slot = 0x1dfe48 + 4 * (half(28) - half(30) + selector - half(18))
    return struct.unpack_from('<I', image, slot)[0]


def pascal(machine, at, name):
    data = name.encode('utf-16le')
    machine.cpu.mem_write(at, struct.pack('<H', len(data) // 2) + data)


def library(image, name='A', tracks=None):
    m = Library(image, tracks or [('A', True), ('B', False)])
    m.write(UI + 0x1c, QUERY)
    m.write(QUERY + 0x44, PLAYLIST)
    m.write(QUERY + 0xa7c, 0x1000f000, 1)
    m.write(0x1000f000, PLAYLIST)
    pascal(m, UI + 0x28, 'A song')
    pascal(m, UI + 0x428, name)
    pascal(m, UI + 0x628, name)
    m.hook(0xe24d0, lambda: 0)  # smart-playlist refresh / disk service
    return m


class BrowseActions(unittest.TestCase):
    def test_preflight_is_read_only_and_accepts_compilation_only_artist(self):
        for selector in (0xe8, 0xea):
            m = library(self.image)
            before = bytes(m.cpu.mem_read(QUERY, 0xb00)), bytes(m.cpu.mem_read(UI, 0x1a00))
            self.assertEqual(m.call(method(self.image, selector), UI, 0xfffffffe), 1)
            self.assertEqual((bytes(m.cpu.mem_read(QUERY, 0xb00)), bytes(m.cpu.mem_read(UI, 0x1a00))), before)
            self.assertEqual(m.lookups, 0)

    def test_artist_shortcut_includes_compilation_tracks(self):
        m = library(self.image)
        self.assertEqual(m.call(method(self.image, 0xe8), UI, 0xfffffffd), 1)
        self.assertEqual([m.call(0x4e028, QUERY, w) for w in m.wrappers], [1, 0])
        self.assertEqual(m.build_index(2), ['A'])

    def test_album_shortcut_ignores_previous_artist_genre_and_playlist_filters(self):
        m = library(self.image)
        for offset in (0x70, 0x270, 0x470, 0x870):
            pascal(m, QUERY + offset, 'B')
        self.assertEqual(m.call(method(self.image, 0xea), UI, 0xfffffffd), 1)
        self.assertEqual([m.call(0x4e028, QUERY, w) for w in m.wrappers], [1, 0])
        self.assertEqual(m.read(QUERY + 0x40), 0)

    def test_empty_or_bad_metadata_does_not_change_query(self):
        for selector, offset in ((0xe8, 0x428), (0xea, 0x628)):
            for length in (0, 256, 65535):
                for operation in (0xfffffffe, 0xfffffffd):
                    m = library(self.image)
                    m.cpu.mem_write(UI + offset, struct.pack('<H', length))
                    before = bytes(m.cpu.mem_read(QUERY, 0xb00))
                    self.assertEqual(m.call(method(self.image, selector), UI, operation), 0)
                    self.assertEqual(bytes(m.cpu.mem_read(QUERY, 0xb00)), before)

    def test_no_playing_queue_or_library_does_not_navigate(self):
        for offset in (0x44, 0xa7c, 0xa80):
            m = library(self.image)
            m.write(QUERY + offset, 0)
            self.assertEqual(m.call(method(self.image, 0xe8), UI, 0xfffffffe), 0)

    def test_long_unicode_name_is_not_truncated(self):
        name = '東京' * 127 + 'é'
        m = library(self.image, name, [(name, True), ('Other', False)])
        self.assertEqual(m.call(method(self.image, 0xe8), UI, 0xfffffffd), 1)
        self.assertEqual([m.call(0x4e028, QUERY, w) for w in m.wrappers], [1, 0])

    def test_existing_cache_is_freed_and_playback_fields_preserved(self):
        m = library(self.image)
        m.build_index(2)
        cached = m.read(QUERY + 0xa9c)
        playback = bytes(m.cpu.mem_read(QUERY + 0x44, 0x14))
        self.assertEqual(m.call(method(self.image, 0xea), UI, 0xfffffffd), 1)
        self.assertIn(cached, m.freed)
        self.assertEqual(bytes(m.cpu.mem_read(QUERY + 0x44, 0x14)), playback)

    def test_existing_row_selection_still_calls_original_selector(self):
        m = library(self.image)
        self.assertEqual(m.call(method(self.image, 0xe8), UI, 1), 1)
        self.assertEqual([m.call(0x4e028, QUERY, w) for w in m.wrappers], [0, 1])

    def test_album_songs_use_native_album_order_even_after_podcast_browsing(self):
        m = library(self.image)
        m.write(QUERY + 0xa74, 6)
        m.cpu.mem_write(QUERY + 0xa78, b'\x01')
        requests = []
        def ordered_track():
            requests.append((m.reg(1), m.reg(2)))
            return m.wrappers[m.reg(3)]
        m.hook(0x6e400, ordered_track)  # ordered database access; builder is native
        self.assertEqual(m.call(method(self.image, 0xea), UI, 0xfffffffd), 1)
        self.assertEqual(m.call(0x7190c, QUERY), 1)
        self.assertEqual(requests, [(6, 0), (6, 0)])

    def test_compilation_album_includes_different_artists(self):
        m = library(self.image, 'A', [('A', True), ('B', True)])
        # Native artist ID is track+28; album ID is track+30. Both songs share
        # album A but have different artists. Start with an old artist filter.
        m.write(m.read(m.wrappers[1] + 0x20) + 0x30, 1)
        pascal(m, QUERY + 0x270, 'A')
        self.assertEqual(m.call(method(self.image, 0xea), UI, 0xfffffffd), 1)
        self.assertEqual([m.call(0x4e028, QUERY, w) for w in m.wrappers], [1, 1])

    def test_repeated_shortcuts_rebuild_cache_for_the_new_track(self):
        m = library(self.image)
        previous = None
        for i in range(8):
            name = 'AB'[i % 2]
            pascal(m, UI + 0x428, name)
            self.assertEqual(m.call(method(self.image, 0xe8), UI, 0xfffffffd), 1)
            if previous is not None:
                self.assertEqual(m.freed.count(previous), 1)
            self.assertEqual(m.build_index(2), [name])
            previous = m.read(QUERY + 0xa9c)


def menu_machine(image):
    m = ScriptVM(image)
    # One shortcut executes several nested VM commands, a full native history
    # unwind, fresh Now Playing/actions parents, and the destination. The r6
    # path executes ~546k instructions with these supplied service boundaries.
    m.instruction_limit = 1000000
    m.history([(1882, 0), (2015, 2020)])
    m.write(m.read(APP + 0x104) + 0x14, 2019 * 4 + 2)
    m.real_leave_callbacks()
    m.supply_services(((2, (0x60, 0x61, 0x64, 0x65, 0x66, 0x6d)),
                       (3, (0x8a, 0x8b, 0x91, 0x99)),
                       (25, (0xda, 0xdb)), (26, (0x10f, 0x139))))
    m.hook(0xc3590, lambda: 0)  # native key-table snapshot supplied by UI boundary
    m.bindings = {}
    def register():
        m.bindings[m.reg(1), m.reg(2)] = m.reg(3)
        return 0
    def unregister():
        m.bindings.pop((m.reg(1), m.reg(2)), None)
        return 0
    m.hook(0x1b964, register)
    m.hook(0x1b538, unregister)
    m.write(0x10014000 + 0x1c, QUERY)
    return m


def native_key_machine(image):
    """Run native key registration, snapshot and restore; only allocation and
    the OS long-press configuration remain supplied. Widgets still use the
    menu_machine graphics boundary.
    """
    m = menu_machine(image)
    m.hook(0xd97b8, m.allocate)
    def realloc():
        old, size = m.reg(0), m.reg(1)
        at = m.cursor
        m.cursor += (size + 15) & ~15
        assert m.cursor < 0x100d0000
        m.allocations[at] = size
        if old:
            m.cpu.mem_write(at, bytes(m.cpu.mem_read(old, min(size, m.allocations[old]))))
            m.freed.append(old)
        return at
    m.hook(0xdda9c, realloc)
    m.hook(0xcb0a8, lambda: 0)  # OS: configure which keys report a long press
    m.write(0x1041a200, APP)
    m.call(0xeb410, APP + 0x28)
    comparer = m.call(0xf6140, 0)
    m.call(0xc9cd0, APP + 0x28, comparer)
    m.call(0xc9698, APP + 0x28, 1)
    keys = [0x10, 0x57, 0x58, 0x59, 0x5a, 0x6e, 0x6f]
    array = m.call(0xd9a60, len(keys) * 20)
    m.write(APP + 0x54, array, len(keys) * 20)
    for i, key in enumerate(keys):
        at = array + i * 20
        m.write(at, key, 0, 0, 0, 0)
        m.write(0x10010400, at)
        m.call(0x30d3c, APP + 0x28, 0x10010400)
    for address in (0x1b964, 0x1b538, 0xc3590):
        del m.hooks[address]
    m.binding = lambda kind, key: m.read(m.read(APP + 0x54) + keys.index(key) * 20 + 4 + 4 * kind)
    m.run(2010)
    m.run(1923, m.event)
    m.run(2015, m.event)
    for key in (0x57, 0x58):
        m.call(0x1b964, APP, 0, key, 2025 * 4 + 2)
    return m


class ActionMenus(unittest.TestCase):
    def test_first_song_selection_enters_now_playing_with_native_keys(self):
        m = native_key_machine(self.image)
        m.instruction_limit = 1000000
        m.call(0x95374)  # original startup initializers
        node = m.read(APP + 0x104)
        m.write(node + 8, 2075 * 4 + 2)
        m.write(node + 0x10, 0, 0)
        # Supply the selected row and successful audio-start boundary. The
        # script chain 2076 -> 2012 -> 2011, history and key tables are native.
        m.hook(m.method_address(3, 0xaa), lambda: 0)
        m.hook(m.method_address(26, 0xee), lambda: 1)
        self.assertEqual(m.run(2075), 0xc0000002)
        self.assertEqual(m.run(2076, m.event), 0xc0000002)
        self.assertEqual(m.read(m.read(APP + 0x104) + 8), 2015 * 4 + 2)
        self.assertEqual(m.binding(2, 0x6f), 2536 * 4 + 2)

    def test_hold_installed_on_activation_and_removed_on_suspend(self):
        m = menu_machine(self.image)
        self.assertEqual(m.run(2015, m.event), 0xc0000002)
        self.assertEqual(m.bindings[2, 0x6f], 2536 * 4 + 2)
        self.assertEqual(m.run(2019), 0xc0000002)
        self.assertNotIn((2, 0x6f), m.bindings)

    def test_open_uses_native_seek_cleanup_and_correct_center_action(self):
        m = menu_machine(self.image)
        ui = 0x10014000
        m.call(0x60c70, ui)
        m.cpu.mem_write(ui + 0x190a, b'\x01\x01')
        m.write(ui + 0x190c, 12345, 100)
        m.hook(0x9bc7c, lambda: 1000)
        audio = []
        m.hook(0xca4f8, lambda: audio.append(('seek', m.reg(1))))
        m.hook(0x4d4bc, lambda: audio.append(('command', m.reg(0))))
        self.assertEqual(m.run(2536, m.event), 0xc0000002)
        self.assertEqual(audio, [('seek', 12345), ('command', 10)])
        node = m.read(APP + 0x104)
        self.assertEqual(m.read(node + 8), 2537 * 4 + 2)
        self.assertEqual(m.bindings[3, 0x6f], 1888 * 4 + 2)
        self.assertEqual(m.bindings[3, 0x6e], 1860 * 4 + 2)
        self.assertNotIn((2, 0x6f), m.bindings)
        self.assertEqual(m.run(1860, m.event), 0xc0000002)
        self.assertEqual(m.read(m.read(APP + 0x104) + 8), 2015 * 4 + 2)
        self.assertEqual(m.bindings[2, 0x6f], 2536 * 4 + 2)
        self.assertTrue(all(status == 1 for status in m.callback_status))

    def test_held_center_release_does_not_activate_first_action(self):
        m = menu_machine(self.image)
        selected = []
        m.hook(m.method_address(3, 0x9c), lambda: selected.append('activate'))
        self.assertEqual(m.run(2536, m.event), 0xc0000002)
        self.assertEqual(m.run(m.bindings[3, 0x6f] >> 2, m.event), 0xc0000002)
        self.assertEqual(selected, [])
        self.assertEqual(m.bindings[3, 0x6f], 1896 * 4 + 2)
        self.assertEqual(m.run(m.bindings[3, 0x6f] >> 2, m.event), 0xc0000002)
        self.assertEqual(selected, ['activate'])

    def test_browse_replaces_old_history_with_fresh_non_browsing_parents(self):
        for command, selector, destination in ((2538, 0xea, 2075), (2539, 0xe8, 2092)):
            m = menu_machine(self.image)
            self.assertEqual(m.run(2536, m.event), 0xc0000002)
            events = []
            def browse():
                operation = m.reg(1)
                if operation == 0xfffffffb:  # r19 contextual view, not a browse mutation
                    return 0x6100
                events.append((operation, m.read(m.read(APP + 0x104) + 8)))
                return 1
            m.hook(method(self.image, selector), browse)
            m.supply_services(((2, (0x6c,)), (26, (0xfe, 0x100, 0xfb))))
            self.assertEqual(m.run(command, m.event), 0xc0000002)
            self.assertEqual(events, [(0xfffffffe, 2537 * 4 + 2), (0xfffffffd, 2537 * 4 + 2)])
            node = m.read(APP + 0x104)
            chain = []
            while node:
                chain.append(m.read(node + 8) >> 2)
                node = m.read(node + 4)
            self.assertEqual(chain, [destination, 2537, 2015, 1882])
            self.assertTrue(all(status == 1 for status in m.callback_status))

    def test_browse_menu_returns_one_level_with_native_key_snapshots(self):
        for command in (2538, 2539):
            with self.subTest(command=command):
                m = native_key_machine(self.image)
                m.supply_services(((2, (0x6c,)), (26, (0xfe, 0x100, 0xfb))))
                m.hook(method(self.image, 0xea), lambda: 0x6100 if m.reg(1) == 0xfffffffb else 1)
                m.hook(method(self.image, 0xe8), lambda: 1)
                m.run(2536, m.event)
                m.run(1888, m.event)
                for _ in range(3):
                    self.assertEqual(m.run(command, m.event), 0xc0000002)
                    self.assertEqual(m.run(m.binding(3, 0x6e) >> 2, m.event), 0xc0000002)
                    self.assertEqual(m.read(m.read(APP + 0x104) + 8) >> 2, 2537)
                    self.assertEqual(m.binding(3, 0x6f) >> 2, 1896)
                m.run(m.binding(3, 0x6e) >> 2, m.event)
                self.assertEqual(m.read(m.read(APP + 0x104) + 8) >> 2, 2015)
                self.assertEqual(m.binding(3, 0x6f) >> 2, 2029)
                self.assertEqual(m.binding(2, 0x6f) >> 2, 2536)
                self.assertTrue(all(status == 1 for status in m.callback_status))

    def test_artist_album_depth_and_hold_menu(self):
        m = native_key_machine(self.image)
        m.supply_services(((2, (0x6c,)), (26, (0xfe, 0x100, 0xfb))))
        m.hook(method(self.image, 0xea), lambda: 0x6100 if m.reg(1) == 0xfffffffb else 1)
        m.hook(method(self.image, 0xe8), lambda: 1)
        m.hook(m.method_address(3, 0xaa), lambda: 1)
        m.run(2536, m.event)
        m.run(1888, m.event)
        m.run(2539, m.event)
        m.run(2093, m.event)  # native Album selection opens Songs
        self.assertEqual(m.read(m.read(APP + 0x104) + 8) >> 2, 2075)
        for parent in (2092, 2537, 2015):
            m.run(m.binding(3, 0x6e) >> 2, m.event)
            self.assertEqual(m.read(m.read(APP + 0x104) + 8) >> 2, parent)
        m.run(2536, m.event)
        m.run(1888, m.event)
        m.run(2538, m.event)
        m.run(2120, m.event)
        self.assertEqual(m.read(m.read(APP + 0x104) + 8) >> 2, 1882)
        m.run(1860, m.event)  # release after holding Menu must stay home
        self.assertEqual(m.read(m.read(APP + 0x104) + 8) >> 2, 1882)
        self.assertTrue(all(status == 1 for status in m.callback_status))


    def test_missing_metadata_keeps_actions_menu_open(self):
        m = menu_machine(self.image)
        self.assertEqual(m.run(2536, m.event), 0xc0000002)
        node = m.read(APP + 0x104)
        for command in (2538, 2539):
            self.assertEqual(m.run(command, m.event), 0xc0000002)
            self.assertEqual(m.read(APP + 0x104), node)

    def test_metadata_disappearing_during_cleanup_keeps_fresh_actions_open(self):
        for command, selector in ((2538, 0xea), (2539, 0xe8)):
            m = native_key_machine(self.image)
            m.hook(method(self.image, selector), lambda:
                   0x6100 if m.reg(1) == 0xfffffffb else int(m.reg(1) == 0xfffffffe))
            m.run(2536, m.event)
            m.run(1888, m.event)
            self.assertEqual(m.run(command, m.event), 0xc0000002)
            self.assertEqual(m.read(m.read(APP + 0x104) + 8) >> 2, 2537)
            self.assertEqual(m.binding(3, 0x6f) >> 2, 1896)
            m.run(m.binding(3, 0x6e) >> 2, m.event)
            self.assertEqual(m.read(m.read(APP + 0x104) + 8) >> 2, 2015)

    def test_shuffle_row_uses_original_setting_command_and_arguments(self):
        m = menu_machine(self.image)
        settings = m.object(27, 0x10016000)
        m.call(0xe7658, m.global_slot(0x9ba), settings)
        m.hook(m.method_address(3, 0xa7), lambda: 0x27b5)
        m.hook(m.method_address(3, 0xa8), lambda: 0x2780)
        calls = []
        m.hook(m.method_address(27, 0x140), lambda: calls.append(tuple(m.reg(i) for i in range(4))))
        for _ in range(3):
            self.assertEqual(m.run(2155, m.event), 0xc0000002)
        self.assertEqual(calls, [(0x10016000, 0x27b5, 0x2780, 0x283f)] * 3)

    def test_native_key_snapshot_returns_to_normal_controls_from_every_mode(self):
        for mode in (None, 2029, 2042, 2048):
            with self.subTest(mode=mode):
                m = native_key_machine(self.image)
                if mode:
                    m.run(mode, m.event)  # actual stock seek/rating/alternate-panel entry
                m.run(2536, m.event)
                self.assertEqual(m.binding(3, 0x6f), 1888 * 4 + 2)
                m.run(1888, m.event)  # release the held Center button
                m.run(1860, m.event)
                self.assertEqual(m.binding(3, 0x6f), 2029 * 4 + 2)
                self.assertEqual(m.binding(3, 0x5a), 1931 * 4 + 2)
                self.assertEqual(m.binding(3, 0x59), 1932 * 4 + 2)
                self.assertEqual(m.binding(2, 0x6f), 2536 * 4 + 2)
                self.assertEqual(m.binding(1, 0x57), 0)
                self.assertEqual(m.binding(1, 0x58), 0)
                self.assertEqual(m.run(m.binding(3, 0x6f) >> 2, m.event), 0xc0000002)
                self.assertEqual(m.binding(3, 0x6f), 2032 * 4 + 2)
                self.assertEqual(bytes(m.cpu.mem_read(0x10014000 + 0x1908, 1)), b'\x01')
                self.assertTrue(all(status == 1 for status in m.callback_status))

    def test_normalizing_controls_still_commits_pending_seek_once(self):
        m = native_key_machine(self.image)
        m.run(2029, m.event)
        ui = 0x10014000
        m.cpu.mem_write(ui + 0x190a, b'\x01\x01')
        m.write(ui + 0x190c, 12345, 100)
        m.hook(0x9bc7c, lambda: 1000)
        audio = []
        m.hook(0xca4f8, lambda: audio.append(('seek', m.reg(1))))
        m.hook(0x4d4bc, lambda: audio.append(('command', m.reg(0))))
        m.run(2536, m.event)
        self.assertEqual(audio, [('seek', 12345), ('command', 10)])
        self.assertEqual(bytes(m.cpu.mem_read(ui + 0x1908, 4)), b'\x00\x01\x00\x00')
        m.run(1888, m.event)
        m.run(1860, m.event)
        self.assertEqual(audio, [('seek', 12345), ('command', 10)])
        self.assertEqual(m.binding(3, 0x6f), 2029 * 4 + 2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    BrowseActions.image = ActionMenus.image = load_image(args.candidate)
    unittest.main(argv=[__file__], verbosity=2)
