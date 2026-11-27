"""Exercise Volume Limit storage and widget operations in the packaged ARM code.

The native slider, Pixo binding, file wrappers, preference initialization and
logical-volume calculations execute unchanged. A memory filesystem supplies
the lower storage API, including short transfers and explicit failure cases.
Run with .venv/bin/python outside the macOS sandbox for Unicorn's JIT.
"""
import argparse
import struct
import unittest
from pathlib import Path

from native_fixture import NativeMachine, load_image
from verify_resources import directory, records, word

PREFS, AUDIO, UI = 0x10001000, 0x10003000, 0x10008000
RENDERER, PARENT, DISPLAY = 0x10012000, 0x1001b000, 0x1001c000
MAGIC = 0x564c4d31
NAMES = tuple(f'iPod_Control\\Device\\VolumeLimit{i}' for i in range(2))


def record(generation, cap):
    return struct.pack('<5I', MAGIC, generation, generation ^ 0xffffffff,
                       cap, cap ^ 0xffffffff)


def boot_target(image):
    instruction = word(image, 0x8db08)
    delta = (instruction & 0xffffff) << 2
    if delta & 0x2000000:
        delta -= 0x4000000
    return 0x8db10 + delta


class VolumeMachine(NativeMachine):
    def __init__(self, image, files=None, regional=False):
        super().__init__(image)
        self.files = dict(files or {})
        self.handles, self.io, self.outputs = {}, [], []
        self.failure = None
        self.regional = regional
        self.hook(0x799e8, lambda: int(self.regional))
        self.hook(0xb7d28, lambda: 0)  # native Preferences file is independent
        self.hook(0xa2b60, lambda: 7)
        self.hook(0x49dc4, lambda: 0)
        self.hook(0x49f14, lambda: 0)
        self.hook(0xa33d4, self.open_file)
        self.hook(0xa2db0, self.file_size)
        self.hook(0xa34a8, self.read_file)
        self.hook(0xa38e4, self.write_file)
        self.hook(0xa2a34, self.flush_file)
        self.hook(0xa2188, self.close_file)
        self.hook(0x6bdd4, lambda: 0x10015000)  # stock a7 fallback, no list needed
        self.hook(0x9f9a8, lambda: 0x10018000)
        self.hook(0xd9a60, self.allocate)
        self.hook(0xd97b8, self.allocate)
        self.hook(0xec, lambda: self.cpu.mem_write(
            self.reg(0), bytes(self.cpu.mem_read(self.reg(1), self.reg(2)))))
        self.hook(0x6c760, lambda: 0)
        self.hook(0x64a48, lambda: UI if self.reg(0) == 0x3300 else 0)
        self.hook(0x3be44, lambda: 0)
        self.hook(0x9100c, lambda: 0)  # raster invalidation, not widget state
        self.hook(0x3f33c, lambda: 0)  # display ancestor notification
        self.hook(0x39b2c, lambda: 0)  # external property observers
        self.write(UI, 0x26294)
        self.write(UI + 0x18, PREFS)
        self.write(PREFS + 4, AUDIO)
        self.write(PREFS + 0xb48, 216 if regional else 256)
        self.write(AUDIO, PREFS)
        self.write(AUDIO + 0x2770, 200, 31)
        self.hook(0xea168, lambda: 0)
        self.hook(0x102494, lambda: 0)
        self.hook(0xe1fc4, lambda: 0)
        self.hook(0x14f960, lambda: self.outputs.append(self.reg(1)) or 0)
        # Construct the native Contrast scalar with the Volume Limit contract.
        self.call(0xba5f0)
        row = bytearray(next(r for r in records(directory(image)['View'][1][0x27b4])
                             if word(r, 4) == 0x3080))
        for offset, value in ((12, 0x6121), (16, 0xffffffff), (88, 256)):
            struct.pack_into('<I', row, offset, value)
        self.cpu.mem_write(0x1001a000, bytes(row[4:]))
        self.slider = self.call(0x4c454, 0, 0, 0x1001a000, 0x707a8)
        self.call(0x660f0, self.slider)
        self.write(PARENT, 0x272a0)
        self.write(PARENT + 0x88, DISPLAY)
        self.write(self.slider + 0x10, PARENT)
        self.write(RENDERER, 0x23d7c)
        self.write(RENDERER + 0x18, self.slider)

    def path(self, address):
        return bytes(self.cpu.mem_read(address, 128)).split(b'\0', 1)[0].decode('ascii')

    def open_file(self):
        drive, name, mode = self.reg(0), self.path(self.reg(1)), self.reg(2)
        assert drive == 7 and name in NAMES and mode in (1, 2)
        self.io.append(('open', name, mode))
        if mode == 1 and name not in self.files:
            return -12  # native ENOENT mapping at b468c / 43ab0
        if mode == 1 and self.failure == 'read_open':
            return -27
        if mode == 2 and self.failure == 'open':
            return -1
        handle = len(self.handles) + 10
        self.handles[handle] = name, mode
        if mode == 2:
            self.files[name] = b''
        return handle

    def file_size(self):
        name, _ = self.handles[self.reg(0)]
        if self.failure == 'size':
            return -1
        self.cpu.mem_write(self.reg(1), bytes(44))
        self.write(self.reg(1) + 12, len(self.files[name]))
        return 0

    def read_file(self):
        name, mode = self.handles[self.reg(0)]
        assert mode == 1
        if self.failure == 'read':
            return -1
        data = self.files[name][:self.reg(2)]
        if self.failure == 'short_read':
            data = data[:8]
        self.cpu.mem_write(self.reg(1), data)
        return len(data)

    def write_file(self):
        name, mode = self.handles[self.reg(0)]
        assert mode == 2
        self.io.append(('write', name, self.reg(2)))
        if self.failure == 'write':
            return -1
        data = bytes(self.cpu.mem_read(self.reg(1), self.reg(2)))
        if self.failure == 'short_write':
            data = data[:8]
        self.files[name] = data
        return len(data)

    def flush_file(self):
        self.io.append(('flush', self.handles[self.reg(0)][0]))
        return -1 if self.failure == 'flush' else 0

    def close_file(self):
        name, mode = self.handles[self.reg(0)]
        self.io.append(('close', name, mode))
        return -1 if (self.failure == 'close' and mode == 2 or
                      self.failure == 'read_close' and mode == 1) else 0

    def load(self):
        return self.call(boot_target(self.image), PREFS)

    def control(self, operation):
        return self.call(word(self.image, 0x1dff6c), RENDERER, operation & 0xffffffff)

    def draft(self, value):
        self.call(0xce1f4, self.slider, value, 0)


