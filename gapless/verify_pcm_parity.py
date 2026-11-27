"""Compare a marked album at the 5.5G's PCM ring and the patched 4G's I2S queue."""
import argparse
import hashlib
import struct
from pathlib import Path

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_ARM, Uc
from unicorn import arm_const as arm

from verify_clip import Port, REFERENCE_HASH
from verify_transition import verify as run_4g_album
from verify_tx import Machine

ENGINE, OUTPUT, RING, SLOTS = 0x900000, 0x910000, 0x912000, 0x913000
BUFFER, CODEC, VTABLE, METHODS = 0x920000, 0x940000, 0x941000, 0x942000
RECORD, HANDLE, HANDLE_VTABLE = 0x944000, 0x946000, 0x947000
VALID_HANDLE, COMMAND = 0x948000, 0x949000
STACK, STOP = 0x94f000, 0x94ff00
MODE = 0x14937338
PUMP = 0x40018ed4
REG = [getattr(arm, f'UC_ARM_REG_R{i}') for i in range(13)]


class Retail55Pump:
    """Execute the retail pump and queue. Feed decoded blocks at the codec vtable."""
    def __init__(self, image):
        if hashlib.sha256(image).hexdigest() != REFERENCE_HASH:
            raise ValueError('expected the stock 5.5G OSOS')
        self.cpu = c = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        c.mem_map(0, 0x740000)
        c.mem_write(0, image)
        c.mem_map(ENGINE, 0x50000)
        c.mem_map(0x40000000, 0x20000)
        c.mem_map(0x10800000, 0x100000)
        c.mem_map(0x14936000, 0x100000)
        # The 5.5G's startup table gives the load, execution and zero-fill
        # ranges. Relative calls between noncontiguous overlays need SRAM PCs.
        for entry in (0x84058, 0x8406c, 0x840a8, 0x840bc):
            source, destination, size = struct.unpack_from('<III', image, entry)
            source &= 0x00ffffff
            c.mem_write(destination, image[source:source + size])
        self.write(CODEC, VTABLE)
        for method in (8, 12, 16, 20, 60):
            self.write(VTABLE + method, METHODS + method)
            c.hook_add(UC_HOOK_CODE, self.codec_method, method,
                       begin=METHODS + method, end=METHODS + method)
        # Single-threaded fixture: cache maintenance, task locks, notifications
        # and the message inbox do not drive the producer or trim the data.
        for address in (0xf5c20, 0x235df8, 0x235dc0, 0x2355ac,
                        0xc15cc, 0x2404dc, 0x4001dc0c):
            c.hook_add(UC_HOOK_CODE, self.service, begin=address, end=address)
        for address in (0xa96c0, 0x17ca6c, 0x17ca8c, VALID_HANDLE, 0x25a974):
            c.hook_add(UC_HOOK_CODE, self.metadata_service, begin=address, end=address)
        self.write(HANDLE, HANDLE_VTABLE)
        self.write(HANDLE + 4, RECORD)
        self.write(HANDLE_VTABLE, VALID_HANDLE)
        self.write(OUTPUT + 0x58, ENGINE)
        self.write(OUTPUT + 0x5c, RING)
        self.write(ENGINE + 0x2e5c, OUTPUT)
        c.mem_write(ENGINE + 0x2e28, bytes((2, 16)))
        self.write(RING + 0x30, SLOTS)
        self.write(RING + 0x38, BUFFER)
        c.mem_write(RING + 0x24, bytes((3,)))
        self.capacity = 0

    def write(self, address, value):
        self.cpu.mem_write(address, struct.pack('<I', value))

    def read(self, address):
        return struct.unpack('<I', self.cpu.mem_read(address, 4))[0]

    def ret(self, value):
        self.cpu.reg_write(REG[0], value)
        self.cpu.reg_write(arm.UC_ARM_REG_PC, self.cpu.reg_read(arm.UC_ARM_REG_LR))

    def service(self, cpu, address, size, user):
        self.ret(0)

    def metadata_service(self, cpu, address, size, user):
        if address == 0xa96c0:
            self.ret(RECORD)
        elif address == 0x25a974:
            assert cpu.reg_read(REG[0]) == 0x48
            self.ret(COMMAND)
        elif address == VALID_HANDLE:
            self.ret(1)
        else:
            # Enter/leave lock, preserving the firmware's argument registers.
            cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))

    def track_command(self, count, padding, delay):
        c = self.cpu
        # At 0xd79c4 the 5.5G has read a 0x1b4-byte library header to sp+0x424.
        header = bytearray(0x1b4)
        header[:4] = b'mhit'
        struct.pack_into('<II', header, 4, len(header), len(header))
        struct.pack_into('<I', header, 0x14, 1)
        struct.pack_into('<IQ', header, 0xb8, delay, count)
        struct.pack_into('<II', header, 0xc8, padding, 1)
        struct.pack_into('<I', header, 0xf8, 0xa55a)
        c.mem_write(STACK + 0x424, bytes(header))
        c.reg_write(arm.UC_ARM_REG_SP, STACK)
        c.reg_write(REG[4], STACK + 0x424)
        c.emu_start(0xd79c4, 0xd7bcc, count=1000)
        assert c.reg_read(arm.UC_ARM_REG_PC) == 0xd7bcc
        assert struct.unpack('<Q', c.mem_read(RECORD + 0xf8, 8))[0] == count
        # The caller at 0xacabc keeps its local track handle at sp+4.
        c.reg_write(arm.UC_ARM_REG_SP, HANDLE - 4)
        c.reg_write(REG[0], HANDLE)
        c.emu_start(0xacabc, 0x215ec0, count=600)
        assert c.reg_read(arm.UC_ARM_REG_PC) == 0x215ec0
        assert c.reg_read(REG[0]) == COMMAND and self.read(COMMAND + 0x1c) == 53
        return struct.unpack('<5I', c.mem_read(COMMAND + 0x2c, 20))

    def codec_method(self, cpu, address, size, method):
        if method == 12:
            if self.next_block == len(self.blocks):
                self.ret(0)
                return
            target = cpu.reg_read(REG[1])
            cpu.mem_write(target, self.blocks[self.next_block])
            self.next_block += 1
            # Stock may pipeline a second decode into its other scratch slot.
            self.pending.append(target)
            self.ret(1)
        elif method == 16:
            if self.pending:
                target = self.pending.pop(0)
                if BUFFER <= target < BUFFER + 16 * self.capacity:
                    # The original producer reads the asynchronous source clock
                    # at engine+0x2de8 before computing each descriptor's span.
                    if self.position is None:
                        self.position = self.read(ENGINE + 0x2de8)
                    self.write(ENGINE + 0x2de8, self.position)
                    self.position += self.block_frames
                else:
                    self.preroll += 1
                self.ret(1)
            else:
                self.ret(0)
        elif method == 20:
            self.ret(int(self.next_block == len(self.blocks) and not self.pending))
        elif method == 60:
            self.ret(4 * self.block_frames)
        else:
            self.ret(0)

    def call(self, address, *args):
        c = self.cpu
        c.reg_write(arm.UC_ARM_REG_SP, STACK)
        c.reg_write(arm.UC_ARM_REG_LR, STOP)
        for i, value in enumerate(args):
            if i < 4:
                c.reg_write(REG[i], value)
            else:
                self.write(STACK + (i - 4) * 4, value)
        try:
            c.emu_start(address, STOP, count=3000000)
        except Exception as exc:
            exc.add_note(f'entry={address:#x}, pc={c.reg_read(arm.UC_ARM_REG_PC):#x}, '
                         f'lr={c.reg_read(arm.UC_ARM_REG_LR):#x}')
            raise
        assert c.reg_read(arm.UC_ARM_REG_PC) == STOP, hex(c.reg_read(arm.UC_ARM_REG_PC))
        return c.reg_read(REG[0])

    def track(self, blocks, *, codec, delay, count, padding, mp3_mode=2):
        self.blocks = blocks
        self.next_block = self.preroll = 0
        self.pending = []
        self.block_frames = len(blocks[0]) // 4
        capacity = self.block_frames * 4
        assert capacity and all(len(block) == capacity for block in blocks)
        c = self.cpu
        if not self.capacity:
            self.write(RING + 0x40, capacity)
            self.write(RING + 0x44, capacity)
            self.write(RING + 0x4c, 16 * capacity)
            c.mem_write(MODE, bytes((1, 1)))
            assert self.call(0x1c6dac, RING, 0, 0, capacity) == 0
            self.capacity = capacity
        else:
            assert self.capacity == capacity
            # The stock initializer has progressed from mode 1 to mode 2.
            # Calling it for the successor preserves the old descriptors.
            assert self.call(0x1c6dac, RING, 0, 0, capacity) == 8
        first = self.read(RING + 20)
        # Keep this fixture below one complete revolution of the 16-slot ring.
        # No asynchronous hardware consumer can erase the observed descriptors.
        assert first + len(blocks) < 16
        c.mem_write(ENGINE + 0x2de8, bytes(8))
        self.write(ENGINE + 0x90c8, self.block_frames)
        c.mem_write(ENGINE + 0x2e34, bytes((codec,)))
        c.mem_write(ENGINE + 0x2e3c, bytes((mp3_mode,)))
        fields = self.track_command(count, padding, delay)
        assert fields == (count, padding, delay, 0xa55a, 1)
        self.call(0x249cfc, ENGINE, *fields)
        # Expose the clock's initial value as set by whole-block pre-roll.
        self.position = None
        self.call(PUMP, ENGINE, CODEC)
        after = self.read(RING + 20)
        output = bytearray()
        for i in range(first, after):
            slot = SLOTS + 28 * i
            base, extent, length = struct.unpack('<3I', c.mem_read(slot, 12))
            assert extent == capacity and base == BUFFER + i * extent
            assert c.mem_read(slot + 16, 1)[0] == 8 and length <= extent
            output.extend(c.mem_read(base, length))
        return bytes(output)


