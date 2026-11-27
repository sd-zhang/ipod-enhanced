"""Execute contextual OTG actions as ARM/VM; OS/storage boundaries supplied.

Use repository Python outside the macOS sandbox for Unicorn JIT.
"""
import argparse
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

import verify_actions
from native_fixture import NativeMachine, load_image, APP
from verify_actions import method, menu_machine
from verify_resources import directory, records, word
from verify_actions_widgets import ActionsDisplay, ActionsVMDisplay
from unicorn import UC_HOOK_CODE

UI, QUERY, PREFS, AUDIO, QUEUE, OTG, HEADER, CURRENT, TRACK = (
    0x10060000 + i * 0x4000 for i in range(9))


def state(m, *, playing_otg=False, ui=UI, count=3):
    queue = OTG if playing_otg else QUEUE
    m.write(ui + 0x1c, QUERY)
    m.write(QUERY, PREFS)
    m.write(PREFS + 4, AUDIO)
    m.write(QUERY + 0x44, queue, OTG)
    m.write(QUERY + 0xa70, PREFS)
    m.write(AUDIO + 0x2a4, queue, CURRENT)
    m.write(AUDIO + 0x2768, 2)
    m.write(AUDIO + 0x2778, 12345)
    for obj in (QUEUE, OTG):
        m.write(obj + 4, 0x706c7374)
        m.write(obj + 0xc, PREFS)
    m.write(OTG + 0x24, HEADER)
    m.cpu.mem_write(HEADER + 0x2e, struct.pack('<H', count))
    m.write(CURRENT, queue)
    m.write(CURRENT + 0x10, 42)
    m.write(CURRENT + 0x20, TRACK)
    return queue


def mutation_services(m, *, fail_allocation=False):
    """Real append/copy, delete, observer dispatch and audio detachment.

    Supply allocator/index insertion/unlink, decoder teardown, UI refresh,
    database cache and disk services. These are NOT hardware audio tests.
    """
    m.events = []
    m.hook(0x98b48, lambda: m.events.append(('lock', m.reg(0))))
    m.hook(0xdfa74, lambda: m.events.append(('unlock', m.reg(0))))
    allocated = CURRENT + 0x100
    def allocate_item():
        m.events.append(('allocate', m.reg(0)))
        if fail_allocation:
            return 0
        m.write(allocated, OTG)
        m.write(allocated + 0x10, 43)
        return allocated
    m.hook(0x9dbcc, allocate_item)
    def insert():
        m.events.append(('insert', m.reg(0)))
        n = m.read(HEADER + 0x2c) >> 16
        m.cpu.mem_write(HEADER + 0x2e, struct.pack('<H', n + 1))
    m.hook(0x9771c, insert)
    def unlink():
        # Detachment must have completed before unlinking/freeing current.
        assert m.reg(0) == CURRENT
        assert m.read(AUDIO + 0x2a4) == m.read(AUDIO + 0x2a8) == 0
        m.events.append(('unlink', m.reg(0)))
        n = m.read(HEADER + 0x2c) >> 16
        m.cpu.mem_write(HEADER + 0x2e, struct.pack('<H', n - 1))
    m.hook(0x539d4, unlink)
    def free_item():
        assert m.reg(1) == CURRENT
        m.events.append(('free', m.reg(1)))
        m.cpu.mem_write(CURRENT, b'\xa5' * 0x40)
    m.hook(0x53634, free_item)
    # Native MSend / MReceive tags and callback ABI. Only the receiver index
    # accessor is supplied, not c77d4 or a8708 or a92d8.
    receiver, slot = CURRENT + 0x1000, CURRENT + 0x1100
    m.write(OTG + 0x2c, 0x4d536e64)
    m.write(OTG + 0x44, 1)
    m.write(receiver, 0x4d526376)
    m.write(receiver + 0x30, 0xa8708, AUDIO)
    m.write(slot, receiver)
    def listener():
        assert (m.reg(0), m.reg(1)) == (OTG + 0x3c, 1)
        return slot
    m.hook(0x79588, listener)
    def stop_decoder():
        assert (m.reg(0), m.reg(1), m.reg(2)) == (AUDIO, 0, 1)
        m.events.append(('decoder_stop', CURRENT))
        m.write(AUDIO + 0x2768, 0)
        return 0
    m.hook(0xa9e74, stop_decoder)
    m.hook(0xa9e58, lambda: 0)  # decoder service release
    m.hook(0xe1fc4, lambda: m.events.append(('audio_refresh', m.reg(0))))
    m.hook(0xdf794, lambda: m.events.append(('detach', m.reg(1))))
    m.hook(0xcb554, lambda: 0)  # DB dirty service
    m.hook(0x912c4, lambda: m.events.append(('browse_cache', m.reg(0))))
    m.hook(0x9b6fc, m.free)
    m.hook(0xe0de4, lambda: m.events.append(('add_ui', m.reg(0))))
    m.hook(0xe2d40, lambda: m.events.append(('remove_ui', m.reg(0))))
    def trace(cpu, pc, size, context):
        if pc == 0xc77d4 and m.reg(0) == OTG + 0x2c:
            m.events.append(('notify', m.reg(1), m.reg(2)))
    m.cpu.hook_add(UC_HOOK_CODE, trace)
    return allocated


