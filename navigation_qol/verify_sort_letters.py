"""Execute host-sort import and index hooks in the packaged ARM firmware.

Uses supplied stream/allocator boundaries; this is not a device emulator.
Unicorn must run outside the macOS sandbox, using .venv/bin/python.
"""
import argparse
import json
import struct
import sys
import unittest
from pathlib import Path

from native_fixture import Library, NativeMachine, QUERY, load_image
from unicorn import arm_const as arm
from verify_native import REG, STACK

READER, STREAM, TRACK = 0x11000000, 0x11050000, 0x10040000
GROUP = {23: 0xfc, 28: 0xfd, 30: 0xfe}


def text_payload(text, endian='<', encoding=1):
    data = text.encode(('utf-16le' if endian == '<' else 'utf-16be')
                       if encoding == 1 else 'utf-8')
    return struct.pack(endian + '4I', encoding, len(data), 0, 0) + data


def attach_stream(m, map_reader=True):
    if map_reader:
        m.cpu.mem_map(READER, 0x60000)
    m.write(READER + 0x40000, STREAM)
    m.stream_data, m.stream_position, m.stream_error = b'', 0, 0
    m.stream_reads = 0

    def read():
        assert m.reg(0) == READER
        count = m.reg(2)
        assert count <= 0x40000
        m.stream_reads += 1
        if m.stream_error:
            return m.stream_error
        end = m.stream_position + count
        assert end <= len(m.stream_data), 'read escaped metadata record'
        if count:
            m.cpu.mem_write(m.reg(1), m.stream_data[m.stream_position:end])
        m.stream_position = end
        return 0

    def skip():
        assert m.reg(0) == STREAM
        if m.stream_error:
            return m.stream_error
        m.stream_position += m.reg(1)
        assert m.stream_position <= len(m.stream_data), 'skip escaped metadata record'
        return 0

    m.hook(0x4d208, read)
    m.hook(0x316ac, skip)


def import_sort(m, kind, payload, endian='<', track=TRACK):
    """Original dispatch, patched unknown-mhod handler, original status join."""
    m.stream_data, m.stream_position = payload, 0
    m.cpu.mem_write(READER + 0x40083, bytes([endian == '>']))
    m.write(STACK + 0x410, 24, 24 + len(payload), kind)
    for i in range(13):
        m.cpu.reg_write(REG[i], 0x12000000 + i)
    m.cpu.reg_write(REG[4], track)
    m.cpu.reg_write(REG[6], READER)
    m.cpu.reg_write(REG[7], READER + 0x40000)
    preserved = [m.reg(i) for i in range(4, 13)]
    m.cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
    m.cpu.emu_start(0xb6c10, 0xb6ed8, count=2000000)
    assert m.cpu.reg_read(arm.UC_ARM_REG_PC) == 0xb6ed8
    assert m.cpu.reg_read(arm.UC_ARM_REG_SP) == STACK
    assert [m.reg(i) for i in range(4, 13)] == preserved
    return m.reg(0)


def attach_native_stream(m, capacity):
    """Keep the real firmware buffering/read/skip; supply only OS file reads."""
    buffer = 0x11100000
    m.cpu.mem_map(buffer, 0x80000)
    del m.hooks[0x4d208]
    del m.hooks[0x316ac]
    m.cpu.mem_write(STREAM + 5, b'\x01')
    m.write(STREAM + 8, 0xabc)
    m.write(STREAM + 0x10, capacity, buffer, buffer, buffer - 1, 0, 0)
    m.file_position = 0
    m.sentinel = b'next record sentinel'

    def read():
        assert m.reg(0) == 0xabc
        count = m.read(m.reg(1))
        raw = m.stream_data + m.sentinel
        data = raw[m.file_position:m.file_position + count]
        if data:
            m.cpu.mem_write(m.reg(2), data)
        m.write(m.reg(1), len(data))
        m.file_position += len(data)
        return 0 if len(data) == count else 0xffffffd9

    m.hook(0x94768, read)


