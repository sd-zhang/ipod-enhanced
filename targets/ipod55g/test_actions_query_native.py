"""Artist shortcut regressions using the native query, index and ScriptVM.

Metadata storage, playlist enumeration, allocation and display/OS boundaries are
supplied. Metadata getters, UTF-16 comparison, the query predicate, album index,
count/selection methods and scripts execute ARM. Run with Unicorn JIT permission.
No device access.
"""
import struct
import unittest

from unicorn import arm_const as arm
import actions
import test_actions_native as native_actions
from research_actions import APP, UI, QUERY

DB = 0x100d0000
TEXT = 0x100ee000
ARTIST = 'Café 🎵'
TRUE = 0xc0000002


class ActionsQueryNative(unittest.TestCase):
    # Reuse the existing source build and navigation fixture without inheriting
    # its tests. verify_release can replace setUpClass with the packaged image.
    setUpClass = classmethod(native_actions.ActionsNative.setUpClass.__func__)
    machine = native_actions.ActionsNative.machine
    vm = native_actions.ActionsNative.vm
    selector_hook = native_actions.ActionsNative.selector_hook
    history = native_actions.ActionsNative.history
    history_chain = native_actions.ActionsNative.history_chain

    def library(self, m, rows, current=0):
        entries, records = [], []
        pools = {DB+0xe0: {}, DB+0x138: {}}
        string_at = 0x100e0000
        for index, (artist, album) in enumerate(rows):
            record = 0x100d3000+index*0x200
            entry = 0x100d9000+index*0x40
            m.write(record, DB)
            m.write(record+0x2c, index+1)
            m.write(record+0x34, index+1)
            m.write(entry, native_actions.QUEUE)
            m.write(entry+0x20, record)
            for pool, name in ((DB+0xe0, artist), (DB+0x138, album)):
                data = name.encode('utf-16le')
                m.cpu.mem_write(string_at, data)
                pools[pool][index+1] = (string_at, data)
                string_at += 0x200
            entries.append(entry)
            records.append(record)
        m.write(QUERY+0xcf8, DB)
        # Source zero's unselected Songs list uses the native library playlist
        # fallback. Keep its raw ordered entries as well as album enumeration.
        m.write(QUERY+0xce4, native_actions.QUEUE)
        m.write(native_actions.ORDER+0x10, *entries)
        album_order = sorted(range(len(rows)), key=lambda i: rows[i][1])
        m.write(native_actions.QUEUE+0x314, native_actions.ORDER+0x200)
        m.write(native_actions.ORDER+0x210, *(entries[i] for i in album_order))
        m.cpu.mem_write(native_actions.HEADER+0x32, struct.pack('<H', len(entries)))
        m.write(UI+0x28, 0x676608, records[current], QUERY, 1)
        m.hook(0x255c60, lambda: 1)  # Current-record generation is valid.
        m.hook(0xa8178, m.allocate)
        m.hook(0xa7f9c, m.free)
        m.hook(0xa7200, lambda: 0)  # String storage lock/unlock.
        m.hook(0xb9f20, lambda: 0)

        def sorted_entry():
            # Native album indexing requests playlist sort selector 6.
            self.assertEqual(m.reg(1), 6)
            return entries[album_order[m.reg(3)]] if m.reg(3) < len(rows) else 0

        def storage_pointer():
            pointer, data = pools[m.reg(0)][m.reg(1)]
            m.write(m.reg(2), pointer)
            m.write(m.reg(3), len(data))
            return 0

        def storage_copy():
            _, data = pools[m.reg(0)][m.reg(1)]
            maximum = m.read(m.cpu.reg_read(arm.UC_ARM_REG_SP))
            data = data[:maximum]
            m.cpu.mem_write(m.reg(2), data)
            m.write(m.reg(3), len(data))
            return 0

        def reset_entry_cache():
            # Native a37a8 has already cleared the playlist cache-valid flag;
            # this is its storage-list traversal callback boundary.
            for entry in entries:
                m.cpu.mem_write(entry+0x1c, b'\0')
            return 0

        m.hook(0x9c8e4, sorted_entry)
        m.hook(0x9efe0, storage_pointer)
        m.hook(0x9eff8, storage_copy)
        m.hook(0xb79e0, reset_entry_cache)
        return entries

    def query_vm(self, rows):
        m = self.vm()
        history = self.history(m)
        entries = self.library(m, rows)
        # The history fixture normally replaces every music selector. Retain
        # actual album refresh/count and browse-context behavior for these tests.
        for address in (0x1e90cc, 0x1e7728, 0x1e7628, 0x1e7f08):
            m.hooks.pop(address, None)
        m.hook(m.read(0x670fbc+0x54), lambda: 0)  # Display property notification.
        for atom in (4002, 4202, 4244, actions.SCREEN):
            m.call(0x1d27e8, APP, atom*4+2)
        return m, history, entries

    def text(self, m, address):
        count = struct.unpack('<H', m.cpu.mem_read(address, 2))[0]
        self.assertLessEqual(count, 255)
        return bytes(m.cpu.mem_read(address+2, count*2)).decode('utf-16le')

    def album_names(self, m):
        count = m.call(0x1e7728, UI)
        names = []
        for index in range(count):
            self.assertEqual(m.call(0x9c9fc, QUERY, index, TEXT), 0)
            names.append(self.text(m, TEXT))
        return names

    def test_artist_single_album_stays_on_artist_albums(self):
        m, history, _ = self.query_vm([(ARTIST, 'Alpha'), ('Other', 'Gamma')])
        self.assertEqual(m.run(actions.ARTIST, 0), TRUE)
        self.assertEqual(self.history_chain(m, history), [4184, actions.SCREEN, 4244, 4002])
        self.assertEqual(m.read(UI+0xc4),2) # One album plus the native All row.
        self.assertEqual(self.album_names(m), ['Alpha'])
        self.assertEqual(self.text(m, QUERY+0x228), ARTIST)
        self.assertEqual(self.text(m, QUERY+0x628), '')
        self.assertEqual(m.cpu.mem_read(UI+0x48d, 1), b'\x05')

    def test_artist_multiple_albums_excludes_other_artists(self):
        rows = [(ARTIST, 'Alpha'), (ARTIST, 'Beta'), ('Other', 'Beta'), ('Other', 'Gamma')]
        m, history, entries = self.query_vm(rows)
        playing = bytes(m.cpu.mem_read(native_actions.PLAYING, 0x30))
        self.assertEqual(m.run(actions.ARTIST, 0), TRUE)
        self.assertEqual(self.history_chain(m, history), [4184, actions.SCREEN, 4244, 4002])
        self.assertEqual(m.read(UI+0xc4),3) # Refresh must precede screen opening.
        self.assertEqual(self.album_names(m), ['Alpha', 'Beta'])
        self.assertEqual([m.call(0x1142a8, QUERY, e, 0) for e in entries], [1, 1, 0, 0])
        self.assertEqual(bytes(m.cpu.mem_read(native_actions.PLAYING, 0x30)), playing)

    def test_artist_album_selection_opens_filtered_songs(self):
        rows = [(ARTIST, 'Alpha'), (ARTIST, 'Beta'), ('Other', 'Beta'), ('Other', 'Gamma')]
        m, history, entries = self.query_vm(rows)
        self.assertEqual(m.run(actions.ARTIST, 0), TRUE)
        self.assertEqual(self.album_names(m), ['Alpha', 'Beta'])
        # Native All occupies row 0, so Beta is row 2. Execute the stock row
        # command, including the unhooked album selector, then the Songs script.
        self.selector_hook(m, 3, 0xc7, lambda: 2)
        self.assertEqual(m.run(4185, 0), TRUE)
        self.assertEqual(self.history_chain(m, history), [4164, 4184, actions.SCREEN, 4244, 4002])
        self.assertEqual(self.text(m, QUERY+0x228), ARTIST)
        self.assertEqual(self.text(m, QUERY+0x628), 'Beta')
        self.assertEqual([m.call(0x1142a8, QUERY, e, 0) for e in entries], [0, 1, 0, 0])
        self.assertEqual(m.cpu.mem_read(UI+0x48d, 1), b'\x09')
        self.assertEqual(m.run(3973, 0), TRUE)
        self.assertEqual(self.history_chain(m, history), [4184, actions.SCREEN, 4244, 4002])
        self.assertEqual(m.run(3973, 0), TRUE)
        self.assertEqual(self.history_chain(m, history), [actions.SCREEN, 4244, 4002])

    def test_artist_menu_returns_to_actions_without_old_library_parent(self):
        m, history, _ = self.query_vm([(ARTIST, 'Alpha'), (ARTIST, 'Beta'), ('Other', 'Gamma')])
        self.assertEqual(m.run(actions.ARTIST, 0), TRUE)
        self.assertEqual(m.run(3973, 0), TRUE)
        self.assertEqual(self.history_chain(m, history), [actions.SCREEN, 4244, 4002])
        self.assertEqual(m.run(3969, 0), TRUE)
        self.assertEqual(self.history_chain(m, history), [4002])

    def test_repeated_album_and_artist_use_native_query_with_real_callbacks(self):
        rows = [(ARTIST, 'Alpha'), (ARTIST, 'Beta'), ('Other', 'Alpha'), ('Other', 'Gamma')]
        m, history, entries = self.query_vm(rows)
        m.instruction_limit = 1000000
        m.hooks.pop(0x140384)
        playing = bytes(m.cpu.mem_read(native_actions.PLAYING, 0x30))
        # The first command normalizes old library parents. Later commands
        # reuse the now-safe chain while still resetting the actual query.
        for command, leaf, predicate in ((actions.ARTIST, 4184, [1, 1, 0, 0]),
                                        (actions.ALBUM, 4164, [1, 0, 1, 0]),
                                        (actions.ARTIST, 4184, [1, 1, 0, 0])):
            self.assertEqual(m.run(command, 0), TRUE)
            self.assertEqual(self.history_chain(m, history), [leaf, actions.SCREEN, 4244, 4002])
            self.assertEqual([m.call(0x1142a8, QUERY, e, 0) for e in entries], predicate)
            if command == actions.ALBUM:
                self.assertEqual(m.read(UI+0xc0), 2)
                index = m.read(QUERY+0xc88)
                self.assertEqual([m.read(index+4*i) for i in range(2)], [entries[0], entries[2]])
                self.assertEqual(self.text(m, QUERY+0x228), '')
                self.assertEqual(self.text(m, QUERY+0x628), 'Alpha')
            else:
                self.assertEqual(self.album_names(m), ['Alpha', 'Beta'])
                self.assertEqual(m.read(UI+0xc4), 3)
                self.assertEqual(self.text(m, QUERY+0x228), ARTIST)
                self.assertEqual(self.text(m, QUERY+0x628), '')
            self.assertEqual(bytes(m.cpu.mem_read(native_actions.PLAYING, 0x30)), playing)
            self.assertEqual(m.run(3973, 0), TRUE)
            self.assertEqual(self.history_chain(m, history), [actions.SCREEN, 4244, 4002])
        self.assertEqual(m.run(3969, 0), TRUE)
        self.assertEqual(self.history_chain(m, history), [4002])


if __name__ == '__main__':
    unittest.main(verbosity=2)