class OTGResources(unittest.TestCase):
    def test_context_menus_preserve_five_existing_rows_and_append_action(self):
        menus = directory(self.image)['Menu'][1]
        add, remove = map(records, (menus[0x6102], menus[0x6109]))
        self.assertEqual(len(add), 6)
        self.assertEqual(len(remove), 6)
        self.assertEqual(add[:5], remove[:5])
        self.assertEqual([word(r, 12) for r in add[:5]],
                         [n * 4 + 2 for n in (2538, 2539, 2155, 2155, 2160)])
        strings = directory(self.image)['Str '][1]
        for row, command, label in ((add[5], 2542, b'Add to On-The-Go\0'),
                                     (remove[5], 2543, b'Remove from On-The-Go\0')):
            self.assertEqual(word(row, 12), command * 4 + 2)
            self.assertEqual(strings[word(row, 20)], label)


class OTGContext(unittest.TestCase):
    def test_uses_playing_queue_independently_of_browse_mode(self):
        for playing in (False, True):
            for browsing in (0, 1, 3, 5, 9):
                m = NativeMachine(self.image)
                state(m, playing_otg=playing)
                m.cpu.mem_write(UI + 0x18a5, bytes([browsing]))
                before = bytes(m.cpu.mem_read(UI, 0x24000))
                self.assertEqual(m.call(method(self.image, 0xea), UI, -5 & 0xffffffff),
                                 0x6107 if playing else 0x6100)
                self.assertEqual(bytes(m.cpu.mem_read(UI, 0x24000)), before)

    def test_missing_or_incoherent_context_has_no_remove_action(self):
        for at in (UI + 0x1c, QUERY, PREFS + 4, QUERY + 0x44, QUERY + 0x48,
                   AUDIO + 0x2a4, AUDIO + 0x2a8, CURRENT, CURRENT + 0x10,
                   CURRENT + 0x20, OTG + 4, OTG + 0x24):
            m = NativeMachine(self.image)
            state(m, playing_otg=True)
            m.write(at, 0)
            self.assertEqual(m.call(method(self.image, 0xea), UI, -5 & 0xffffffff), 0x6100)
            self.assertEqual(m.call(method(self.image, 0xea), UI, -7 & 0xffffffff), 0)