class SortLetters(unittest.TestCase):
    def machine(self):
        m = NativeMachine(self.image)
        attach_stream(m)
        return m

    def test_articles_and_custom_sort_names_reach_native_album_index(self):
        m = Library(self.image, [('The Life of Pablo', False),
                                ('A Hat in Time OST', False), ('Custom title', False)])
        attach_stream(m)
        for i, sort in enumerate(('Life of Pablo', 'Hat in Time OST', 'Zebra')):
            self.assertEqual(import_sort(m, 28, text_payload(sort), track=TRACK + i * 256), 0)
        self.assertEqual(m.build_index(2), ['The Life of Pablo', 'A Hat in Time OST', 'Custom title'])
        pointer, count = m.read(QUERY + 0xa9c), m.read(QUERY + 0xaa0)
        self.assertEqual(bytes(m.cpu.mem_read(pointer + 4 * count, count)), b'LHZ')

    def test_marker_matches_observed_host_order_for_symbol_prefixed_names(self):
        # These positions come from the saved mhod52 permutation, not from
        # the old 5G first-character fallback used as the previous oracle.
        names = ['Gliese 667', '#Goldrush, Vol. 1', 'Groove Coaster',
                 'Portable Memories', '(R)Evolution: The Remixes', 'Rakuen']
        m = Library(self.image, [(name, False) for name in names])
        self.assertEqual(m.build_index(2), names)
        pointer, count = m.read(QUERY + 0xa9c), m.read(QUERY + 0xaa0)
        self.assertEqual(bytes(m.cpu.mem_read(pointer + 4 * count, count)), b'GGGPRR')

    def test_leading_punctuation_symbols_and_spaces_use_next_sort_character(self):
        names = ['#Goldrush', '(R)Evolution', '  [\"Éclair\"]', '℃iel',
                 '-夢幻交響詩 福寿草-', '-45', '123 Go', '#(  )', '😀Alpha']
        expected = b'GREI!###A'
        for mask, offset in ((2, 0xa9c), (4, 0xa8c), (0x40, 0xa84), (0x4000000, 0xa94)):
            with self.subTest(mask=mask):
                m = Library(self.image, [(name, False) for name in names])
                m.build_index(mask)
                pointer, count = m.read(QUERY + offset), m.read(QUERY + offset + 4)
                self.assertEqual(bytes(m.cpu.mem_read(pointer + 4 * count, count)), expected)

    def test_host_sort_strings_use_the_same_prefix_rule_in_both_encodings(self):
        for endian in ('<', '>'):
            for encoding in (1, 2):
                with self.subTest(endian=endian, encoding=encoding):
                    m = Library(self.image, [('Custom One', False), ('Custom Two', False),
                                            ('Custom Three', False), ('Custom Four', False)])
                    attach_stream(m)
                    for i, sort in enumerate(('#Goldrush', '(R)Evolution', '℃iel', '😀Alpha')):
                        self.assertEqual(import_sort(m, 28, text_payload(sort, endian, encoding),
                                                     endian, TRACK + 256 * i), 0)
                    m.build_index(2)
                    pointer, count = m.read(QUERY + 0xa9c), m.read(QUERY + 0xaa0)
                    self.assertEqual(bytes(m.cpu.mem_read(pointer + 4 * count, count)), b'GRIA')

    def test_emoji_presentation_and_joiners_stay_with_ignored_prefix(self):
        names = ['❤Alpha', '❤️Alpha', '👩Alpha', '👩\u200d💻Alpha', '\u200bAlpha']
        for encoding in (None, 1, 2):
            for endian in ('<', '>'):
                with self.subTest(encoding=encoding, endian=endian):
                    m = Library(self.image, [(name if encoding is None else f'Custom{i}', False)
                                             for i, name in enumerate(names)])
                    if encoding is not None:
                        attach_stream(m)
                        for i, name in enumerate(names):
                            self.assertEqual(import_sort(m, 28, text_payload(name, endian, encoding),
                                                         endian, TRACK + 256 * i), 0)
                    m.build_index(2)
                    pointer, count = m.read(QUERY + 0xa9c), m.read(QUERY + 0xaa0)
                    self.assertEqual(bytes(m.cpu.mem_read(pointer + 4 * count, count)), b'A' * count)

    def test_unicode_prefix_classification_at_every_generated_range_boundary(self):
        import re
        import unicodedata
        from research_letters import Reference, reference_image
        reference = Reference(reference_image(Path('iPod_25.1.3.ipsw')))
        source = (Path(__file__).parent / 'sort_prefix.h').read_text()
        points = set(range(128))
        for start, end in re.findall(r'\{0x([0-9a-f]+), 0x([0-9a-f]+)\}', source):
            for value in (int(start, 16), int(end, 16) & 0xffffff):
                points.update((value - 1, value, value + 1))
        m = self.machine()
        for value in sorted(points):
            if not 0 <= value <= 0x10ffff or 0xd800 <= value <= 0xdfff:
                continue
            char = chr(value)
            category = unicodedata.category(char)
            ignored = category[0] in 'PSZ' or category in ('Cf', 'Mn') or char in '\t\n\v\f\r'
            expected = ord('A') if ignored else ord(reference.group(char))
            raw = (char + 'Alpha').encode('utf-16le')
            m.cpu.mem_write(0x10030000, raw)
            actual = m.call(self.symbols['letters_track_group'], 0x10030000, len(raw) // 2, TRACK, 6)
            self.assertEqual(actual, expected, hex(value))

    def test_endian_encodings_and_character_groups(self):
        m = self.machine()
        for endian in ('<', '>'):
            for encoding in (1, 2):
                for name, group in [('  Life', b'L'), ('Éclair', b'E'), ('9 Lives', b'#'),
                                    ('中', b'!'), ('😀', b'#'), ('Ω', b'#')]:
                    for kind, slot in GROUP.items():
                        with self.subTest(endian=endian, encoding=encoding, name=name, kind=kind):
                            m.cpu.mem_write(TRACK, bytes(256))
                            data = text_payload(name, endian, encoding)
                            self.assertEqual(import_sort(m, kind, data, endian), 0)
                            self.assertEqual(bytes(m.cpu.mem_read(TRACK + slot, 1)), group)
                            self.assertEqual(m.stream_position, len(data))

    def test_invalid_or_empty_sort_strings_fall_back_without_losing_stream_position(self):
        m = self.machine()
        invalid = [b'', b'x' * 15, text_payload(''), text_payload('    '),
                   struct.pack('<4I', 3, 2, 0, 0) + b'AB',
                   struct.pack('<4I', 1, 9, 0, 0) + b'A\0',
                   struct.pack('<4I', 1, 1, 0, 0) + b'A',
                   b'x' * (0x40000 + 1)]
        for data in (b'\xc0\xaf', b'\xe2\x82', b'\xed\xa0\x80', b'\xf4\x90\x80\x80', b'\x80'):
            invalid.append(struct.pack('<4I', 2, len(data), 0, 0) + data)
        for payload in invalid:
            with self.subTest(length=len(payload), prefix=payload[:24]):
                m.cpu.mem_write(TRACK, bytes(256))
                self.assertEqual(import_sort(m, 28, payload), 0)
                self.assertEqual(m.read(TRACK + 0xfc), 0)
                self.assertEqual(m.stream_position, len(payload))

    def test_padded_record_and_scratch_limit_do_not_touch_importer_state(self):
        m = self.machine()
        for payload in (text_payload('Zebra') + bytes(27), text_payload('Z') + bytes(0x40000 - 18)):
            self.assertEqual(import_sort(m, 28, payload), 0)
            self.assertEqual(m.read(READER + 0x40000), STREAM)
            self.assertEqual(bytes(m.cpu.mem_read(TRACK + 0xfd, 1)), b'Z')
            self.assertEqual(m.stream_position, len(payload))

    def test_malformed_leading_utf16_surrogates_use_display_fallback(self):
        for endian in ('<', '>'):
            for units in ((0xd800,), (0xdc00,), (0xd800, 0x41), (0x20, 0xd800)):
                with self.subTest(endian=endian, units=units):
                    m = Library(self.image, [('Zebra', False), ('Zulu', False)])
                    attach_stream(m)
                    raw = struct.pack(endian + f'{len(units)}H', *units)
                    data = struct.pack(endian + '4I', 1, len(raw), 0, 0) + raw
                    self.assertEqual(import_sort(m, 28, data, endian), 0)
                    m.build_index(2)
                    pointer, count = m.read(QUERY + 0xa9c), m.read(QUERY + 0xaa0)
                    self.assertEqual(bytes(m.cpu.mem_read(pointer + 4 * count, count)), b'ZZ')

    def test_unknown_types_keep_native_skip_and_io_errors_propagate(self):
        m = self.machine()
        m.cpu.mem_write(TRACK + 0xfc, b'ABC\0')
        for kind in (0, 18, 22, 29, 53, 0xffffffff):
            self.assertEqual(import_sort(m, kind, b'arbitrary ignored metadata'), 0)
            self.assertEqual(bytes(m.cpu.mem_read(TRACK + 0xfc, 4)), b'ABC\0')
        self.assertEqual(m.stream_reads, 0)
        for kind in (28, 27):
            m.stream_error = 0xffffff30
            self.assertEqual(import_sort(m, kind, text_payload('Zebra')), m.stream_error)
            self.assertEqual(bytes(m.cpu.mem_read(TRACK + 0xfc, 4)), b'ABC\0')

    def test_filters_deduplication_and_all_four_index_kinds(self):
        for mask, offset, kind, expected in ((4, 0xa8c, 23, b'BZ'), (2, 0xa9c, 28, b'XBZ'),
                                          (0x4000000, 0xa94, 30, b'XBZ'), (0x40, 0xa84, 28, b'TTT')):
            with self.subTest(mask=mask):
                m = Library(self.image, [('The Absent', True), ('The Alpha', False),
                                        ('The Alpha', True), ('The Beta', False)])
                attach_stream(m)
                for i, sort in enumerate(('Xylophone', 'Bravo', 'Bravo', 'Zulu')):
                    self.assertEqual(import_sort(m, kind, text_payload(sort), track=TRACK + 256 * i), 0)
                m.build_index(mask)
                pointer, count = m.read(QUERY + offset), m.read(QUERY + offset + 4)
                self.assertEqual(bytes(m.cpu.mem_read(pointer + 4 * count, count)), expected)

    def test_native_full_track_import_preserves_gapless_and_handles_next_record(self):
        m = self.machine()
        header = bytearray(0xf4)
        fields = (291, 0x76543210, 2, 1110)
        children = b''
        for kind, name in ((28, 'Life of Pablo'), (23, 'West'), (30, 'Composer')):
            data = text_payload(name)
            children += b'mhod' + struct.pack('<5I', 24, 24 + len(data), kind, 0, 0) + data
        header[:4] = b'mhit'
        struct.pack_into('<4I', header, 4, 0xf4, 0xf4 + len(children), 3, 12345)
        struct.pack_into('<I', header, 0x14, 1)
        struct.pack_into('<f', header, 0x88, 44100.0)
        struct.pack_into('<3I', header, 0xb8, *fields[:3])
        struct.pack_into('<I', header, 0xc8, fields[3])
        m.stream_data, m.stream_position = bytes(header) + children, 0
        m.hook(0x9de90, lambda: TRACK)
        m.cpu.mem_write(TRACK + 256, b'\xa5' * 16)
        m.write(STACK + 0x524, 1)
        for i in range(13):
            m.cpu.reg_write(REG[i], 0)
        for reg, value in ((6, READER), (7, READER + 0x40000), (8, 0x10003000)):
            m.cpu.reg_write(REG[reg], value)
        m.cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
        m.cpu.emu_start(0xb68c0, 0xb6f2c, count=200000)
        self.assertEqual(m.cpu.reg_read(arm.UC_ARM_REG_PC), 0xb6f2c)
        self.assertEqual(m.reg(5), 0)
        self.assertEqual(m.stream_position, len(m.stream_data))
        self.assertEqual(bytes(m.cpu.mem_read(TRACK + 0xec, 16)), struct.pack('<4I', *fields))
        self.assertEqual(bytes(m.cpu.mem_read(TRACK + 0xfc, 4)), b'WLC\0')
        self.assertEqual(bytes(m.cpu.mem_read(TRACK + 256, 16)), b'\xa5' * 16)

    def test_clone_copies_groups_and_preserves_gapless_and_guard(self):
        m = self.machine()
        source = bytes((33 * i + 9) & 255 for i in range(256))
        m.cpu.mem_write(0x10050000, source)
        m.cpu.mem_write(TRACK, b'\xa5' * 272)
        m.cpu.reg_write(REG[4], TRACK)
        m.cpu.reg_write(REG[5], 0x10050000)
        m.cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
        m.cpu.emu_start(0xda208, 0xda218, count=20000)
        self.assertEqual(bytes(m.cpu.mem_read(TRACK, 256)), source)
        self.assertEqual(bytes(m.cpu.mem_read(TRACK + 256, 16)), b'\xa5' * 16)

    def test_native_pool_size_reuse_and_free(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'gapless'))
        from verify_track_metadata import verify_pool
        verify_pool(self.image, 256)
        m = self.machine()
        m.hook(0x9b8e0, m.allocate)
        m.hook(0x9b8e8, m.allocate)
        m.hook(0x9b6fc, m.free)
        self.assertEqual(m.call(0x9d7ec, 256, 100, 0x10001000), 0)
        pool = m.read(0x10001000)
        track = m.call(0x9d6cc, pool)
        m.cpu.mem_write(track, b'\xa5' * 256)
        m.call(0x53634, pool, track)
        self.assertEqual(m.call(0x9d6cc, pool), track)
        self.assertEqual(bytes(m.cpu.mem_read(track, 256)), bytes(256))
        m.call(0x53720, pool)
        self.assertEqual(set(m.freed), set(m.allocations))

    def test_real_buffered_reader_keeps_following_record_intact(self):
        cases = (text_payload('Zebra'), text_payload('Z') + bytes(0x40000 - 18),
                 b'Q' * 15, b'Q' * (0x40000 + 1))
        for capacity in (16, 64, 4096, 0x40000):
            for kind in (28, 53):
                for data in cases:
                    with self.subTest(capacity=capacity, kind=kind, size=len(data)):
                        m = self.machine()
                        attach_native_stream(m, capacity)
                        self.assertEqual(import_sort(m, kind, data), 0)
                        self.assertEqual(m.call(0x4d208, READER, 0x10060000, len(m.sentinel)), 0)
                        self.assertEqual(bytes(m.cpu.mem_read(0x10060000, len(m.sentinel))), m.sentinel)
                        self.assertEqual(m.read(READER + 0x40000), STREAM)

    def test_real_buffered_reader_propagates_eof_without_caching(self):
        for capacity in (16, 64, 4096):
            m = self.machine()
            attach_native_stream(m, capacity)

            def short_read():
                m.write(m.reg(1), 0)
                return 0xffffffd9

            m.hook(0x94768, short_read)
            m.cpu.mem_write(TRACK + 0xfc, b'ABC\0')
            self.assertEqual(import_sort(m, 28, text_payload('Zebra')), 0xffffffd9)
            self.assertEqual(bytes(m.cpu.mem_read(TRACK + 0xfc, 4)), b'ABC\0')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    SortLetters.image = load_image(args.candidate)
    SortLetters.symbols = json.loads((args.candidate.with_suffix('.artifacts') / 'letters-symbols.json').read_text())
    unittest.main(argv=[__file__], verbosity=2)
