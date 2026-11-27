"""Characterize native letter navigation; this does NOT patch/build firmware.

Run with .venv/bin/python outside the macOS sandbox (Unicorn uses JIT).
The 5G grouping, search, string ownership, quick-letter lookup and UI property
dispatcher execute original ARM. Search supplies model storage and selection;
property tests supply UTF-16 disk strings. 4G tests use its original list-model
constructor, property chain, index builder and UTF-8 conversion. These establish
porting contracts, not hardware performance or a finished letter-navigation UI.
"""
import argparse
import hashlib
import struct
import unittest
from pathlib import Path
from zipfile import ZipFile

from native_fixture import Library, Machine, QUERY, DB, load_image
from unicorn import arm_const as arm

REFERENCE_SHA = "784ae3d5540fd2f89e8c947b93629e97f62a06dc311d9745aab143e4db6bb251"
CANDIDATE_SHA = "24fe2cf44239c4fb65c9732c89da35f84296093acad3b1ec7da0a75f58423d7b"
STR, STQN = 0x53747220, 0x5374514e
TEXT, WRAPPER = 0x10002000, 0x10001000
WIDGET, MODEL = 0x10004000, 0x10005000
UI, Q5, TABLE, INDEX, DATABASE, OUTPUT, CONFIG = (
    0x10010000, 0x10011000, 0x10013000, 0x10014000,
    0x10015000, 0x10016000, 0x10017000)
UI4, MODEL4, ALL4 = 0x10013000, 0x10016000, 0x10017000


def c_string(machine, pointer):
    return bytes(machine.cpu.mem_read(pointer, 1024)).split(b"\0", 1)[0].decode("utf-8")


def reference_image(path):
    with ZipFile(path) as z:
        firmware = z.read("Firmware-25.6.3")
    offset, length = struct.unpack_from("<II", firmware, 0x420c)
    # 5G uses 2048-byte sectors here; the 4G payload helper uses 512.
    image = firmware[offset + 0x800:offset + 0x800 + length]
    if hashlib.sha256(image).hexdigest() != REFERENCE_SHA:
        raise ValueError("expected stock 5.5G Firmware-25.6.3")
    return image