class OTGMutation(unittest.TestCase):
    def make(self, playing=False, count=3, fail=False):
        m = NativeMachine(self.image)
        state(m, playing_otg=playing, count=count)
        m.added = mutation_services(m, fail_allocation=fail)
        return m

    def invoke(self, m, operation):
        return m.call(method(self.image, 0xea), UI, operation & 0xffffffff)

    def test_native_add_copies_live_track_and_preserves_transport_and_query(self):
        m = self.make()
        before = bytes(m.cpu.mem_read(AUDIO, 0x3000)), bytes(m.cpu.mem_read(QUERY, 0xb00))
        self.assertEqual(self.invoke(m, -6), 1)
        self.assertEqual(m.read(m.added), OTG)
        self.assertEqual(m.read(m.added + 0x20), TRACK)
        self.assertEqual(m.read(m.added + 0x18), 42)
        self.assertEqual(m.read(TRACK + 0x20), m.added)
        self.assertEqual(m.read(HEADER + 0x2c) >> 16, 4)
        self.assertEqual(bytes(m.cpu.mem_read(PREFS + 0xb46, 1)), b'\x01')
        self.assertEqual((bytes(m.cpu.mem_read(AUDIO, 0x3000)),
                          bytes(m.cpu.mem_read(QUERY, 0xb00))), before)
        self.assertEqual([e[0] for e in m.events],
                         ['lock', 'allocate', 'insert', 'notify', 'unlock', 'add_ui'])
        self.assertIn(('notify', 0x61706c69, m.added), m.events)

    def test_add_handles_empty_limit_boundary_full_and_allocation_failure(self):
        for count, fail, result in ((0, False, 1), (999, False, 1), (1000, False, 0),
                                    (65535, False, 0), (3, True, 0)):
            with self.subTest(count=count, fail=fail):
                m = self.make(count=count, fail=fail)
                self.assertEqual(self.invoke(m, -6), result)
                self.assertEqual(m.read(HEADER + 0x2c) >> 16, count + result)
                self.assertEqual(bytes(m.cpu.mem_read(PREFS + 0xb46, 1)), bytes([result]))
                self.assertEqual([e for e in m.events if e[0] in ('lock', 'unlock')],
                                 [('lock', PREFS), ('unlock', PREFS)])
                if not result:
                    self.assertFalse(any(e[0] in ('notify', 'insert', 'add_ui') for e in m.events))

    def test_add_and_remove_accept_playing_and_paused_selections(self):
        for transport in (1, 2):
            for playing_otg in (False, True):
                m = self.make(playing_otg)
                m.write(AUDIO + 0x2768, transport)
                self.assertEqual(self.invoke(m, -7 if playing_otg else -6), 1)
                self.assertEqual(m.read(AUDIO + 0x2768), 0 if playing_otg else transport)

    def test_stale_action_cannot_reverse_itself_or_mutate_wrong_queue(self):
        for playing, operation in ((False, -7), (True, -6)):
            m = self.make(playing)
            self.assertEqual(self.invoke(m, operation), 0)
            self.assertEqual(m.events, [])

    def test_track_and_context_are_reread_after_lock(self):
        for transition in ('track', 'queue', 'missing', 'lock'):
            m = self.make()
            new = CURRENT + 0x200
            m.cpu.mem_write(new, bytes(m.cpu.mem_read(CURRENT, 0x40)))
            m.write(new + 0x10, 99)
            def change():
                m.events.append(('lock', m.reg(0)))
                if transition == 'track':
                    m.write(AUDIO + 0x2a8, new)
                elif transition == 'queue':
                    state(m, playing_otg=True)
                elif transition == 'lock':
                    m.write(QUERY + 0xa70, PREFS + 0x100)
                else:
                    m.write(AUDIO + 0x2a8, 0)
            m.hook(0x98b48, change)
            self.assertEqual(self.invoke(m, -6), int(transition == 'track'))
            self.assertIn(('unlock', PREFS), m.events)
            if transition == 'track':
                self.assertEqual(m.read(m.added + 0x18), 99)
            else:
                self.assertFalse(any(e[0] == 'allocate' for e in m.events))

    def test_removal_notifies_audio_before_unlink_and_frees_exact_duplicate(self):
        for count in (1, 3, 1000):
            m = self.make(True, count=count)
            duplicate = CURRENT + 0x200
            m.cpu.mem_write(duplicate, bytes(m.cpu.mem_read(CURRENT, 0x40)))
            m.write(duplicate + 0x10, 99)
            untouched = bytes(m.cpu.mem_read(duplicate, 0x40))
            m.write(QUERY + 0xaac, 0x10059000, 3)
            self.assertEqual(self.invoke(m, -7), 1)
            self.assertEqual(bytes(m.cpu.mem_read(duplicate, 0x40)), untouched)
            self.assertEqual(m.read(HEADER + 0x2c) >> 16, count - 1)
            self.assertEqual(m.read(AUDIO + 0x2a8), 0)
            self.assertEqual(m.read(AUDIO + 0x2a4), 0)
            self.assertEqual(m.read(AUDIO + 0x2768), 0)
            self.assertEqual(m.read(QUERY + 0x44), OTG)
            self.assertEqual((m.read(QUERY + 0xaac), m.read(QUERY + 0xab0)), (0, 0))
            self.assertIn(0x10059000, m.freed)
            self.assertEqual(bytes(m.cpu.mem_read(PREFS + 0xb46, 1)), b'\x01')
            names = [e[0] for e in m.events]
            self.assertLess(names.index('notify'), names.index('decoder_stop'))
            self.assertLess(names.index('decoder_stop'), names.index('unlink'))
            self.assertLess(names.index('unlink'), names.index('free'))
            self.assertEqual(names[-1], 'remove_ui')

    def test_removal_matches_native_stock_path_after_row_resolution(self):
        stock, added = self.make(True), self.make(True)
        stock.hook(0x6e400, lambda: CURRENT)
        self.assertEqual(stock.call(0x50aec, QUERY, 2), 0)
        stock.call(0xe2d40, UI)
        self.assertEqual(self.invoke(added, -7), 1)
        self.assertEqual(added.events, stock.events)
        for at, size in ((QUERY, 0xb00), (AUDIO, 0x3000), (PREFS, 0xc00),
                         (CURRENT, 0x40), (HEADER, 0x40)):
            self.assertEqual(bytes(added.cpu.mem_read(at, size)), bytes(stock.cpu.mem_read(at, size)))

    def test_group_entry_or_cross_database_context_cannot_be_added_or_removed(self):
        for playing in (False, True):
            m = self.make(playing)
            m.cpu.mem_write(CURRENT + 0x1d, b'\x01')
            self.assertEqual(self.invoke(m, -7 if playing else -6), 0)
            self.assertEqual(m.events, [])
        m = self.make()
        m.write(QUEUE + 0xc, PREFS + 0x100)
        self.assertEqual(self.invoke(m, -6), 0)
        self.assertEqual(m.events, [])


