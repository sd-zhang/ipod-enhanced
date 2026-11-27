"""Behavior checks using the actual packaged 4G ARM code and command interpreter."""
import argparse
import unittest
from pathlib import Path

from native_fixture import APP, QUERY, Library, ScriptVM, load_image


class CompilationArtists(unittest.TestCase):
    def test_compilation_only_artist_hidden_but_mixed_artist_kept(self):
        for mixed in ((True, False), (False, True)):
            m = Library(self.image, [("A", True), ("B", mixed[0]), ("B", mixed[1]), ("C", False)])
            self.assertEqual(m.build_index(), ["B", "C"])
            self.assertEqual([m.selected_artist(i) for i in (-1, 0, 1, 2)], [None, "B", "C", None])
            self.assertEqual(m.lookups, 4)
            self.assertEqual(m.locks, ["lock", "unlock"])

    def test_empty_and_all_compilation_libraries(self):
        for tracks in ([], [("A", True)], [("A", True), ("B", True)]):
            m = Library(self.image, tracks)
            self.assertEqual(m.build_index(), [])
            self.assertIsNone(m.selected_artist(0))

    def test_normal_names_deduplication_and_non_ascii(self):
        names = ["A", "A", "Björk", "Björk", "東京"]
        m = Library(self.image, [(name, False) for name in names])
        self.assertEqual(m.build_index(), ["A", "Björk", "東京"])

    def test_cache_hit_does_not_rescan(self):
        m = Library(self.image, [("A", True), ("B", False)])
        self.assertEqual(m.build_index(), ["B"])
        before = m.lookups, m.storage_reads, len(m.allocations), len(m.locks)
        self.assertEqual(m.build_index(), ["B"])
        self.assertEqual((m.lookups, m.storage_reads, len(m.allocations), len(m.locks)), before)

    def test_album_composer_and_genre_indexes_keep_compilations(self):
        for mask in (2, 0x40, 0x4000000):
            m = Library(self.image, [("A", True), ("B", False)])
            self.assertEqual(m.build_index(mask), ["A", "B"])

    def test_filter_does_not_poison_track_query_cache(self):
        m = Library(self.image, [("A", True), ("A", False)], artist_filter="A")
        self.assertEqual(m.build_index(), ["A"])
        # Album/song browsing uses this original predicate. Both tracks remain
        # playable after the artist list has excluded the compilation entry.
        for wrapper in m.wrappers:
            self.assertEqual(m.call(0x4e028, 0x10001000, wrapper), 1)

    def test_query_filter_applied_before_compilation_rule(self):
        m = Library(self.image, [("A", True), ("B", False)], artist_filter="A")
        self.assertEqual(m.build_index(), [])

    def test_native_count_and_select_query_agree_with_filtered_rows(self):
        m = Library(self.image, [("A", True), ("B", True), ("B", False), ("C", False)])
        self.assertEqual(m.call(0x7157c, QUERY), 2)
        self.assertEqual(m.call(0xc5594, QUERY, 0), 0)
        self.assertEqual([m.call(0x4e028, QUERY, w) for w in m.wrappers], [0, 1, 1, 0])
        self.assertEqual(m.call(0xc5594, QUERY, 1), 0)
        self.assertEqual([m.call(0x4e028, QUERY, w) for w in m.wrappers], [0, 0, 0, 1])
        self.assertEqual(m.call(0xc5594, QUERY, 0xffffffff), 0)  # native All row
        self.assertEqual([m.call(0x4e028, QUERY, w) for w in m.wrappers], [1, 1, 1, 1])
        self.assertEqual(m.call(0xc5594, QUERY, 2), 0xffffffce)

    def test_all_compilation_native_count_and_all_row_remain_usable(self):
        m = Library(self.image, [("A", True), ("B", True)])
        self.assertEqual(m.call(0x7157c, QUERY), 0)
        library_object = 0x10011000
        m.write(library_object + 0x1c, QUERY)
        # This is the actual script selector 0xe8, including the UI transition
        # guard above the query setter. The query setter alone is insufficient.
        self.assertEqual(m.call(0xc560c, library_object, 0), 1)
        self.assertEqual(m.call(0xc5594, QUERY, 0xffffffff), 0)
        self.assertEqual([m.call(0x4e028, QUERY, w) for w in m.wrappers], [1, 1])
        self.assertEqual(m.call(0xc560c, library_object, 1), 0)
        self.assertEqual(m.call(0xc560c, library_object, 0xffffffff), 0)

    def test_ui_all_guard_preserves_empty_library_and_allocation_failure(self):
        for tracks in ([], [("A", True)]):
            m = Library(self.image, tracks)
            if tracks:
                m.hook(0x9b8e0, lambda: 0)
            library_object = 0x10011000
            m.write(library_object + 0x1c, QUERY)
            self.assertEqual(m.call(0xc560c, library_object, 0), 0)

    def test_long_name_and_empty_name_preserve_stock_handling(self):
        m = Library(self.image, [("", False), ("A" * 255, True), ("B" * 255, False)])
        self.assertEqual(m.build_index(), ["B" * 255])
        self.assertEqual(m.selected_artist(0), "B" * 255)

    def test_allocation_failure_does_not_publish_partial_index(self):
        m = Library(self.image, [("A", False)])
        m.hook(0x9b8e0, lambda: 0)
        self.assertEqual(m.call(0x7157c, QUERY), 0)
        self.assertEqual(m.read(QUERY + 0xa8c), 0)
        self.assertEqual(m.lookups, 0)
        self.assertEqual(m.locks, [])

    def test_invalidation_rebuilds_after_compilation_flag_changes(self):
        m = Library(self.image, [("A", True), ("B", False)])
        self.assertEqual(m.build_index(), ["B"])
        allocation = m.read(QUERY + 0xa8c)
        m.call(0x91158, QUERY)  # native artist cache invalidation
        m.cpu.mem_write(m.read(m.wrappers[0] + 0x20) + 0x11, b"\x00")
        self.assertEqual(m.build_index(), ["A", "B"])
        self.assertIn(allocation, m.freed)


