"""Run Repeat UI and album queue switching with native filtering and seek code.

Disk queue allocation/append/delete, audio load/start, decoder I/O and drawing
are supplied boundaries. The helper, query/index, queue-copy traversal, repeat
provider, absolute seek bridge and pause state machine execute ARM firmware.
This verifies dispatch/state contracts, not audible playback on hardware.
Run repository Python outside the macOS sandbox for Unicorn's JIT.
"""
import argparse
import struct
import unittest
from pathlib import Path

from native_fixture import QUERY, DB, PLAYLIST, load_image
from verify_actions import UI, library, method, menu_machine
from verify_resources import directory, word

AUDIO, OLD, CURRENT = 0x10016000, 0x1001a000, 0x1001b000
NEW, HEADER, DECODER = 0x1001c000, 0x1001d000, 0x1001e000


def album_machine(image, *, paused=False, failure=None, machine=None, ui=UI):
    m = machine or library(image, tracks=[('A', True), ('B', True), ('C', False)])
    m.ui = ui
    if machine:
        from verify_actions import pascal
        for offset, text in ((0x28, 'A song'), (0x428, 'A'), (0x628, 'A')):
            pascal(m, ui + offset, text)
        m.hook(0xe24d0, lambda: 0)
    # A compilation album has songs by different artists, even when the old
    # queue came from a mixed playlist and the selected song is not its first.
    m.write(m.read(m.wrappers[1] + 0x20) + 0x30, 1)
    m.write(DB + 4, AUDIO)
    m.write(QUERY + 0x44, OLD)
    m.write(AUDIO + 0x2a4, OLD, CURRENT)
    m.write(AUDIO + 0x2768, 2 if paused else 1)
    m.write(AUDIO + 0x2778, 12345)
    m.write(AUDIO + 0x2998, 180000)
    m.write(AUDIO + 0x2994, 8)
    m.write(AUDIO + 0xc, DECODER)
    m.cpu.mem_write(DECODER + 0x2b6, b'\x01')
    m.write(CURRENT, OLD)
    m.write(CURRENT + 0x20, m.read(m.wrappers[1] + 0x20))
    m.write(NEW + 0x24, HEADER)
    m.write(NEW + 8, DB)
    m.queue, m.deleted, m.started, m.seeks = [], [], [], []
    m.failures = set()

    def fail_once(kind):
        if failure == kind and kind not in m.failures:
            m.failures.add(kind)
            return True
        return False

    def create():
        m.write(m.reg(1), NEW)
        return -108 if fail_once('create') else 0

    def append():
        source, destination = m.reg(0), m.reg(1)
        assert destination == NEW
        if failure == 'append' and len(m.queue) == 1:
            return 0
        item = NEW + 0x400 + 0x40 * len(m.queue)
        m.write(item, NEW)
        m.write(item + 0x10, 100 + len(m.queue))
        m.write(item + 0x18, m.read(source + 0x10))
        m.write(item + 0x20, m.read(source + 0x20))
        m.queue.append(item)
        m.cpu.mem_write(HEADER + 0x2e, struct.pack('<H', len(m.queue)))
        if failure == 'race':
            m.write(AUDIO + 0x2a8, CURRENT + 0x100)
        if failure == 'copy_no_decoder':
            m.cpu.mem_write(DECODER + 0x2b6, b'\0')
        return item

    def lookup():
        source, order, flags, row = [m.reg(i) for i in range(4)]
        if source == NEW:
            assert (order, flags) == (1, 0)
            return m.queue[row] if row < len(m.queue) else 0
        assert source == PLAYLIST and (order, flags) == (6, 0)
        return m.wrappers[row]

    def prepare():
        assert m.reg(0) == AUDIO
        item = m.reg(1)
        m.write(AUDIO + 0x2768, 0)
        m.write(AUDIO + 0x2a4, m.read(item) if item else 0, item)
        m.write(AUDIO + 0x2778, 0)
        return -50 if fail_once('prepare') else 0

    def start():
        assert m.reg(0) == AUDIO and m.reg(2) == 1
        item = m.reg(1)
        m.started.append(item)
        if failure == 'rollback' and item == CURRENT:
            return -50  # no successful rebind: still references failed NEW
        m.write(AUDIO + 0x2a4, m.read(item), item)
        m.write(AUDIO + 0x2768, 1)
        m.write(AUDIO + 0x2778, 0)
        m.write(AUDIO + 0x2994, 8)
        m.write(AUDIO + 0x2998, 180000)
        m.cpu.mem_write(DECODER + 0x2b6, b'\x01')
        if item != CURRENT:
            if failure == 'post_start_no_decoder':
                m.cpu.mem_write(DECODER + 0x2b6, b'\0')
            if failure == 'post_start_not_seekable':
                m.write(AUDIO + 0x2994, 0)
            if failure == 'post_start_no_duration':
                m.write(AUDIO + 0x2998, 0)
        return -50 if fail_once('start') or failure == 'rollback' else 0

    def seek():
        m.seeks.append((m.read(AUDIO + 0x2a8), m.reg(2)))
        if fail_once('seek'):
            return -50
        m.write(m.reg(3), m.reg(2))
        return 0

    def delete():
        queue = m.reg(0)
        assert queue != m.read(AUDIO + 0x2a4), 'freeing active audio queue'
        assert queue != m.read(QUERY + 0x44), 'freeing query-owned queue'
        m.deleted.append(queue)
        return 0

    m.hook(0x9d92c, create)
    m.hook(0x47ea4, append)
    m.hook(0x6e400, lookup)
    m.hook(0x537bc, delete)
    m.hook(0xa91b0, prepare)
    m.hook(0xa8be8, start)
    m.hook(0xcbb54, lambda: 0)
    m.hook(0xdc944, lambda: m.write(m.reg(1), 321) or 0)
    m.hook(0xa8084, lambda: 321)
    m.hook(0xdcb78, seek)
    m.hook(0xa8268, lambda: DECODER)
    for address in (0xe1fc4, 0xc77d4, 0xa9e58, 0xdce9c, 0xdca10, 0xdcde8):
        m.hook(address, lambda: 0)
    if failure == 'index':
        m.hook(0x9b8e0, lambda: 0)
    return m


