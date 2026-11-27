"""Execute stock and patched 4G ARM routines using Unicorn 2 (no device I/O)."""
import argparse
import struct
from pathlib import Path

from unicorn import UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, Uc
from unicorn import arm_const as arm

TRACK = 0x410000
LIBRARY = 0x411000
STREAM = 0x412000
STACK = 0x414000
STOP = 0x418000
SOURCE = 0x419000
WORD = struct.Struct("<I")
METADATA = struct.Struct("<IIII")
FIELDS = (0x00000123, 0x76543210, 0x00000002, 0x00000456)
REGS = [getattr(arm, f"UC_ARM_REG_R{index}") for index in range(13)]
POINTER_READS = {
    0xb6c84: (3, 0x24), 0xb6cb0: (3, 0x30), 0xb6cdc: (3, 0x34),
    0xb6cf0: (3, 0x28), 0xb6d1c: (3, 0x38), 0xb6d30: (3, 0x3c),
    0xb6d5c: (3, 0x40), 0xb6d70: (3, 0x44), 0xb6de4: (2, 0x44),
    0xb6df8: (3, 0x48), 0xb6e0c: (3, 0x4c), 0xb6e20: (3, 0x50),
    0xb6e34: (3, 0x54),
}


def machine(image):
    cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    cpu.mem_map(0, (len(image) + 4095) & -4096)
    cpu.mem_write(0, image)
    cpu.mem_map(0x400000, 0x20000)
    cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
    cpu.reg_write(arm.UC_ARM_REG_LR, STOP)
    return cpu


def run(cpu, start, stop):
    cpu.emu_start(start, stop, count=20000)
    assert cpu.reg_read(arm.UC_ARM_REG_PC) == stop, hex(cpu.reg_read(arm.UC_ARM_REG_PC))


def imported(image, endian, length, fields=FIELDS):
    cpu = machine(image)
    header = bytearray(0xf4)
    pack = lambda offset, fmt, *args: struct.pack_into(endian + fmt, header, offset, *args)
    header[:4] = b'mhit' if endian == '<' else b'tihm'
    pack(4, 'I', length)
    pack(8, 'I', max(0xf4, length))
    pack(0x10, 'I', 0x01234567)
    pack(0x14, 'I', 1)
    pack(0x88, 'f', 44100.0)
    pack(0xb8, 'I', fields[0])
    pack(0xbc, 'Q', fields[2] << 32 | fields[1])
    pack(0xc8, 'I', fields[3])
    cpu.mem_write(STREAM + 0x83, bytes([endian == '>']))
    cpu.mem_write(TRACK + 0xfc, bytes([0xa5]) * 20)
    cpu.reg_write(REGS[4], 0)
    cpu.reg_write(REGS[6], STREAM)
    cpu.reg_write(REGS[7], STREAM)
    cpu.reg_write(REGS[8], LIBRARY)
    cpu.mem_write(STACK + 0x524, WORD.pack(1))
    # Use the firmware's real byte swap, short-header fill, and field mappings.
    # Replace file I/O and the constructor's application services at this boundary.
    def intercept(uc, pc, size, data):
        if pc == 0x4d208:
            assert uc.reg_read(REGS[2]) == len(header)
            uc.mem_write(uc.reg_read(REGS[1]), bytes(header))
            result = 0
        elif pc == 0x9de90:
            result = TRACK
        elif pc == 0x316ac:
            assert uc.reg_read(REGS[1]) == (length - 0xf4) & 0xffffffff
            result = 0
        else:
            return
        uc.reg_write(REGS[0], result)
        uc.reg_write(arm.UC_ARM_REG_PC, uc.reg_read(arm.UC_ARM_REG_LR))
    cpu.hook_add(UC_HOOK_CODE, intercept)
    run(cpu, 0xb68c0, 0xb6f2c)
    assert cpu.reg_read(REGS[5]) == 0
    assert cpu.reg_read(REGS[9]) == 0
    assert bytes(cpu.mem_read(TRACK + 0xfc, 20)) == bytes([0xa5])*20
    assert bytes(cpu.mem_read(TRACK + 0x1c, 4)) == WORD.pack(0x01234567)
    return cpu


