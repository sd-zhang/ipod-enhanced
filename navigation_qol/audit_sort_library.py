"""Read a supplied iTunesDB and replay its sort metadata/index in native ARM.

No device writes. The JSON report contains counts/hashes, not library names.
Run outside the macOS sandbox with the repository Python environment.
"""
import argparse
from collections import Counter
import hashlib
import json
import struct
import unicodedata
from pathlib import Path

from native_fixture import Library, HEADER, QUERY, DB, PLAYLIST, load_image
from research_letters import Reference, reference_image
from verify_sort_letters import attach_stream, import_sort, GROUP


def tracks_and_order(data):
    at = data.index(b'mhlt')
    header, count = struct.unpack_from('<II', data, at + 4)
    assert header >= 12
    at += header
    tracks = []
    for _ in range(count):
        assert data[at:at + 4] == b'mhit'
        header, total, children = struct.unpack_from('<III', data, at + 4)
        assert 0xf4 <= header <= total and at + total <= len(data)
        cursor = at + header
        metadata = {}
        for _ in range(children):
            assert data[cursor:cursor + 4] == b'mhod'
            size, length, kind = struct.unpack_from('<III', data, cursor + 4)
            assert 24 <= size <= length and cursor + length <= at + total
            metadata[kind] = data[cursor + size:cursor + length]
            cursor += length
        assert cursor == at + total
        tracks.append((metadata, bool(data[at + 0x1e] & 1)))
        at += total
    # Find the host album permutation; require a complete bijection of tracks.
    cursor = 0
    while (cursor := data.find(b'mhod', cursor)) >= 0:
        size, total, kind = struct.unpack_from('<III', data, cursor + 4)
        if kind == 52 and size >= 24 and size + 48 <= total and cursor + total <= len(data):
            sort, entries = struct.unpack_from('<II', data, cursor + size)
            if sort == 4 and entries == count:
                assert size + 48 + 4 * count <= total
                order = struct.unpack_from(f'<{count}I', data, cursor + size + 48)
                assert sorted(order) == list(range(count))
                return tracks, order
        cursor += 4
    raise AssertionError('no full host album-order permutation')


def decode(payload):
    encoding, length = struct.unpack_from('<II', payload)
    assert len(payload) >= 16 + length
    return payload[16:16 + length].decode({1: 'utf-16le', 2: 'utf-8'}[encoding])


def sort_initial(name):
    """Independent Unicode classification for the host's ignored prefixes.

    Keep base-letter folding separate: the old audit compared the marker to
    the same first-character assumption and missed #Goldrush/(R)Evolution.
    """
    symbol_only = False
    for char in name:
        category = unicodedata.category(char)
        if category[0] in 'PS':
            symbol_only = True
        elif category[0] == 'Z' or category in ('Cf', 'Mn') or char in '\t\n\v\f\r':
            pass
        else:
            return char if char != '\0' else ''
    return '#' if symbol_only else ''


def audit(candidate, database):
    data = database.read_bytes()
    tracks, order = tracks_and_order(data)
    reference = Reference(reference_image(Path('iPod_25.1.3.ipsw')))
    expected_cache = {}

    def expected(name):
        first = sort_initial(name)
        if not first:
            return 0
        # The 5G reference executes the independent native grouping routine.
        if first not in expected_cache:
            expected_cache[first] = ord(reference.group(first))
        return expected_cache[first]

    m = Library(load_image(candidate), [])
    attach_stream(m)
    # Keep the full library separate from fixture heap, stack and importer.
    m.cpu.mem_map(0x12000000, 0x1000000)
    m.instruction_limit = 30000000
    m.cpu.mem_write(HEADER + 0x2c, struct.pack('<HH', 0, len(tracks)))
    cursor = 0x12400000
    imported, bytes_read = Counter(), 0
    article_tracks, article_albums = 0, set()
    expected_groups = {}
    for rank, source_index in enumerate(order):
        metadata, compilation = tracks[source_index]
        name = decode(metadata[3]) if 3 in metadata else ''
        wrapper, track = 0x12000000 + rank * 64, 0x12100000 + rank * 256
        encoded = name.encode('utf-16le')
        assert cursor + len(encoded) < 0x13000000
        if encoded:
            m.cpu.mem_write(cursor, encoded)
        m.strings[rank + 1] = (cursor, encoded, name)
        cursor += (len(encoded) + 3) & ~3
        m.write(wrapper, PLAYLIST)
        m.write(wrapper + 8, wrapper + 64 if rank + 1 < len(tracks) else 0)
        m.write(wrapper + 0x20, track)
        m.write(track, DB)
        m.cpu.mem_write(track + 0x11, bytes([2 if compilation else 0]))
        for offset in (0x28, 0x30, 0x38, 0x3c):
            m.write(track + offset, rank + 1)
        m.wrappers.append(wrapper)
        for kind, slot in GROUP.items():
            if kind in metadata:
                payload = metadata[kind]
                assert import_sort(m, kind, payload, track=track) == 0
                assert m.stream_position == len(payload)
                actual = bytes(m.cpu.mem_read(track + slot, 1))[0]
                assert actual == expected(decode(payload)), (source_index, kind, actual)
                imported[kind] += 1
                bytes_read += len(payload)
        sort = decode(metadata[28]) if 28 in metadata else ''
        expected_groups[rank + 1] = expected(sort) or expected(name) or ord('#')
        if name.casefold().startswith(('a ', 'an ', 'the ')):
            article_tracks += 1
            article_albums.add(name)
            assert sort, 'article-prefixed album has no host sort name'
    m.write(HEADER + 0x20, m.wrappers[0] if m.wrappers else 0)
    print(f'Imported {sum(imported.values())} real sort records; building {len(tracks)}-track native album index', flush=True)
    m.build_index(2)
    pointer, count = m.read(QUERY + 0xa9c), m.read(QUERY + 0xaa0)
    actual = bytes(m.cpu.mem_read(pointer + 4 * count, count))
    expected_bytes = bytes(expected_groups[m.read(pointer + 4 * i)] for i in range(count))
    assert actual == expected_bytes
    # Concrete observations from the saved host permutation, independent of
    # both firmware folding and the generic expected() implementation above.
    observed = {'#Goldrush, Vol. 1': ord('G'), '(R)Evolution: The Remixes': ord('R')}
    observed_checked = []
    for i in range(count):
        name = m.strings[m.read(pointer + 4 * i)][2]
        if name in observed:
            assert actual[i] == observed[name]
            observed_checked.append(name)
    return dict(candidate=str(candidate), sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
                database_sha256=hashlib.sha256(data).hexdigest(), database_bytes=len(data), tracks=len(tracks),
                sort_records=dict(imported), import_payload_bytes=bytes_read,
                article_album_tracks=article_tracks, unique_article_albums=len(article_albums),
                native_album_rows=count, mismatched_groups=0,
                reported_prefix_examples_checked=len(observed_checked),
                supplied_boundaries=['stream reads/seeks', 'heap/locks', 'host-order track lookup', 'string storage'],
                hardware_tested=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('database', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.candidate, args.database)
    with args.report.open('x') as file:
        file.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
