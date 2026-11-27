"""Exercise the real 4G request, file selection and decoder-open path in Unicorn."""
import argparse
import struct
from pathlib import Path

from unicorn import UC_HOOK_CODE
from unicorn import arm_const as arm

from image_layout import extend_osos
from patch_engine_binding import install_binding
from patch_music_request import patch_requests
from patch_track_metadata import patch_osos
from verify_track_metadata import REGS, STACK, STOP, TRACK, WORD, machine, run

ENGINE = 0x144a91fc
FILE_INFO = ENGINE + 0x2e3c
GLOBALS = 0x103c99a8
LIBRARY, COLLECTION, MANAGER, PATH = 0x401000, 0x402000, 0x403000, 0x404000
REQUEST, UTF16, TEMP_INFO = 0x405000, 0x406000, 0x407000
UI, CACHE_ENTRY, CACHE_CHUNK, CACHE_RANGE = 0x408000, 0x40c000, 0x40d000, 0x40e000
FILE, MFIL = 0x46494c45, 0x4d46494c
FIVE = struct.Struct("<5I")


def verify(image, symbols):
    cpu = machine(bytes(image))
    state = symbols["gapless_engine_timing"]
    publication = symbols["gapless_handoff_state"] + 4
    pages = {0x103c9000, 0x1041b000, 0x60000000, 0x60005000, state & -4096, (state + 35) & -4096,
             publication & -4096}
    # The extension's whole static RAM (a diagnostic build also counts and
    # traces events there): from its lowest to past its highest symbol.
    static = [v for v in symbols.values() if 0x14500000 <= v < 0x14600000]
    if static:
        pages |= set(range(min(static) & -4096, (max(static) + 0x3000) & -4096, 4096))
    for page in sorted(pages):
        cpu.mem_map(page, 4096)
    cpu.mem_map(ENGINE & -4096, 0xb000)

    def write_word(address, value):
        cpu.mem_write(address, WORD.pack(value))

    def word(address):
        return WORD.unpack(cpu.mem_read(address, 4))[0]

    write_word(MANAGER, COLLECTION)
    write_word(GLOBALS + 4, LIBRARY)
    write_word(GLOBALS + 8, MANAGER)
    write_word(ENGINE + 0x2e7c, COLLECTION)
    write_word(PATH, 2)
    cpu.mem_write(UTF16, "one.mp3\x00".encode("utf-16le"))
    blocks = {}
    heap = 0x41a000
    fail_size = None
    parser_result = 0
    locks = 0
    mfil_name = None
    def services(cpu, pc, instruction_size, user):
        nonlocal heap, locks
        a, b, c = (cpu.reg_read(r) for r in REGS[:3])
        if pc in (0x1045b8, 0x1044cc):
            if a == fail_size:
                result = 0
            else:
                result = heap
                heap += (a + 12 + 3) & -4
                blocks[result] = a
                cpu.mem_write(result, bytes([0xad]) * (a + 8))
        elif pc == 0x9b8e8 and a == 28:
            result = CACHE_RANGE
        elif pc == 0x9b8e8:
            assert a == 720
            cpu.mem_write(REQUEST, bytes([0x89]) * (a + 4))
            result = REQUEST
        elif pc == 0x11b5e0:
            assert not locks
            locks += 1
            result = 0
        elif pc == 0x11dd5c:
            assert locks == 1
            locks -= 1
            result = 0
        elif pc == 0xecad0:
            if mfil_name is not None:
                assert bytes(cpu.mem_read(b, len(mfil_name)+1)) == mfil_name + b'\0'
            write_word(a, UTF16)
            result = a
        elif pc == 0x3b4c4:
            assert locks == 1
            cpu.mem_write(c + 24, bytes([parser_result & 255]))
            write_word(c + 0x24, 1)
            result = 0
        elif pc in (0xfac94, 0xfaca4):
            size = blocks[a]
            assert bytes(cpu.mem_read(a + size, 8)) == bytes([0xad]) * 8
            cpu.mem_write(a, bytes([0xef]) * size)
            result = 0
        elif pc in (0x3b3d4, 0xa2bf0, 0xfd03c, 0xab790, 0x9f5d8, 0x9fce4, 0x9fcdc):
            result = 0
        elif pc == 0x133bf4:
            result = 1
        else:
            return
        cpu.reg_write(REGS[0], result)
        cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))

    cpu.hook_add(UC_HOOK_CODE, services)

    def append(fields, cookie, creator=0xa7e24, source=FILE, eligible=True):
        cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
        cpu.reg_write(REGS[5], TRACK)
        cpu.mem_write(TRACK + 0xec, struct.pack("<4I", *fields))
        run(cpu, 0xa7da4, 0xa7dc8)
        assert FIVE.unpack(cpu.mem_read(REQUEST + 700, 20)) == (*fields, 0)
        assert bytes(cpu.mem_read(REQUEST + 720, 4)) == bytes([0x89]) * 4
        write_word(REQUEST + 716, cookie)
        cpu.mem_write(STACK, bytes(32))
        write_word(STACK, PATH)
        cpu.reg_write(REGS[7], UI)
        for reg, value in zip(REGS, (LIBRARY, REQUEST + 20, 0, source)):
            cpu.reg_write(reg, value)
        cpu.reg_write(arm.UC_ARM_REG_LR, creator)
        run(cpu, 0xdc43c, creator)
        assert locks == 0
        if not word(COLLECTION):
            return 0
        node = word(word(COLLECTION))
        assert blocks[node] == 84 and bytes(cpu.mem_read(node + 84, 8)) == b"\xad" * 8
        expected = (*fields, cookie) if creator == 0xa7e24 and eligible else (0,) * 5
        actual = FIVE.unpack(cpu.mem_read(node + 64, 20))
        assert actual == expected, (hex(source), actual, expected)
        return node

    def select(destination=FILE_INFO):
        cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
        cpu.reg_write(REGS[4], ENGINE + 0x2000)
        write_word(STACK + 0x78, destination)
        write_word(STACK + 0x88, TEMP_INFO + 0x100)
        run(cpu, 0x115e54, 0x115e64)
        assert not locks
        return cpu.reg_read(REGS[0])

    a = (576, 0x76543210, 1, 201)
    b = (2112, 45123, 0, 37)
    curr = bytes(range(16))
    node = append(a, 0x80000011)
    assert word(publication) == 0x80000011 and not word(state + 32)
    cpu.mem_write(REQUEST + 700, bytes(20))
    assert select() == 1 and word(state + 32) == 0x80000011
    assert bytes(cpu.mem_read(FILE_INFO, 48)) == bytes(cpu.mem_read(node, 48))
    assert bytes(cpu.mem_read(state, 16)) == struct.pack("<4I", *a)
    cpu.mem_write(state + 16, curr)
    assert select() == 1 and bytes(cpu.mem_read(state + 16, 16)) == curr
    previous = bytes(cpu.mem_read(state, 36))
    second = append(b, 0x12)
    assert word(publication) == 0x12 and bytes(cpu.mem_read(state, 36)) == previous
    assert bytes(cpu.mem_read(node, 84)) == b"\xef" * 84
    assert select(TEMP_INFO) == 1 and bytes(cpu.mem_read(state, 36)) == previous
    parser_result = -1
    assert select() == 0 and bytes(cpu.mem_read(state, 36)) == previous
    parser_result = 0
    assert select() == 1 and word(state + 32) == 0x12
    assert bytes(cpu.mem_read(state, 32)) == struct.pack("<4I", *b) + bytes(16)
    assert bytes(cpu.mem_read(FILE_INFO, 48)) == bytes(cpu.mem_read(second, 48))

    # Even a foreign FILE call with a convincing tail must not own timing.
    append(a, 0x3456, creator=0xacba0)
    assert word(publication) == 0x12
    assert select() == 1 and bytes(cpu.mem_read(state, 36)) == bytes(36)
    fail_size = 84
    assert append(a, 0x55) == 0
    assert word(publication) == 0x12

    # Submit a local track to RetailOS' real cache allocator, then use the
    # same hit-to-MFIL conversion the player invokes at 0xa9580/0xa9638.
    fail_size = None
    write_word(UI, LIBRARY)
    write_word(TRACK + 0xc, FILE)
    write_word(TRACK + 0x14, 123)
    write_word(TRACK + 0x70, 1500)
    cpu.mem_write(PATH + 4, b'B.m4a\0')
    write_word(0x1041b5c8, CACHE_ENTRY)
    pool = LIBRARY + 0xb64
    for offset, value in ((0, 0x4348554e), (8, 4096), (0x10, 1), (0x18, CACHE_CHUNK)):
        write_word(pool + offset, value)
    write_word(CACHE_CHUNK + 8, 4096)
    for start, arguments in ((0x2ef70, (LIBRARY, FILE, PATH, TRACK)),
                             (0x43c94, (UI, TRACK, TEMP_INFO, PATH))):
        cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
        cpu.reg_write(arm.UC_ARM_REG_LR, STOP)
        write_word(STACK, 0)
        for register, value in zip(REGS, arguments):
            cpu.reg_write(register, value)
        run(cpu, start, STOP)
        assert cpu.reg_read(REGS[0]) == 0
    assert word(CACHE_ENTRY + 4) == 0x74726368 and word(CACHE_ENTRY + 0x20) == CACHE_RANGE
    assert word(TEMP_INFO) == MFIL and word(UI + 0x29d0) == CACHE_ENTRY
    assert word(PATH + 0x110) == CACHE_ENTRY and word(PATH + 0x114) == 1500
    assert word(CACHE_ENTRY + 0x28) == 1
    mfil_name = f'{CACHE_ENTRY}.m4a'.encode()
    append(b, 0x56, source=MFIL)
    assert word(MANAGER + 0x20) == 0   # stock MFIL append's distinct notification
    assert select() == 1 and bytes(cpu.mem_read(state, 16)) == struct.pack("<4I", *b)
    assert word(publication) == word(state+32) == 0x56
    for address, value in ((TRACK+0xc, MFIL), (UI+0x29d0, 0), (TRACK+0x14, 124)):
        saved = word(address)
        write_word(address, value)
        append(a, 0x57, source=MFIL, eligible=False)
        write_word(address, saved)
        assert select() == 1 and bytes(cpu.mem_read(state, 36)) == bytes(36)
    append(a, 0x57, creator=0xacba0, source=MFIL)
    assert word(publication) == 0x56
    print("4G request → locked file list → decoder selection passed; tested replaced nodes, "
          "cache-backed local MFIL, manual/automatic cookies, retry, unrelated queries, "
          "foreign sources and allocation failure")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock_osos", type=Path)
    parser.add_argument("linked_elf", type=Path)
    args = parser.parse_args()
    staged = patch_requests(patch_osos(args.stock_osos.read_bytes()))
    with args.linked_elf.open("rb") as elf:
        image, symbols = extend_osos(staged, elf)
    install_binding(image, symbols)
    verify(image, symbols)
