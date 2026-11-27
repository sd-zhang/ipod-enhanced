"""Native 4G test fixtures. Disk storage, allocation and OS services are supplied.

The index builder, metadata getters, collation, query predicate, script VM and
history navigation execute the original ARM code. This is not a device emulator.
Run Unicorn outside the macOS sandbox, using the repository's Python environment.
"""
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "menu_scroll"))
from verify_native import Machine
from verify_resources import archive, payload
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ
from unicorn import arm_const as arm

QUERY, DB, PLAYLIST, HEADER = 0x10001000, 0x10003000, 0x10005000, 0x10006000
OUT = 0x10010000
APP, RENDERER = 0x10011000, 0x10012000


def load_image(path):
    return payload(archive(path)["Firmware-4.3.1.1"], 0x4200)


class NativeMachine(Machine):
    def __init__(self, image):
        super().__init__(image)
        self.image = image
        self.cpu.mem_map(0x10300000, 0x200000)
        self.cpu.mem_write(0x10390004, image[0x32de04:0x32de04 + 0xd96c])
        self.allocations = {}
        self.freed = []
        self.cursor = 0x10020000

    def allocate(self):
        size = self.reg(0)
        at = self.cursor
        self.cursor += (size + 15) & ~15
        assert self.cursor < 0x100d0000, "fixture allocation exhausted"
        self.allocations[at] = size
        return at

    def free(self):
        self.freed.append(self.reg(0))