def verify_pool(image, stride):
    cpu = machine(image)
    descriptor = SOURCE
    chunk = 0x400000
    # Fill the slot area and a guard. The real allocator should clear only the
    # slots it returns, even though it links all 100 members when filling a chunk.
    cpu.mem_write(chunk, bytes([0xac]) * (100 * stride + 0x28))
    def allocate(uc, pc, size, data):
        if pc == 0x9b8e8:
            assert uc.reg_read(REGS[0]) == 16
            result = descriptor
        elif pc == 0x9b8e0:
            assert uc.reg_read(REGS[0]) == 100 * stride + 0x24
            result = chunk
        else:
            return
        uc.reg_write(REGS[0], result)
        uc.reg_write(arm.UC_ARM_REG_PC, uc.reg_read(arm.UC_ARM_REG_LR))
    cpu.hook_add(UC_HOOK_CODE, allocate)
    cpu.reg_write(REGS[2], LIBRARY + 0x38)
    run(cpu, 0x8bf14, STOP)
    assert WORD.unpack(cpu.mem_read(LIBRARY + 0x38, 4))[0] == descriptor
    assert struct.unpack("<II", cpu.mem_read(descriptor + 4, 8)) == (stride, 100)
    for i in range(100):
        cpu.reg_write(REGS[0], descriptor)
        cpu.reg_write(arm.UC_ARM_REG_LR, STOP)
        run(cpu, 0x9d6cc, STOP)
        slot = chunk + 0x20 + i * stride
        assert cpu.reg_read(REGS[0]) == slot
        assert bytes(cpu.mem_read(slot, stride)) == bytes(stride)
        cpu.mem_write(slot, bytes([0x5d]) * stride)
    assert bytes(cpu.mem_read(chunk + 0x20, 100 * stride)) == bytes([0x5d]) * (100 * stride)
    assert bytes(cpu.mem_read(chunk + 0x20 + 100 * stride, 8)) == bytes([0xac])*8


def verify(stock, patched):
    for image, stride in ((stock, 0xf0), (patched, 0x100)):
        verify_pool(image, stride)
    print("Stock and patched track pools each allocate 100 zeroed slots with verified strides")
    for endian in ('<', '>'):
        for length in (0xb0, 0xbb, 0xc3, 0xcb, 0xcc, 0xf4, 0x184):
            for fields in (FIELDS, (0, 0, 0, 0)):
                a = imported(stock, endian, length, fields)
                b = imported(patched, endian, length, fields)
                assert a.mem_read(TRACK, 0xec) == b.mem_read(TRACK, 0xec)
                new_fields = METADATA.unpack(b.mem_read(TRACK + 0xec, 16))
                assert new_fields == (fields if length >= 0xcc else (0, 0, 0, 0)), (endian, length, new_fields)
                for pc, (reg, displacement) in POINTER_READS.items():
                    for cpu in (a,b):
                        run(cpu, pc, pc+4)
                        assert cpu.reg_read(REGS[reg]) == TRACK + displacement, hex(pc)
    print("28 track imports and 13 legacy child-record pointer sites verified")

    cpu = machine(patched)
    source = bytes((33 * i + 9) & 255 for i in range(252))
    cpu.mem_write(SOURCE, source)
    cpu.mem_write(TRACK, bytes([0xf5]) * 256)
    cpu.reg_write(REGS[4], TRACK)
    cpu.reg_write(REGS[5], SOURCE)
    run(cpu, 0xda208, 0xda218)
    assert bytes(cpu.mem_read(TRACK, 256)) == source + bytes([0xf5]) * 4
    print("Track clone copies 252 bytes and preserves the following guard")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock_osos', type=Path)
    parser.add_argument('patched_osos', type=Path)
    args = parser.parse_args()
    verify(args.stock_osos.read_bytes(), args.patched_osos.read_bytes())