def marked_blocks(first, size, number):
    return [struct.pack(f'<{size}I', *range(i, i + size))
            for i in range(first, first + size * number, size)]


def verify(stock, candidate, elf, reference):
    # The library stores both words of mhit.sampleCount. Exercise the producer
    # and the 4G clip cursor without simulating over 27 hours of playback.
    long_count = (1 << 32) + 700
    assert Retail55Pump(reference).track_command(long_count, 1, 1)[0] == 700
    with elf.open('rb') as binary:
        port = Port(binary)
    port.start(1, long_count, 1)
    assert port.state() == (1, 700, 1, 1)
    print('Count 2^32 + 700: 5.5G command 53 carries 700, 4G cursor retains both words')
    for b_count in (621, 550):
        ref = Retail55Pump(reference)
        a = ref.track(marked_blocks(0, 1152, 2), codec=0, delay=1, count=700, padding=1)
        a_last = ref.read(RING + 20) - 1
        b = ref.track(marked_blocks(10000, 1152, 1), codec=0, delay=2,
                      count=b_count, padding=1)
        b_last = ref.read(RING + 20) - 1
        assert a_last < b_last
        for index in (a_last, b_last):
            assert ref.cpu.mem_read(SLOTS + 28 * index + 24, 1) == b'\x01'
        with elf.open('rb') as f:
            frames = run_4g_album(Machine(stock, f, 0x21f3c), candidate, b_count)
        captured = struct.pack(f'<{len(frames)}I', *frames)
        assert a == captured[:700 * 4] and b[:b_count * 4] == captured[700 * 4:]
        assert len(a) == 700 * 4 and len(b) == 621 * 4
        assert b[b_count * 4:] == struct.pack(f'<{621-b_count}I',
                                               *range(10531 + b_count, 11152))
        assert (a + b == captured) == (b_count == 621)
        print(f'Marked A/B, requested 700+{b_count}, stock 5.5G ring '
              f'{len(a)//4}+{len(b)//4}, patched 4G I2S 700+{len(frames)-700}')


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('stock_4g', 'patched_4g', 'playback_elf', 'stock_55g'):
        cli.add_argument(name, type=Path)
    args = cli.parse_args()
    verify(args.stock_4g.read_bytes(), args.patched_4g.read_bytes(), args.playback_elf,
           args.stock_55g.read_bytes())
