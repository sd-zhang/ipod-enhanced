"""Independent checks of the packaged UI change, against its input IPSW.

No emulator or physical device is involved. This reads the actual output ZIP
and resource records instead of importing the patcher's parser or target table.
"""
import argparse
import struct
import unittest
from pathlib import Path
from zipfile import ZipFile

ROOT = 0x1a507c
MEMBER = "Firmware-4.3.1.1"
TARGETS = {
    0x3301: 0x3367, 0x3302: 0x336a, 0x3303: 0x336b,
    0x3304: 0x336c, 0x3305: 0x336d, 0x3307: 0x336f, 0x330f: 0x3374,
}


def word(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def archive(path):
    with ZipFile(path) as z:
        assert z.testzip() is None
        return {name: z.read(name) for name in z.namelist()}


def payload(firmware, entry):
    start = 512 + word(firmware, entry + 12)
    return firmware[start:start + word(firmware, entry + 16)]


def directory(image):
    base = ROOT + word(image, ROOT + 4)
    sections = {}
    for i in range(word(image, ROOT + 8)):
        header = ROOT + 12 + i * 16
        tag = image[header:header + 4][::-1].decode("ascii")
        table = ROOT + word(image, header + 12)
        entries = {}
        keys = []
        for j in range(word(image, header + 4)):
            key, offset, size = struct.unpack_from("<3I", image, table + j * 12)
            assert base + offset + size <= len(image) - 4
            entries[key] = image[base + offset:base + offset + size]
            keys.append(key)
        assert keys == sorted(set(keys)), (tag, "unsorted or repeated resource ID")
        sections[tag] = header, entries
    return sections


def records(data):
    result, cursor = [], 4
    for _ in range(word(data, 0)):
        length = 4 + word(data, cursor)
        assert length >= 16 and length % 4 == 0
        result.append(data[cursor:cursor + length])
        cursor += length
    assert cursor == len(data)
    return result


def resource_key(record):
    return struct.unpack_from("<h", record, 12)[0] & 0xffffffff


class ResourceContract(unittest.TestCase):
    def test_music_lists_have_independent_scrolling_selected_rows(self):
        old = directory(self.before)["View"][1]
        new = directory(self.after)["View"][1]
        for key, label in TARGETS.items():
            with self.subTest(list=hex(key)):
                original = records(old[key])
                pair = records(new[key])
                self.assertEqual(len(pair), 2, "missing selected-row template")
                self.assertEqual(pair[0], original[0])
                self.assertNotEqual(resource_key(pair[0]), resource_key(pair[1]))
                self.assertEqual(pair[1][:12], pair[0][:12], "selected row class/size/tag changed")
                self.assertEqual(pair[1][16:], pair[0][16:], "selected row layout/flags changed")
                normal = records(new[resource_key(pair[0])])
                selected = records(new[resource_key(pair[1])])
                self.assertEqual(len(normal), len(selected))
                changed = 0
                for a, b in zip(normal, selected):
                    if word(a, 12) != label:
                        self.assertEqual(a, b, "changed an icon or unrelated label")
                        continue
                    changed += 1
                    self.assertEqual(word(b, 4), 0x3180)
                    self.assertEqual(word(b, 0), 0x64)
                    self.assertEqual(b[8:0x58], a[8:])
                    self.assertEqual(struct.unpack_from("<4I", b, 0x58), (140, 1250, 8, 25))
                self.assertEqual(changed, 1)

    def test_every_other_resource_is_unchanged(self):
        for tag, (_, old) in directory(self.before).items():
            new = directory(self.after)[tag][1]
            for key, data in old.items():
                if tag == "View" and key in TARGETS:
                    continue
                self.assertEqual(data, new[key], (tag, hex(key)))
            self.assertEqual(len(new) - len(old), 7 if tag == "View" else 0)

    def test_executable_audio_and_original_resources_are_byte_identical(self):
        before, after = self.before, self.after
        view_header = directory(before)["View"][0]
        allowed = (0xe8, 0x2e8, 0x4a8, view_header + 4, view_header + 12)
        restored = bytearray(after[:len(before) - 4])
        for offset in allowed:
            restored[offset:offset + 4] = before[offset:offset + 4]
        self.assertEqual(bytes(restored), before[:-4])
        self.assertLessEqual(len(after), 0x390000)
        self.assertEqual(len(after) % 4, 0)
        self.assertEqual(word(after, 0xe8), len(after))
        self.assertEqual(after[-4:], b"\x55\xaa\x55\xaa")
        for offset in (0x2e8, 0x4a8):
            self.assertEqual(word(after, offset), 0x04000000 + len(after) - 4)

    def test_container_keeps_updater_and_metadata(self):
        self.assertEqual(self.old_archive.keys(), self.new_archive.keys())
        for name in self.old_archive:
            if name != MEMBER:
                self.assertEqual(self.old_archive[name], self.new_archive[name])
        old, new = self.old_archive[MEMBER], self.new_archive[MEMBER]
        self.assertEqual(payload(old, 0x4228), payload(new, 0x4228))
        self.assertEqual(sum(self.after) & 0xffffffff, word(new, 0x421c))
        # Apart from OSOS length/checksum and the relocated updater, headers stay.
        restored = bytearray(new[:0x4400])
        for offset in (0x4210, 0x421c, 0x4234):
            restored[offset:offset + 4] = old[offset:offset + 4]
        self.assertEqual(restored, old[:0x4400])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    ResourceContract.old_archive = archive(args.source)
    ResourceContract.new_archive = archive(args.candidate)
    ResourceContract.before = payload(ResourceContract.old_archive[MEMBER], 0x4200)
    ResourceContract.after = payload(ResourceContract.new_archive[MEMBER], 0x4200)
    unittest.main(argv=[__file__], verbosity=2)