class VolumeLimit(unittest.TestCase):
    def test_boot_uses_newest_valid_record_and_native_regional_ceiling(self):
        for regional, files, expected in (
                (False, {}, 256), (True, {}, 216),
                (False, {NAMES[0]: record(4, 90)}, 90),
                (True, {NAMES[0]: record(4, 256)}, 216),
                (False, {NAMES[0]: record(4, 90), NAMES[1]: record(5, 130)}, 130),
                (False, {NAMES[0]: record(0xffffffff, 90), NAMES[1]: record(0, 130)}, 130)):
            with self.subTest(regional=regional, files=files):
                m = VolumeMachine(self.image, files, regional)
                self.assertEqual(m.load(), 1)
                self.assertEqual(m.read(PREFS + 0xb48), expected)
                self.assertFalse(any(event[0] == 'write' for event in m.io))

    def test_bad_record_never_replaces_valid_slot(self):
        good = record(4, 90)
        corrupt = bytearray(record(5, 130))
        corrupt[12] ^= 1
        for bad in (b'', good[:8], good + b'junk', bytes(corrupt), record(5, 257), bytes(20)):
            m = VolumeMachine(self.image, {NAMES[0]: good, NAMES[1]: bad})
            m.load()
            self.assertEqual(m.read(PREFS + 0xb48), 90)

    def test_short_or_failed_reads_are_rejected(self):
        for failure in ('short_read', 'read'):
            m = VolumeMachine(self.image, {NAMES[0]: record(4, 90)})
            m.failure = failure
            m.load()
            self.assertEqual(m.read(PREFS + 0xb48), 256)

    def test_wheel_changes_only_bounded_draft_and_reopen_discards_it(self):
        m = VolumeMachine(self.image, regional=True)
        m.write(PREFS + 0xb48, 120)
        self.assertEqual(m.control(-1), 1)
        self.assertEqual(m.read(m.slider + 0x6c), 120)
        self.assertEqual(m.read(m.slider + 0x74), 216)
        for _ in range(250):
            m.control(-2)
        self.assertEqual(m.read(m.slider + 0x6c), 216)
        for _ in range(250):
            m.control(-3)
        self.assertEqual(m.read(m.slider + 0x6c), 0)
        self.assertEqual(m.read(PREFS + 0xb48), 120)
        self.assertEqual(m.io, [])
        self.assertEqual(m.outputs, [])
        m.control(-1)
        self.assertEqual(m.read(m.slider + 0x6c), 120)

    def test_encoded_wheel_magnitude_zero_and_saturation_change_only_draft(self):
        m = VolumeMachine(self.image)
        m.write(PREFS + 0xb48, 120)
        m.control(-1)
        for operation, expected in ((-16, 120), (-17, 120), (-18, 121),
                                    (-19, 120), (-36, 130), (-37, 120),
                                    (-528, 256), (-529, 0),
                                    (-0x80000000, 256), (-0x7fffffff, 0)):
            with self.subTest(operation=operation):
                self.assertEqual(m.control(operation), 1)
                self.assertEqual(m.read(m.slider + 0x6c), expected)
        self.assertEqual(m.read(PREFS + 0xb48), 120)
        self.assertEqual(m.io, [])
        self.assertEqual(m.outputs, [])

    def test_save_preserves_previous_slot_lowers_volume_and_balance(self):
        old = record(9, 210)
        m = VolumeMachine(self.image, {NAMES[0]: old})
        m.control(-1)
        m.draft(120)
        self.assertEqual(m.control(-4), 1)
        self.assertEqual(m.files[NAMES[0]], old)
        self.assertEqual(m.files[NAMES[1]], record(10, 120))
        self.assertEqual(m.read(PREFS + 0xb48), 120)
        self.assertEqual(m.read(UI + 0x184c), 120)
        self.assertEqual((m.read(AUDIO + 0x2770), m.read(AUDIO + 0x2774)), (120, 31))
        self.assertEqual(len(m.outputs), 1)
        self.assertEqual([e[0] for e in m.io if e[0] in ('write', 'flush')], ['write', 'flush'])
        reboot = VolumeMachine(self.image, m.files)
        reboot.load()
        self.assertEqual(reboot.read(PREFS + 0xb48), 120)

    def test_lowering_reapplies_even_when_current_volume_already_below_limit(self):
        m = VolumeMachine(self.image)
        m.write(AUDIO + 0x2770, 50)
        m.control(-1)
        m.draft(120)
        self.assertEqual(m.control(-4), 1)
        self.assertEqual(m.read(AUDIO + 0x2770), 50)
        self.assertEqual(len(m.outputs), 1)

    def test_raising_ceiling_does_not_raise_output_and_full_range_is_unlimited(self):
        m = VolumeMachine(self.image, {NAMES[0]: record(5, 90)}, regional=True)
        m.write(PREFS + 0xb48, 90)
        m.write(AUDIO + 0x2770, 80)
        m.control(-1)
        m.draft(216)
        self.assertEqual(m.control(-4), 1)
        self.assertEqual(m.files[NAMES[1]], record(6, 256))
        self.assertEqual(m.read(PREFS + 0xb48), 216)
        self.assertEqual(m.read(AUDIO + 0x2770), 80)
        self.assertEqual(m.outputs, [])

    def test_save_failures_leave_runtime_cap_and_previous_file_unchanged(self):
        for failure in ('open', 'write', 'short_write', 'flush', 'close'):
            with self.subTest(failure=failure):
                old = record(9, 90)
                m = VolumeMachine(self.image, {NAMES[0]: old})
                m.write(PREFS + 0xb48, 90)
                m.control(-1)
                m.draft(120)
                m.failure = failure
                self.assertEqual(m.control(-4), 0)
                self.assertEqual(m.files[NAMES[0]], old)
                self.assertEqual(m.read(PREFS + 0xb48), 90)
                self.assertEqual(m.outputs, [])
                if failure == 'short_write':
                    reboot = VolumeMachine(self.image, m.files)
                    reboot.load()
                    self.assertEqual(reboot.read(PREFS + 0xb48), 90)

    def test_unreadable_slots_are_not_overwritten_during_save(self):
        for failure in ('read_open', 'read', 'short_read', 'size', 'read_close'):
            with self.subTest(failure=failure):
                files = {NAMES[0]: record(9, 90), NAMES[1]: record(8, 80)}
                m = VolumeMachine(self.image, files)
                m.write(PREFS + 0xb48, 90)
                m.control(-1)
                m.draft(120)
                m.failure = failure
                self.assertEqual(m.control(-4), 0)
                self.assertEqual(m.files, files)
                self.assertEqual(m.read(PREFS + 0xb48), 90)

    def test_save_wraps_generation_and_alternates_oldest_slot(self):
        m = VolumeMachine(self.image, {NAMES[0]: record(0xfffffffe, 100),
                                       NAMES[1]: record(0xffffffff, 110)})
        m.control(-1)
        m.draft(120)
        self.assertEqual(m.control(-4), 1)
        self.assertEqual(m.files[NAMES[0]], record(0, 120))
        self.assertEqual(m.files[NAMES[1]], record(0xffffffff, 110))

    def test_zero_limit_mutes_and_survives_restart(self):
        m = VolumeMachine(self.image)
        m.control(-1)
        m.draft(0)
        self.assertEqual(m.control(-4), 1)
        self.assertEqual(m.read(AUDIO + 0x2770), 0)
        self.assertEqual(m.outputs, [0])
        reboot = VolumeMachine(self.image, m.files)
        reboot.load()
        self.assertEqual(reboot.read(PREFS + 0xb48), 0)

    def test_backend_failure_preserves_saved_cap_and_center_can_retry(self):
        m = VolumeMachine(self.image)
        m.control(-1)
        m.draft(120)
        # The output backend fails after native logical-volume state changes.
        decoder = 0x10016000
        m.write(AUDIO + 0xc, decoder)
        m.write(AUDIO + 0x2768, 1)
        m.cpu.mem_write(decoder + 0x2b6, b'\x01')
        m.write(decoder + 0x14, 0x74706c79)
        m.hook(0xdcc54, lambda: -36)
        self.assertEqual(m.control(-4), 0)
        self.assertEqual(m.read(PREFS + 0xb48), 120)
        self.assertEqual(m.read(AUDIO + 0x2770), 120)
        self.assertEqual(m.files[NAMES[0]], record(0, 120))
        applied = []
        m.hook(0xdcc54, lambda: applied.append(m.reg(1)) or 0)
        self.assertEqual(m.control(-4), 1)
        self.assertEqual(len(applied), 1)

    def test_ordinary_renderer_selector_still_uses_original_method(self):
        m = VolumeMachine(self.image)
        native = []
        m.hook(0x1b6f8, lambda: native.append((m.reg(0), m.reg(1))) or 42)
        for argument in (0, 5, 7, 0x261d, -5, -15):
            self.assertEqual(m.control(argument), 42)
        self.assertEqual(native, [(RENDERER, value & 0xffffffff)
                                  for value in (0, 5, 7, 0x261d, -5, -15)])
        self.assertEqual(m.io, [])

    def test_missing_ui_prefs_or_widget_fails_without_storage(self):
        for missing in ('ui', 'prefs', 'widget'):
            m = VolumeMachine(self.image)
            if missing == 'ui':
                m.hook(0x64a48, lambda: 0)
            elif missing == 'prefs':
                m.write(UI + 0x18, 0)
            else:
                m.write(RENDERER + 0x18, 0)
            for operation in (-1, -2, -3, -4):
                self.assertEqual(m.control(operation), 0)
            self.assertEqual(m.io, [])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('firmware', type=Path)
    args = parser.parse_args()
    VolumeLimit.image = (args.firmware.read_bytes() if args.firmware.suffix == '.bin'
                         else load_image(args.firmware))
    unittest.main(argv=['verify_volume'], verbosity=2)