class HoldMenu(unittest.TestCase):
    def test_handler_calls_native_home_unwind_with_animation_disabled(self):
        m = ScriptVM(self.image)
        events = []
        m.write(m.global_slot(0x9a0), 0xc0000002)  # native VM true
        m.hook(0x1b828, lambda: int(m.reg(0) == APP and m.reg(1) == 1882 * 4 + 2))
        m.hook(0x1b8b4, lambda: events.append((m.reg(0), m.reg(1), m.read(m.global_slot(0x9a0)))))
        m.hook(0xad7a4, lambda: events.append("backlight"))
        self.assertEqual(m.run(2120, m.event), 0xc0000002)
        self.assertEqual(events, [(APP, 1882 * 4 + 2, 0xc0000000)])
        self.assertEqual(m.read(m.global_slot(0x9a0)), 0xc0000002)

    def test_native_history_unwinds_and_releases_nodes_in_order(self):
        m = ScriptVM(self.image)
        home, music, album = m.history([(1882, 0), (2108, 1900), (2091, 1901)])
        m.write(m.global_slot(0x9a0), 0xc0000002)
        self.assertEqual(m.run(2120, m.event), 0xc0000002)
        self.assertEqual(m.read(APP + 0x104), home)
        self.assertEqual(m.commands, [1901 * 4 + 2, 2108 * 4 + 2, 1900 * 4 + 2, 1882 * 4 + 2])
        self.assertIn(album, m.freed)
        self.assertIn(music, m.freed)
        self.assertNotIn(home, m.freed)
        self.assertEqual(m.read(m.global_slot(0x9a0)), 0xc0000002)
        m.commands.clear()
        before = len(m.freed)
        for _ in range(10):
            self.assertEqual(m.run(2120, m.event), 0xc0000002)
        self.assertEqual(m.read(APP + 0x104), home)
        self.assertEqual(m.commands, [])
        self.assertNotIn(home, m.freed[before:])
        self.assertEqual(m.run(1860, m.event), 0xc0000002)  # a subsequent Menu release/back
        self.assertEqual(m.read(APP + 0x104), home)
        self.assertEqual(m.commands, [])

    def test_short_menu_still_pops_just_one_level(self):
        m = ScriptVM(self.image)
        home, music, album = m.history([(1882, 0), (2108, 1900), (2091, 1901)])
        self.assertEqual(m.run(1860, m.event), 0xc0000002)
        self.assertEqual(m.read(APP + 0x104), music)
        self.assertEqual(m.commands, [1901 * 4 + 2, 2108 * 4 + 2])
        self.assertIn(album, m.freed)
        self.assertNotIn(home, m.freed)

    def test_missing_home_uses_existing_home_constructor(self):
        m = ScriptVM(self.image)
        m.history([])
        m.write(m.global_slot(0x9a0), 0xc0000002)
        # Native title-widget services and binding snapshot are UI boundaries.
        # The script's fallback, history push, and callback assignment run.
        for address in (0x1ba10, 0x1b848, 0xc3590):
            m.hook(address, lambda: 0)
        self.assertEqual(m.run(2120, m.event), 0xc0000002)
        node = m.read(APP + 0x104)
        self.assertEqual(m.read(node + 8), 1882 * 4 + 2)
        self.assertEqual(m.read(node + 0x10), 1884 * 4 + 2)
        self.assertEqual(m.read(node + 0x14), 1883 * 4 + 2)
        self.assertEqual(m.commands, [1882 * 4 + 2])
        self.assertEqual(m.read(m.global_slot(0x9a0)), 0xc0000002)

    def test_manual_backlight_menu_item_remains_available(self):
        m = ScriptVM(self.image)
        calls = []
        m.hook(0x7f41c, lambda: calls.append("backlight"))
        self.assertEqual(m.run(2122, m.event), 0xc0000002)
        self.assertEqual(calls, ["backlight"])

    def test_special_screen_unregisters_hold_and_restores_it_on_exit(self):
        m = ScriptVM(self.image)
        bindings = {}
        self.assertEqual(m.call(0x949d0, 2012 * 4 + 2, 0x10010000, 0x10010004), 1)
        m.call(0xe7658, m.global_slot(0x9b2), m.read(0x10010000))
        # Boundary is the app's key-binding service. Execute the complete
        # original enter/leave scripts; no hardware button timing is simulated.
        def register():
            bindings[m.reg(1), m.reg(2)] = m.reg(3)
            return 0
        def unregister():
            bindings.pop((m.reg(1), m.reg(2)), None)
            return 0
        m.hook(0x1b964, register)
        m.hook(0x1b538, unregister)
        for type_index, selectors in ((2, (0x61, 0x6c, 0x77, 0x6f, 0x65, 0x79)),
                                      (25, (0xdb, 0xcf)), (26, (0x121,))):
            for selector in selectors:
                m.hook(m.method_address(type_index, selector), lambda: 0)
        m.run(1928, m.event)
        self.assertEqual(bindings[2, 0x6e], 2120 * 4 + 2)
        self.assertEqual(m.run(2004), 0xc0000002)
        self.assertNotIn((2, 0x6e), bindings)
        self.assertEqual(m.run(2007), 0xc0000002)
        self.assertEqual(bindings[2, 0x6e], 2120 * 4 + 2)

    def test_recording_leave_callback_reaches_stop_before_home(self):
        m = ScriptVM(self.image)
        home, recording = m.history([(1882, 0), (2361, 2357)])
        m.real_leave_callbacks()
        recorder = m.object(39, 0x10016000)
        m.call(0xe7658, m.global_slot(0x9df), recorder)
        m.supply_services(((2, (0x6a, 0x65)), (3, (0x8e,))))
        stopped = []
        m.hook(m.method_address(39, 0x19c), lambda: stopped.append(m.read(APP + 0x104)))
        self.assertEqual(m.run(2120, m.event), 0xc0000002)
        self.assertEqual(m.callback_status, [1])
        self.assertEqual(stopped, [recording])
        self.assertEqual(m.commands, [2357 * 4 + 2, 1882 * 4 + 2])
        self.assertEqual(m.read(APP + 0x104), home)

    def test_playback_leave_runs_native_seek_cleanup(self):
        m = ScriptVM(self.image)
        home, playback = m.history([(1882, 0), (2015, 2020)])
        m.real_leave_callbacks()
        # Key services (including the new Hold Center unregistration) are UI
        # boundaries; this test executes seek cleanup down to audio submission.
        m.supply_services(((2, (0x64, 0x65, 0x6a, 0x6b, 0x6d)), (3, (0x8a,)), (25, (0xda,))))
        library = 0x10014000
        m.write(library + 0x1c, QUERY)
        m.call(0x60c70, library)  # actual seek-mode entry
        m.cpu.mem_write(library + 0x190a, b"\x01\x01")  # pending seek + resume
        m.write(library + 0x190c, 12345, 100)
        m.hook(0x9bc7c, lambda: 1000)  # OS ticks; pending seek exceeds commit delay
        audio = []
        m.hook(0xca4f8, lambda: audio.append(("seek", m.reg(0), m.reg(1))))
        m.hook(0x4d4bc, lambda: audio.append(("command", m.reg(0), m.reg(1))))
        self.assertEqual(m.run(2120, m.event), 0xc0000002)
        self.assertEqual(m.callback_status, [1])
        self.assertEqual(audio, [("seek", QUERY, 12345), ("command", 10, 0)])
        self.assertEqual(bytes(m.cpu.mem_read(library + 0x1908, 4)), b"\x00\x01\x00\x00")
        self.assertEqual(m.commands, [2020 * 4 + 2, 1882 * 4 + 2])
        self.assertEqual(m.read(APP + 0x104), home)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ipsw", type=Path)
    args = parser.parse_args()
    image = load_image(args.ipsw)
    CompilationArtists.image = HoldMenu.image = image
    unittest.main(argv=[__file__], verbosity=2)