class Reference(Machine):
    def __init__(self, image):
        super().__init__(b"")
        self.cpu.mem_map(0x400000, 0x400000)
        self.cpu.mem_write(0, image)
        self.cpu.mem_map(0x10800000, 0x100000)
        # Startup copy-table entries, not invented UI globals.
        for entry in (0x84058, 0x8406c, 0x84080, 0x84094, 0x840a8, 0x840bc):
            source, dest, size = struct.unpack_from("<3I", image, entry)
            if 0x10800000 <= dest < 0x10900000:
                source &= 0x3ffffff
                self.cpu.mem_write(dest, image[source:source + size])
        self.cursor = 0x10030000
        self.live = {}
        self.peak_live = 0
        self.hook(0x11e6ac, self.resize)
        self.hook(0x11ab70, self.release)

    def resize(self):
        old, size = self.reg(0), self.reg(1)
        assert old == 0, "search should construct fresh string wrappers"
        pointer = self.cursor
        self.cursor += (size + 15) & ~15
        assert self.cursor < 0x100e0000
        self.live[pointer] = size
        self.peak_live = max(self.peak_live, sum(self.live.values()))
        return pointer

    def release(self):
        del self.live[self.reg(0)]

    def group(self, name):
        self.cpu.mem_write(TEXT, name.encode("utf-8") + b"\0")
        self.write(WRAPPER, 0, TEXT)
        return chr(self.call(0x10f55c, WRAPPER))

    def search_fixture(self, names, start, first=0):
        self.names, self.selected, self.first = names, start, first
        self.reads, self.commits = [], []
        self.write(WIDGET, 0x66717c)
        self.write(WIDGET + 0xa4, MODEL)
        self.write(WIDGET + 0xb4, first, len(names))
        self.cpu.mem_write(WIDGET + 0x134, struct.pack("<h", 0x6423))
        self.write(MODEL + 0x14, start)
        self.hook(0x21f254, self.name_at)
        self.hook(0x257260, lambda: self.selected)
        self.hook(0x23ac08, self.select)

    def name_at(self):
        assert (self.reg(0), self.reg(1), self.reg(2)) == (MODEL, STQN, 0x6423)
        row = self.read(MODEL + 0x14)
        assert self.first <= row < len(self.names), row
        self.reads.append(row)
        self.cpu.mem_write(TEXT, self.names[row].encode("utf-8") + b"\0")
        return TEXT

    def select(self):
        # The real search must restore its temporary model index BEFORE it
        # calls the selection boundary, which can notify other UI observers.
        assert self.read(MODEL + 0x14) == self.selected
        row = self.reg(1)
        assert self.first <= row < len(self.names), row
        self.commits.append(row)
        self.selected = row
        self.write(MODEL + 0x14, row)

    def artist_property_fixture(self):
        self.write(UI + 0x1c, Q5)
        self.write(UI + 0x24, CONFIG)
        self.write(CONFIG, Q5)
        self.write(Q5, 1)
        self.write(Q5 + 0xc9c, 0x00130001)  # version 0x113
        self.write(Q5 + 0xc48, INDEX, TABLE, 3)
        self.write(Q5 + 0xcf8, DATABASE)
        self.write(INDEX, 1, 2, 3)
        # Two native runs: B begins at item 0, C at item 2. This fixture
        # supplies the already-imported index; it does not run the importer.
        self.write(TABLE, 2, ord("B"), 0, ord("C"), 2, 0, 3)
        self.disk_names = ["The Beatles", "Björk", "Coldplay"]
        self.disk_reads = []
        self.hook(0x9f0c4, self.disk_string)

    def disk_string(self):
        assert self.reg(0) == DATABASE + 0xe0
        name = self.disk_names[self.reg(1) - 1]
        data = name.encode("utf-16le")
        self.cpu.mem_write(self.reg(2), struct.pack("<H", len(data) // 2) + data)
        self.disk_reads.append(name)
        return 0

    def artist_property(self, tag, row):
        assert self.call(0x1ec614, UI, tag, 0x6423, row, OUTPUT) == 1
        return c_string(self, self.read(OUTPUT))


class FourGNames(Library):
    def __init__(self, image, tracks):
        super().__init__(image, tracks)
        self.write(UI4, 0x26294)
        self.write(UI4 + 0x1c, QUERY)
        assert self.call(0xede78, MODEL4, UI4, 0) == MODEL4
        self.cpu.mem_write(ALL4, b"All\0")
        self.hook(0x6c770, lambda: 0x10018000)  # localization context
        self.hook(0x77368, self.localized_all)

    def localized_all(self):
        assert self.reg(1) == 0x3314
        return ALL4

    def copy_string(self):
        assert self.reg(0) - DB in (0xb0, 0xdc, 0x108, 0x134, 0x18c)
        _, data, _ = self.strings[self.reg(1)]
        self.cpu.mem_write(self.reg(2), struct.pack("<H", len(data) // 2) + data)
        self.storage_reads += 1
        return 0

    def name(self, row, key=0x336c, tag=STR):
        self.write(MODEL4 + 0x14, row)
        pointer = self.call(0x75584, MODEL4, tag, key)
        assert self.read(MODEL4 + 0x14) == row
        return c_string(self, pointer)


class NativeReferenceLetters(unittest.TestCase):
    def test_filtered_rows_rebase_letter_boundaries(self):
        # Execute the actual accepted-entry block of the 5G index builder.
        # The query/compilation predicate precedes this block; accepted source
        # positions below are supplied explicitly, not claimed as full imports.
        for accepted, runs, letters in (
                ((0, 1, 3, 7), [("A", 0), ("B", 2), ("C", 3)], "AABC"),
                ((2, 3, 7), [("B", 0), ("C", 2)], "BBC"),
                ((6, 7), [("C", 0)], "CC"),
                ((), [], "")):
            with self.subTest(accepted=accepted):
                m = Reference(self.image)
                source, result, stack = 0x10019000, 0x1001a000, 0x100fc000
                m.write(source, 3, ord("A"), 0, ord("B"), 2, ord("C"), 6, 0, 8)
                m.write(result, 0)
                m.write(stack + 0x24, source)
                m.write(stack + 0x40, 0xffffffff)
                m.cpu.reg_write(arm.UC_ARM_REG_SP, stack)
                m.cpu.reg_write(arm.UC_ARM_REG_R8, result)
                m.cpu.reg_write(arm.UC_ARM_REG_R9, 0)
                for output_row, source_row in enumerate(accepted):
                    m.cpu.reg_write(arm.UC_ARM_REG_R4, output_row)
                    m.cpu.reg_write(arm.UC_ARM_REG_R11, source_row)
                    m.cpu.emu_start(0xd0c74, 0xd0d28, count=1000)
                    self.assertEqual(m.cpu.reg_read(arm.UC_ARM_REG_PC), 0xd0d28)
                    self.assertEqual(m.cpu.reg_read(arm.UC_ARM_REG_SP), stack)
                actual = [(chr(m.read(result + 4 + 8*i) & 0xffff), m.read(result + 8 + 8*i))
                          for i in range(m.read(result))]
                self.assertEqual(actual, runs)
                # Use the original binary-search consumer of that very output.
                actual_letters = []
                for row in range(len(accepted)):
                    self.assertEqual(m.call(0x9cec4, result, row, OUTPUT), 0)
                    actual_letters.append(chr(m.read(OUTPUT) & 0xffff))
                self.assertEqual("".join(actual_letters), letters)

    def test_grouping_folds_latin_and_preserves_special_buckets(self):
        m = Reference(self.image)
        for name, expected in (
                ("Alpha", "A"), ("alpha", "A"), (" Éclair", "E"),
                ("Ångström", "A"), ("Æther", "A"), ("Œuvre", "O"),
                ("ß", "S"), ("ø", "O"), ("123", "#"), ("#foo", "#"),
                ("", "#"), ("   ", "#"), ("中", "!"), ("あ", "!"),
                ("한", "!"), ("Ω", "#"), ("Ж", "#"), ("😀", "#")):
            with self.subTest(name=name):
                self.assertEqual(m.group(name), expected)

    def test_forward_backward_and_end_boundaries(self):
        names = ["Alpha"] * 40 + ["Beta"] * 10 + ["Charlie"] * 45 + ["Delta"] * 2
        for start, direction, expected in (
                (0, 1, 40), (10, 1, 40), (40, 1, 50), (60, -1, 50),
                (50, -1, 40), (40, -1, 0), (0, -1, 0), (96, 1, 96), (0, 0, 0)):
            with self.subTest(start=start, direction=direction):
                m = Reference(self.image)
                m.search_fixture(names, start)
                result = m.call(0x239b28, WIDGET, direction & 0xffffffff)
                self.assertEqual(m.selected, expected)
                self.assertEqual(result, int(start != expected))
                self.assertEqual(m.live, {})
                if direction == 0:
                    self.assertEqual(m.reads, [])

    def test_nonzero_first_and_single_row_do_not_escape_bounds(self):
        for names, start, first, direction, expected in (
                (["ignored", "Alpha", "Alpha", "Beta"], 3, 1, -1, 1),
                (["ignored", "Alpha", "Alpha", "Beta"], 1, 1, -1, 1),
                (["Alpha"], 0, 0, 1, 0), (["Alpha"], 0, 0, -1, 0)):
            m = Reference(self.image)
            m.search_fixture(names, start, first)
            m.call(0x239b28, WIDGET, direction & 0xffffffff)
            self.assertEqual(m.selected, expected)
            self.assertEqual(m.live, {})

    def test_long_group_uses_sparse_probes_and_releases_strings(self):
        m = Reference(self.image)
        m.search_fixture(["Alpha"] * 2000 + ["Beta"], 0)
        self.assertEqual(m.call(0x239b28, WIDGET, 1), 1)
        self.assertEqual(m.selected, 2000)
        self.assertLess(len(m.reads), 100)
        self.assertEqual(m.live, {})
        self.assertLessEqual(m.peak_live, 12)

    def test_cached_sort_letter_differs_from_display_name(self):
        m = Reference(self.image)
        m.artist_property_fixture()
        self.assertEqual([m.artist_property(STR, i) for i in (1, 2, 3)],
                         ["The Beatles", "Björk", "Coldplay"])
        m.disk_reads.clear()
        self.assertEqual([m.artist_property(STQN, i) for i in (1, 2, 3)], ["B", "B", "C"])
        self.assertEqual(m.disk_reads, [])

    def test_quick_name_all_row_uses_first_artist(self):
        m = Reference(self.image)
        m.artist_property_fixture()
        self.assertEqual(m.artist_property(STQN, 0), "B")
        self.assertEqual(m.disk_reads, [])

    def test_missing_letter_cache_falls_back_to_full_display_name(self):
        m = Reference(self.image)
        m.artist_property_fixture()
        m.write(Q5 + 0xc4c, 0)
        self.assertEqual(m.artist_property(STQN, 1), "The Beatles")
        self.assertEqual(m.disk_reads, ["The Beatles"])

    def test_native_version_guard_suppresses_artist_quick_name(self):
        m = Reference(self.image)
        m.artist_property_fixture()
        m.write(Q5 + 0xc9c, 0x00120001)  # version 0x112
        self.assertEqual(m.artist_property(STQN, 1), "")
        self.assertEqual(m.disk_reads, [])


class NativeFourGNames(unittest.TestCase):
    def test_actual_row_model_handles_filtered_artist_index_and_all(self):
        m = FourGNames(self.image, [("A", True), ("The Beatles", False), ("Éclair", False)])
        self.assertEqual(m.build_index(), ["The Beatles", "Éclair"])
        before = bytes(m.cpu.mem_read(QUERY, 0xaac))
        self.assertEqual([m.name(i) for i in range(3)], ["All", "The Beatles", "Éclair"])
        self.assertEqual(bytes(m.cpu.mem_read(QUERY, 0xaac)), before)

    def test_each_named_index_has_the_correct_row_offset(self):
        # Named artist/album/genre/composer indexes all reserve row zero for All.
        for mask, key, rows in ((4, 0x336c, (1, 2)), (2, 0x336a, (1, 2)),
                                (0x40, 0x336d, (1, 2)), (0x4000000, 0x336b, (1, 2))):
            with self.subTest(key=hex(key)):
                m = FourGNames(self.image, [("Alpha", False), ("中", False)])
                self.assertEqual(m.build_index(mask), ["Alpha", "中"])
                self.assertEqual([m.name(i, key) for i in rows], ["Alpha", "中"])

    def test_4g_quick_name_tag_does_not_provide_5g_sort_letters(self):
        m = FourGNames(self.image, [("The Beatles", False)])
        m.build_index()
        self.assertEqual(m.name(1, tag=STQN), "The Beatles")
        self.assertEqual(m.name(0, tag=STQN), "All")

    def test_empty_filtered_artist_list_retains_all_and_bounds(self):
        m = FourGNames(self.image, [("Compilation artist", True)])
        self.assertEqual(m.build_index(), [])
        self.assertEqual(m.name(0), "All")
        self.assertEqual(m.name(1), "")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=Path("iPod_25.1.3.ipsw"))
    parser.add_argument("--candidate", type=Path,
                        default=Path("build/navigation-qol/iPod_4.3.1.1-gapless-navigation.ipsw"))
    args = parser.parse_args()
    image = load_image(args.candidate)
    if hashlib.sha256(image).hexdigest() != CANDIDATE_SHA:
        raise ValueError("expected the pinned two-feature navigation candidate")
    NativeFourGNames.image = image
    NativeReferenceLetters.image = reference_image(args.reference)
    unittest.main(argv=[__file__], verbosity=2)