class RepeatActions(unittest.TestCase):
    with_sleep = False
    def test_repeat_submenu_has_off_one_album_all_choices(self):
        groups = directory(self.image)
        menu = groups['Menu'][1][0x6102]
        self.assertEqual(word(menu, 0), 5 if self.with_sleep else 4)
        repeat = bytearray(menu[124:164])
        original = groups['Menu'][1][0x278d][124:164]
        self.assertEqual(word(repeat, 4), 0x22)
        self.assertEqual(struct.unpack_from('<H', repeat, 22)[0], 0)
        self.assertEqual(word(repeat, 12), 2542 * 4 + 2)
        repeat[4:8], repeat[12:16], repeat[22:24] = original[4:8], original[12:16], original[22:24]
        self.assertEqual(repeat, original)
        submenu = groups['Menu'][1][0x6109]
        self.assertEqual(word(submenu, 0), 4)
        for row, (command, label) in enumerate(((2544, 0x27ba), (2545, 0x27bb),
                                               (2541, 0x610a), (2546, 0x27bc))):
            self.assertEqual(word(submenu, 4 + 40 * row + 12), command * 4 + 2)
            self.assertEqual(word(submenu, 4 + 40 * row + 20), label)
        self.assertEqual(groups['Str '][1][0x610a], b'Album\0')

    def test_explicit_repeat_choices_set_native_preference_and_queue(self):
        from native_fixture import APP
        m = menu_machine(self.image)
        settings, query, prefs, queue = 0x10060000, 0x10062000, 0x10064000, 0x10066000
        m.write(settings, 0x23a48)
        m.write(settings + 0x58, query)
        m.write(query, prefs)
        m.write(query + 0x44, queue)
        m.write(queue + 8, prefs)
        for address in (0xc77d4, 0xcb554, 0xcdde8):
            m.hook(address, lambda: 0)
        m.call(0xe7658, m.global_slot(0x9ba), m.object(27, settings))
        for command, setting, mode in ((2545, 1, 2), (2546, 2, 1),
                                        (2546, 2, 1), (2544, 0, 0)):
            self.assertEqual(m.run(command, m.event), 0xc0000002)
            self.assertEqual(bytes(m.cpu.mem_read(settings + 0x1d, 1)), bytes([setting]))
            self.assertEqual(m.read(prefs + 0x1c), mode)
            self.assertEqual(m.read(queue + 0xdc) & 0xffff, mode)

    def test_repeat_menu_back_returns_to_actions(self):
        from native_fixture import APP
        m = menu_machine(self.image)
        m.run(2536, m.event)
        m.run(1888, m.event)
        m.run(2542, m.event)
        self.assertEqual(m.read(m.read(APP + 0x104) + 8), 2543 * 4 + 2)
        m.run(1860, m.event)
        self.assertEqual(m.read(m.read(APP + 0x104) + 8), 2537 * 4 + 2)

    def test_hold_menu_from_repeat_returns_home_and_consumes_release(self):
        from verify_actions import native_key_machine
        from native_fixture import APP
        m = native_key_machine(self.image)
        m.supply_services(((2, (0x6c,)),))
        m.run(2536, m.event)
        m.run(1888, m.event)
        m.run(2542, m.event)
        m.run(2120, m.event)
        self.assertEqual(m.read(m.read(APP + 0x104) + 8), 1882 * 4 + 2)
        m.run(1860, m.event)
        self.assertEqual(m.read(m.read(APP + 0x104) + 8), 1882 * 4 + 2)

    def test_native_factories_construct_submenu_and_all_choices(self):
        from verify_actions_widgets import ActionsDisplay
        m = ActionsDisplay(self.image, view=0x6107)
        self.assertEqual(m.read(m.menu + 0xa0), 4)
        m.prepare_renderer()
        m.call(0xdd19c, m.RENDERER, 0x6107, 0, 30, 300)
        for row in (0, 1, 2, 3):
            m.call(0xd11c8, m.menu, row)
            self.assertEqual(m.call(0x96fc4, m.menu), row)
            item = m.call(0x6cebc, m.read(m.menu + 0xe8), row)
            record = m.read(item + 0x10)
            self.assertEqual(m.read(record + 8), (2544, 2545, 2541, 2546)[row] * 4 + 2)

    def test_repeat_row_center_builds_submenu_command_without_cycling_setting(self):
        from verify_actions_widgets import ActionsVMDisplay
        settings = 0x10060000
        m = ActionsVMDisplay(self.image, settings=settings)
        m.prepare_renderer()
        m.call(0xdd19c, m.RENDERER, 0x6100, 0, 30, 300)
        m.call(0xc60ac, m.menu, 3)
        events = []
        del m.hooks[0xaba84]
        m.hook(0xab854, lambda: events.append(m.reg(0)) or 1)
        m.hook(0xd915c, m.free)
        m.hook(0xd9a50, m.free)
        m.call(0x1b518, m.RENDERER)
        self.assertEqual(len(events), 1)
        self.assertEqual(m.read(events[0] + 12), 2542 * 4 + 2)
        self.assertEqual(bytes(m.cpu.mem_read(settings + 0x1d, 1)), b'\0')

    def test_album_script_runs_helper_sets_all_and_preserves_menu_history(self):
        from native_fixture import APP
        from verify_actions_playback import playback_machine, UI as PLAYBACK_UI
        for failure in (None, 'append'):
            with self.subTest(failure=failure):
                m = album_machine(self.image, failure=failure,
                                  machine=playback_machine(self.image, 2075), ui=PLAYBACK_UI)
                settings = 0x10060000
                m.write(settings, 0x23a48)
                m.write(settings + 0x58, QUERY)
                m.call(0xe7658, m.global_slot(0x9ba), m.object(27, settings))
                for address in (0xcb554, 0xcdde8):
                    m.hook(address, lambda: 0)
                m.run(2536, m.event)
                m.run(1888, m.event)
                m.run(2542, m.event)
                self.assertEqual(m.run(2541, m.event), 0xc0000002)
                self.assertEqual(m.read(m.read(APP + 0x104) + 8), 2543 * 4 + 2)
                self.assertEqual(m.read(DB + 0x1c), 1 if failure is None else 0)
                self.assertEqual(m.read(QUERY + 0x44), NEW if failure is None else OLD)
                m.run(1860, m.event)
                self.assertEqual(m.read(m.read(APP + 0x104) + 8), 2537 * 4 + 2)

    def invoke(self, m):
        return m.call(method(self.image, 0xea), m.ui, 0xfffffffc)

    def test_album_preserves_current_track_and_elapsed_in_album_order(self):
        m = album_machine(self.image)
        self.assertEqual(self.invoke(m), 1)
        self.assertEqual(m.read(QUERY + 0x44), NEW)
        self.assertEqual([m.read(w + 0x20) for w in m.queue],
                         [m.read(w + 0x20) for w in m.wrappers[:2]])
        self.assertEqual(m.read(AUDIO + 0x2a8), m.queue[1])
        self.assertEqual(m.read(AUDIO + 0x2778), 12345)
        self.assertEqual(m.seeks, [(m.queue[1], 12666)])
        self.assertEqual(m.read(AUDIO + 0x2768), 1)
        self.assertEqual(m.deleted, [OLD])

    def test_paused_album_remains_paused(self):
        m = album_machine(self.image, paused=True)
        self.assertEqual(self.invoke(m), 1)
        self.assertEqual(m.read(AUDIO + 0x2768), 2)
        self.assertEqual(m.read(AUDIO + 0x2778), 12345)

    def test_allocation_and_partial_copy_failures_keep_old_playback(self):
        for failure in ('index', 'create', 'append'):
            with self.subTest(failure=failure):
                m = album_machine(self.image, failure=failure)
                self.assertEqual(self.invoke(m), 0)
                self.assertEqual(m.read(QUERY + 0x44), OLD)
                self.assertEqual(m.read(AUDIO + 0x2a8), CURRENT)
                self.assertEqual(m.read(AUDIO + 0x2778), 12345)
                self.assertEqual(m.started, [])
                self.assertNotIn(OLD, m.deleted)
                if failure != 'index':
                    self.assertEqual(m.deleted, [NEW])

    def test_missing_current_track_does_not_replace_queue(self):
        m = album_machine(self.image)
        m.write(CURRENT + 0x20, m.read(m.wrappers[2] + 0x20))
        self.assertEqual(self.invoke(m), 0)
        self.assertEqual(m.read(QUERY + 0x44), OLD)
        self.assertEqual(m.started, [])

    def test_start_and_seek_failures_restore_old_queue_position_and_pause(self):
        for failure in ('prepare', 'start', 'seek'):
            with self.subTest(failure=failure):
                m = album_machine(self.image, paused=True, failure=failure)
                self.assertEqual(self.invoke(m), 0)
                self.assertEqual(m.read(QUERY + 0x44), OLD)
                self.assertEqual(m.read(AUDIO + 0x2a8), CURRENT)
                self.assertEqual(m.read(AUDIO + 0x2778), 12345)
                self.assertEqual(m.read(AUDIO + 0x2768), 2)
                self.assertEqual(m.deleted, [NEW])

    def test_library_owned_old_queue_is_not_deleted(self):
        m = album_machine(self.image)
        m.write(QUERY + 0x48, OLD)
        self.assertEqual(self.invoke(m), 1)
        self.assertEqual(m.deleted, [])

    def test_track_change_during_copy_leaves_new_playing_track_untouched(self):
        m = album_machine(self.image, failure='race')
        self.assertEqual(self.invoke(m), 0)
        self.assertEqual(m.read(AUDIO + 0x2a8), CURRENT + 0x100)
        self.assertEqual(m.read(QUERY + 0x44), OLD)
        self.assertEqual(m.started, [])
        self.assertEqual(m.deleted, [NEW])

    def test_failed_rollback_detaches_audio_before_freeing_temporary_queue(self):
        m = album_machine(self.image, failure='rollback')
        self.assertEqual(self.invoke(m), 0)
        self.assertEqual(m.read(QUERY + 0x44), OLD)
        self.assertEqual(m.read(AUDIO + 0x2a8), 0)
        self.assertEqual(m.read(AUDIO + 0x2a4), 0)
        self.assertEqual(m.read(AUDIO + 0x2768), 0)
        self.assertEqual(m.deleted, [NEW])

    def test_unrestorable_nonzero_position_is_rejected_before_queue_creation(self):
        for unavailable in ('decoder', 'seekability', 'duration'):
            with self.subTest(unavailable=unavailable):
                m = album_machine(self.image)
                if unavailable == 'decoder':
                    m.cpu.mem_write(DECODER + 0x2b6, b'\0')
                elif unavailable == 'seekability':
                    m.write(AUDIO + 0x2994, 0)
                else:
                    m.write(AUDIO + 0x2998, 0)
                self.assertEqual(self.invoke(m), 0)
                self.assertEqual(m.read(QUERY + 0x44), OLD)
                self.assertEqual(m.read(AUDIO + 0x2a8), CURRENT)
                self.assertEqual(m.read(AUDIO + 0x2778), 12345)
                self.assertEqual((m.queue, m.started, m.seeks, m.deleted), ([], [], [], []))

    def test_decoder_disappearing_during_copy_rejects_before_transport_change(self):
        m = album_machine(self.image, failure='copy_no_decoder')
        self.assertEqual(self.invoke(m), 0)
        self.assertEqual(m.read(QUERY + 0x44), OLD)
        self.assertEqual(m.read(AUDIO + 0x2a8), CURRENT)
        self.assertEqual(m.read(AUDIO + 0x2778), 12345)
        self.assertEqual((m.started, m.seeks, m.deleted), ([], [], [NEW]))

    def test_missing_seek_capability_after_start_rolls_back_without_cached_success(self):
        for failure in ('post_start_no_decoder', 'post_start_not_seekable', 'post_start_no_duration'):
            with self.subTest(failure=failure):
                m = album_machine(self.image, paused=True, failure=failure)
                self.assertEqual(self.invoke(m), 0)
                self.assertEqual(m.read(QUERY + 0x44), OLD)
                self.assertEqual(m.read(AUDIO + 0x2a8), CURRENT)
                self.assertEqual(m.read(AUDIO + 0x2778), 12345)
                self.assertEqual(m.read(AUDIO + 0x2768), 2)
                self.assertEqual(m.seeks, [(CURRENT, 12666)])
                self.assertEqual(m.deleted, [NEW])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--with-sleep', action='store_true')
    args = parser.parse_args()
    RepeatActions.with_sleep = args.with_sleep
    RepeatActions.image = load_image(args.candidate)
    unittest.main(argv=[__file__], verbosity=2)