class Library(NativeMachine):
    """Tracks arrive in iTunes' sort order; original firmware builds the index."""
    def __init__(self, image, tracks, artist_filter=None):
        super().__init__(image)
        self.write(QUERY, DB)
        self.write(QUERY + 0xa70, DB)
        self.write(PLAYLIST + 0x24, HEADER)
        self.cpu.mem_write(HEADER + 0x2c, struct.pack("<HH", 0, len(tracks)))
        self.strings = {}
        self.wrappers = []
        self.lookups = 0
        self.storage_reads = 0
        self.locks = []
        for i, (name, compilation) in enumerate(tracks):
            wrapper, track, string = 0x10007000 + i * 0x40, 0x10040000 + i * 0x100, 0x10080000 + i * 0x400
            assert i < 128
            data = name.encode("utf-16le")
            assert len(data) <= 1024
            self.cpu.mem_write(string, data)
            self.strings[i + 1] = (string, data, name)
            self.write(wrapper, PLAYLIST)
            self.write(wrapper + 8, wrapper + 0x40 if i + 1 < len(tracks) else 0)
            self.write(wrapper + 0x20, track)
            self.write(track, DB)
            self.cpu.mem_write(track + 0x11, bytes([2 if compilation else 0]))
            for offset in (0x28, 0x30, 0x38, 0x3c):
                self.write(track + offset, i + 1)
            self.wrappers.append(wrapper)
        self.cpu.mem_write(HEADER + 0x1d, b"\x01")
        self.write(HEADER + 0x20, self.wrappers[0] if self.wrappers else 0)
        if artist_filter is not None:
            data = artist_filter.encode("utf-16le")
            self.cpu.mem_write(QUERY + 0x270, struct.pack("<H", len(data) // 2) + data)
        self.hook(0x6bdac, lambda: PLAYLIST)
        self.hook(0x6e4ac, self.track_at)
        self.hook(0x76af8, self.string_at)
        self.hook(0x76c90, self.copy_string)
        self.hook(0x9b8e0, self.allocate)
        self.hook(0x9b6fc, self.free)
        self.hook(0x98b48, lambda: self.locks.append("lock"))
        self.hook(0xdfa74, lambda: self.locks.append("unlock"))

    def track_at(self):
        assert self.reg(0) == PLAYLIST and self.reg(2) == 0
        assert self.reg(1) in (6, 7, 9, 24)
        self.lookups += 1
        return self.wrappers[self.reg(3)]

    def string_at(self):
        assert self.reg(0) - DB in (0xdc, 0x134, 0x18c)
        at, data, _ = self.strings[self.reg(1)]
        self.write(self.reg(2), at)
        self.write(self.reg(3), len(data))
        self.storage_reads += 1
        return 0

    def copy_string(self):
        assert self.reg(0) == DB + 0xdc
        _, data, _ = self.strings[self.reg(1)]
        self.cpu.mem_write(self.reg(2), struct.pack("<H", len(data) // 2) + data)
        return 0

    def build_index(self, mask=4):
        result = self.call(0x4a1d0, QUERY, mask, 0)
        assert result == 0, hex(result)
        offset = {4: 0xa8c, 2: 0xa9c, 0x40: 0xa84, 0x4000000: 0xa94}[mask]
        pointer, count = self.read(QUERY + offset), self.read(QUERY + offset + 4)
        return [self.strings[self.read(pointer + 4 * i)][2] for i in range(count)]

    def selected_artist(self, index):
        result = self.call(0x6e60c, QUERY, index & 0xffffffff, OUT)
        if result != 0:
            return None
        length = self.read(OUT) & 0xffff
        return bytes(self.cpu.mem_read(OUT + 2, length * 2)).decode("utf-16le")


class ScriptVM(NativeMachine):
    def __init__(self, image):
        super().__init__(image)
        # Unicorn's default ARM model accepts odd halfword reads; the 4G's
        # ARM7 does not define them. In particular, 68f00 reads the extension
        # count immediately after a function's declared bytecode length.
        def aligned_halfword(cpu, access, address, size, value, context):
            if size == 2 and address & 1:
                raise AssertionError(
                    f'ARM7 unaligned halfword read at {address:#x}, '
                    f'PC={cpu.reg_read(arm.UC_ARM_REG_PC):#x}')
        self.cpu.hook_add(UC_HOOK_MEM_READ, aligned_halfword)
        for address in (0x960e4, 0xd9a60):
            self.hook(address, self.allocate)
        for address in (0x96070, 0xd915c):
            self.hook(address, self.free)
        self.hook(0xd7980, lambda: self.allocations[self.reg(0)])
        assert self.call(0x95410, 0xa6f30, 8192) == 1
        assert self.call(0x95e64) == 1
        assert self.call(0x95340) == 1
        # Follow the packaged resource directory: later UI candidates relocate
        # TCmd. Loading its old address would silently test obsolete scripts.
        root = 0x1a507c
        for i in range(struct.unpack_from('<I', image, root + 8)[0]):
            tag, count, _, relative = struct.unpack_from('<4sIII', image, root + 12 + 16 * i)
            if tag == b'dmCT':
                entries = image[root + relative:root + relative + 12 * count]
                for key, offset, size in struct.iter_unpack('<III', entries):
                    if key == 0x2380:
                        at = root + struct.unpack_from('<I', image, root + 4)[0] + offset
                        self.call(0x95484, at, size)
                        break
                else:
                    raise AssertionError('missing TCmd 2380')
                break
        else:
            raise AssertionError('missing TCmd directory')
        self.root = self.read(0x1041bab4)
        self.globals = self.read(0x1041ba14)
        for atom, type_index, raw in ((0x99d, 2, APP), (0x99e, 3, RENDERER),
                                      (0x9ac, 25, 0x10013000), (0x9ad, 26, 0x10014000)):
            wrapper = self.object(type_index, raw)
            self.call(0xe7658, self.global_slot(atom), wrapper)
        self.event = self.object(7, 0x10015000)  # signature 2b 01 08 encodes type index 7
        self.call(0xe7658, OUT + 0x200, self.event)

    def object(self, type_index, raw):
        wrapper = self.call(0x12e948, 0x1e10ec + 34 * type_index,
                            self.root, self.read(0x1041bab8))
        self.write(wrapper + 0x2c, raw | 1)
        return wrapper

    def global_slot(self, atom):
        return self.globals + 4 * (atom - 2461)

    def method_address(self, type_index, selector):
        table = 0x1e10ec + 34 * type_index
        half = lambda offset: struct.unpack_from("<H", self.image, table + offset)[0]
        assert half(18) <= selector < half(20)
        index = half(28) - half(30) + selector - half(18)
        return struct.unpack_from("<I", self.image, 0x1dfe48 + 4 * index)[0]

    def run(self, atom, *args):
        assert self.call(0x949d0, atom * 4 + 2, OUT, OUT + 4) == 1
        if args:
            self.write(OUT + 0x100, *args)
        assert self.call(0x12e068, self.root, self.read(OUT), len(args), OUT + 0x100, OUT + 8) == 1
        return self.read(OUT + 8)

    def history(self, commands):
        """Native history nodes; widget-restoration lists are empty fixtures.

        The command boundary records leave/activation callbacks. Native contains,
        unwind, pop, node destruction and list ownership execute unchanged.
        """
        self.write(APP, 0x258a4)
        self.write(APP + 0x10c, 0x286d8, 0, 0, 0)
        self.commands = []
        self.hook(0x91404, lambda: self.commands.append(self.reg(0)))
        previous = 0
        nodes = []
        for command, leave in commands:
            node = self.call(0xeaf74, 0, command * 4 + 2)
            self.write(node + 4, previous)
            self.write(node + 0x10, leave * 4 + 2 if leave else 0)
            previous = node
            nodes.append(node)
        self.write(APP + 0x104, previous)
        return nodes

    def real_leave_callbacks(self):
        """Run original callback dispatch/VM, stopping only at home rendering.

        Assert callback success separately: the stock history unwind does not
        propagate a script failure to its own caller.
        """
        del self.hooks[0x91404]
        self.callback_status = []
        self.write(self.global_slot(0x9a6), 1)  # native tagged integer zero

        def dispatch(cpu, address, size, context):
            if address == 0x91404:
                self.commands.append(self.reg(0))
                if self.reg(0) == 1882 * 4 + 2:
                    cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))
            elif address == 0x9cfb0:
                self.callback_status.append(self.reg(0))
        self.cpu.hook_add(UC_HOOK_CODE, dispatch)

    def supply_services(self, methods):
        for type_index, selectors in methods:
            for selector in selectors:
                self.hook(self.method_address(type_index, selector), lambda: 0)
