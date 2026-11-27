"""Playlist cache invalidation equivalence and cost, executing native ARM.

Destructors are supplied ownership boundaries; index traversal and field clearing
execute actual native code. Exact-package release tests replace the build setup.
"""
import struct
import tempfile
import unittest
from pathlib import Path

from unicorn import UC_HOOK_CODE
from unicorn import arm_const as arm
from build import compose
from image import load_stock
from research_actions import Machine

QUEUE = 0x10040000
BASES = (0x174, 0x238, 0x2fc, 0x3c0, 0x494)
SPECIAL = {6: 0x488, 7: 0x484, 9: 0x48c, 24: 0x490}


class PlaylistCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='ipod55g-playlist-cache-')
        stock = load_stock(Path('iPod_25.1.3.ipsw'))
        cls.stock = stock.osos
        cls.image, cls.symbols, _ = compose(stock, Path(cls.directory.name))

    @classmethod
    def tearDownClass(cls): cls.directory.cleanup()

    def original(self):
        data = bytearray(self.image)
        struct.pack_into('<I', data, 0x1137c4, 0xe92d4070)
        return bytes(data)

    def machine(self, image, values=None, callback=None):
        m = Machine(image)
        for offset, value in (values or {}).items(): m.write(QUEUE+offset, value)
        m.destroyed = []
        def destroy():
            m.destroyed.append((m.reg(0), m.reg(1)))
            if callback: callback(m, m.reg(0))
            return 0xface0123
        # Native 964ec/11460c/a7f9c wrappers execute; supply only heap release.
        m.hook(0x11ab70, destroy)
        return m

    def execute(self, m):
        count = [0]
        hook = m.cpu.hook_add(UC_HOOK_CODE,
                             lambda *args: count.__setitem__(0, count[0]+1))
        value = m.call(0x1137c4, QUEUE)
        m.cpu.hook_del(hook)
        return value, count[0]

    def test_empty_playlist_skips_redundant_native_cleanup_work(self):
        counts = []
        for image in (self.original(), self.image):
            m = self.machine(image)
            value, count = self.execute(m)
            self.assertEqual(value, 0)
            self.assertEqual(m.destroyed, [])
            self.assertEqual(bytes(m.cpu.mem_read(QUEUE, 0x600)), bytes(0x600))
            counts.append(count)
        self.assertLess(counts[1], counts[0]*0.60, counts)

    def compare(self, values, callback=None):
        before = self.machine(self.original(), values, callback)
        after = self.machine(self.image, values, callback)
        a, _ = self.execute(before)
        b, _ = self.execute(after)
        self.assertEqual(a, b)
        self.assertEqual(before.destroyed, after.destroyed)
        self.assertEqual(bytes(before.cpu.mem_read(QUEUE, 0x600)),
                         bytes(after.cpu.mem_read(QUEUE, 0x600)))
        return after

    def test_every_cache_slot_releases_ownership_and_preserves_other_fields(self):
        guards = {offset: 0xabad0000+offset for offset in
                  (0, 8, 0x174, 0x238, 0x2fc, 0x3c0, 0x494, 0x558, 0x5fc)}
        slots = [base+4*index for index in range(1, 49) for base in BASES]
        slots += list(SPECIAL.values())
        for offset in slots:
            with self.subTest(offset=hex(offset)):
                value = 0x13000000+offset
                m = self.compare({**guards, offset: value})
                self.assertEqual(m.read(QUEUE+offset), 0)
                self.assertEqual(m.destroyed, [(value, 4)])
                for at, expected in guards.items():
                    self.assertEqual(m.read(QUEUE+at), expected)

    def test_populated_and_sparse_indexes_preserve_destructor_order(self):
        import random
        rng = random.Random(5508)
        slots = [base+4*index for index in range(1, 49) for base in BASES]
        slots += list(SPECIAL.values())
        for mask in ([True]*len(slots), *[[rng.randrange(4)==0 for _ in slots]
                                        for _ in range(32)]):
            values = {at: 0x13000000+at for at, present in zip(slots, mask) if present}
            m = self.compare(values)
            self.assertEqual(len(m.destroyed), len(values))
            self.assertTrue(all(m.read(QUEUE+at)==0 for at in values))

    def test_cleanup_mutation_is_seen_in_native_order(self):
        # Cleanup may change later fields. A whole-object snapshot or batching
        # would skip newly populated caches or clear an already-passed index.
        first, second, later, special, earlier = range(0x13000000, 0x13000014, 4)
        def mutate(m, value):
            if value == first:
                m.write(QUEUE+0x238+3*4, second)
                m.write(QUEUE+0x174+5*4, later)
                m.write(QUEUE+0x490, special)
                m.write(QUEUE+0x174+1*4, earlier)
        m = self.compare({0x174+3*4: first}, mutate)
        self.assertEqual(m.destroyed, [(first, 4), (second, 4), (later, 4), (special, 4)])
        self.assertEqual(m.read(QUEUE+0x174+1*4), earlier)

    def test_nonzero_callee_saved_registers_survive(self):
        m = self.machine(self.image, {0x494+48*4: 0x13000000})
        registers = [getattr(arm, f'UC_ARM_REG_R{i}') for i in range(4, 12)]
        values = [0xabba0000+i for i in range(8)]
        def seed(cpu, pc, size, context):
            for register, value in zip(registers, values): cpu.reg_write(register, value)
        handle = m.cpu.hook_add(UC_HOOK_CODE, seed, begin=0x1137c4, end=0x1137c4)
        value, _ = self.execute(m)
        m.cpu.hook_del(handle)
        self.assertEqual(value, 0xface0123)
        self.assertEqual([m.cpu.reg_read(r) for r in registers], values)

    def test_queue_clone_preserves_filters_order_selection_and_native_notifications(self):
        from test_actions_query_native import ActionsQueryNative
        from test_actions_native import QUEUE as SOURCE
        from research_actions import QUERY
        rows = [('Artist', 'Alpha'), ('Artist', 'Beta'), ('Other', 'Alpha'),
                ('Other', 'Gamma'), ('Artist', 'Alpha'), ('Other', 'Beta')]
        header, storage, selected, clones = 0x10041000, 0x10042000, 0x10043000, 0x10044000
        for album, reverse, shuffle, selected_row, fail_at in (
            ('', 0, 0, 0, None), ('', 0, 0, 5, None), ('', 1, 0, 2, None),
            ('Alpha', 0, 0, 1, None), ('Alpha', 1, 0, 0, None),
            ('Missing', 0, 0, 0, None), ('', 0, 1, 2, None),
            ('', 0, 0, 2, 2),
        ):
            with self.subTest(album=album, reverse=reverse, shuffle=shuffle,
                              selected=selected_row, allocation_failure=fail_at):
                outcomes = []
                for image in (self.original(), self.image):
                    fixture = ActionsQueryNative()
                    fixture.image = image
                    m = fixture.vm()
                    entries = fixture.library(m, rows)
                    for i, entry in enumerate(entries):
                        m.write(entry+0x10, i+1)
                        # Skip-when-shuffling; selected row must still survive.
                        if i in (1, 2):
                            m.cpu.mem_write(m.read(entry+0x20)+0x8b, b'\x02')
                    m.call(0xb29b0, QUERY, 0)
                    if album:
                        m.cpu.mem_write(QUERY+0x628, struct.pack('<H', len(album))+album.encode('utf-16le'))
                    m.call(0xcc7a8, QUERY)
                    m.write(QUEUE+0xc, m.read(SOURCE+0xc))
                    m.write(QUEUE+8, storage)
                    m.write(QUEUE+0x28, header)
                    m.write(header, QUEUE)
                    # Same native notifier and query listener as construction.
                    self.assertEqual(m.call(0x941d8, QUEUE+0x30), 0)
                    self.assertEqual(m.call(0x94184, QUERY+0xca4, 0x110110, QUERY), 0)
                    self.assertEqual(m.call(0xa6b0c, QUEUE+0x30, QUERY+0xca4), 0)
                    allocations, notifications = [], []
                    def allocate():
                        self.assertEqual(m.reg(0), QUEUE)
                        index = len(allocations)
                        allocations.append(index)
                        if index == fail_at: return 0
                        at = clones+index*0x40
                        m.write(at, QUEUE)
                        m.write(at+0x10, 10000+index)
                        return at
                    m.hook(0xd1df0, allocate)
                    m.hook(0xb3ee0, lambda: 0)
                    def notify(cpu, pc, size, context):
                        notifications.append((m.reg(0), m.reg(1)))
                    handle = m.cpu.hook_add(UC_HOOK_CODE, notify, begin=0x110110, end=0x110110)
                    m.call(0x9388c, QUERY, SOURCE, 1, reverse, QUEUE, shuffle, selected_row, selected)
                    m.cpu.hook_del(handle)
                    order, node = [], m.read(header+0x20)
                    while node:
                        self.assertLess(len(order), len(rows))
                        order.append(m.read(node+0x18))
                        node = m.read(node+8)
                    count = struct.unpack('<H', m.cpu.mem_read(header+0x32, 2))[0]
                    self.assertEqual(count, len(order))
                    self.assertEqual(len(notifications), count)
                    self.assertTrue(all(event == 0x61706c69 for event, _ in notifications))
                    chosen = m.read(selected)
                    outcomes.append((order, m.read(chosen+0x18) if chosen else 0,
                                     notifications, bytes(m.cpu.mem_read(QUEUE, 0x600)),
                                     bytes(m.cpu.mem_read(header, 0x100)),
                                     bytes(m.cpu.mem_read(QUERY, 0xd30))))
                self.assertEqual(outcomes[0], outcomes[1])
                matching = [i+1 for i, row in enumerate(rows) if not album or row[1] == album]
                if reverse: matching.reverse()
                chosen_id = matching[selected_row] if selected_row < len(matching) else 0
                attempted = [ident for i, ident in enumerate(matching)
                             if not shuffle or i == selected_row or ident not in (2, 3)]
                expected = [ident for i, ident in enumerate(attempted) if i != fail_at]
                self.assertEqual(outcomes[1][0], expected)
                self.assertEqual(outcomes[1][1], chosen_id if chosen_id in expected else 0)

    def test_patch_rejects_changed_native_profile_or_bad_target(self):
        import playlist_cache
        for at in (0x1137c4, 0x1137ec, 0x110208, 0x110274, 0x1102f0, 0x110304):
            image = bytearray(self.original())
            image[at] ^= 1
            with self.subTest(address=hex(at)), self.assertRaises(ValueError):
                playlist_cache.apply(image, self.symbols)
        for target in (0, 0x7358c1, len(self.image)):
            symbols = {**self.symbols, 'ipod55g_playlist_invalidate': target}
            with self.subTest(target=hex(target)), self.assertRaises(ValueError):
                playlist_cache.apply(bytearray(self.original()), symbols)


if __name__ == '__main__': unittest.main(verbosity=2)