class OTGMenus(unittest.TestCase):
    def test_real_center_event_dispatches_each_action_and_returns_to_now_playing(self):
        for playing, view, command in ((False, 0x6100, 2542), (True, 0x6107, 2543)):
            with patch.object(verify_actions, 'ScriptVM',
                              lambda image: ActionsVMDisplay(image, view=view)):
                m = menu_machine(self.image)
            state(m, playing_otg=playing, ui=0x10014000)
            mutation_services(m)
            m.prepare_renderer()
            m.call(0xdd19c, m.RENDERER, view, 0, 30, 300)
            m.run(2536, m.event)
            m.run(1888, m.event)
            m.call(0xc60ac, m.menu, 5)
            events = []
            del m.hooks[0xaba84]
            m.hook(0xab854, lambda: events.append(m.reg(0)) or 1)
            m.hook(0xd915c, m.free)
            m.hook(0xd9a50, m.free)
            m.call(0x1b518, m.RENDERER)
            self.assertEqual(len(events), 1)
            self.assertEqual(m.read(events[0] + 12), command * 4 + 2)
            self.assertEqual(m.call(0x80870, m.RENDERER, events[0]), 1)
            self.assertEqual(m.read(m.read(APP + 0x104) + 8), 2015 * 4 + 2)
            self.assertEqual(bytes(m.cpu.mem_read(PREFS + 0xb46, 1)), b'\x01')

    def test_both_views_construct_six_selectable_rows(self):
        for view, command in ((0x6100, 2542), (0x6107, 2543)):
            m = ActionsDisplay(self.image, view=view)
            self.assertEqual(m.read(m.menu + 0xa0), 6)
            m.prepare_renderer()
            m.call(0xdd19c, m.RENDERER, view, 0, 30, 300)
            m.call(0xd11c8, m.menu, 5)
            self.assertEqual(m.call(0x96fc4, m.menu), 5)
            item = m.call(0x6cebc, m.read(m.menu + 0xe8), 5)
            self.assertEqual(m.read(m.read(item + 0x10) + 8), command * 4 + 2)

    def test_open_chooses_context_and_success_pops_one_level(self):
        for playing, command in ((False, 2542), (True, 2543)):
            m = menu_machine(self.image)
            state(m, playing_otg=playing, ui=0x10014000)
            mutation_services(m)
            shown = []
            m.hook(m.method_address(3, 0x91), lambda: shown.append(m.reg(1)))
            self.assertEqual(m.run(2536, m.event), 0xc0000002)
            self.assertIn(0x6107 if playing else 0x6100, shown)
            self.assertEqual(m.read(m.read(APP + 0x104) + 8), 2537 * 4 + 2)
            self.assertEqual(m.run(command, m.event), 0xc0000002)
            self.assertEqual(m.read(m.read(APP + 0x104) + 8), 2015 * 4 + 2)
            self.assertEqual(m.bindings[2, 0x6f], 2536 * 4 + 2)

    def test_failure_and_stale_menu_stay_open_without_mutation(self):
        for playing, command in ((False, 2542), (False, 2543), (True, 2542)):
            m = menu_machine(self.image)
            state(m, playing_otg=playing, ui=0x10014000, count=1000)
            mutation_services(m)
            headings = []
            m.hook(m.method_address(2, 0x60), lambda: headings.append(
                (m.reg(1), m.reg(2), m.reg(3))) or 0)
            self.assertEqual(m.run(2536, m.event), 0xc0000002)
            self.assertEqual(m.run(command, m.event), 0xc0000002)
            self.assertEqual(headings[-1], (0x261f, 0x6132, 0x80))
            self.assertEqual(directory(self.image)['Str '][1][0x6132], b'On-The-Go Unavailable\0')
            self.assertEqual(m.read(m.read(APP + 0x104) + 8), 2537 * 4 + 2)
            self.assertFalse(any(e[0] in ('insert', 'unlink', 'free') for e in m.events))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--resources-only', action='store_true')
    args = parser.parse_args()
    OTGResources.image = OTGContext.image = OTGMutation.image = OTGMenus.image = load_image(args.candidate)
    unittest.main(argv=[__file__], defaultTest='OTGResources' if args.resources_only else None,
                  verbosity=2)
