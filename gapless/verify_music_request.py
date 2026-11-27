"""Compare 4G music request allocation and dispatch in the offline ARM emulator."""
import argparse
import struct
from pathlib import Path

from unicorn import UC_HOOK_CODE
from unicorn import arm_const as arm
from verify_track_metadata import METADATA, REGS, STACK, STOP, TRACK, WORD, machine, run

ENGINE_GLOBALS = 0x103c99a8
ENGINE = 0x41a000
STATUS = 0x1039030c


def getword(cpu, at):
    return WORD.unpack(cpu.mem_read(at, 4))[0]


def verify_request(stock, patched):
    def one(cpu, flags, attr, alloc, fail=False):
        observations = {}
        cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
        for n in range(13):
            cpu.reg_write(REGS[n], 0x77777777)
        cpu.reg_write(REGS[0], 0x3434)
        cpu.reg_write(REGS[5], TRACK)
        cpu.reg_write(REGS[6], 100)
        cpu.reg_write(REGS[8], 150)
        cpu.reg_write(REGS[7], TRACK+0x1000)
        cpu.reg_write(REGS[9], TRACK+0x2000)
        cpu.reg_write(REGS[10], 123)
        cpu.reg_write(REGS[11], 0x46494c45)
        cpu.mem_write(TRACK+0x11, bytes([attr]))
        cpu.mem_write(STACK+0x30, WORD.pack(flags))
        cpu.mem_write(STACK+0x244, WORD.pack(0xdeed))
        def heap(uc, pc, size, data):
            if pc == 0x9b8e8:
                observations['size'] = uc.reg_read(REGS[0])
                uc.reg_write(REGS[0], 0 if fail else alloc)
                uc.reg_write(arm.UC_ARM_REG_PC, uc.reg_read(arm.UC_ARM_REG_LR))
        hook = cpu.hook_add(UC_HOOK_CODE, heap)
        run(cpu, 0xa7d98, 0xa7eec if fail else 0xdc43c)
        cpu.hook_del(hook)
        if not fail:
            observations['arguments'] = tuple(cpu.reg_read(x) for x in REGS[:4])
            observations['stack_arguments'] = bytes(cpu.mem_read(STACK, 32))
            observations['legacy'] = bytes(cpu.mem_read(alloc, 700))
        else:
            assert cpu.reg_read(REGS[6]) == (2**32-108)
        return observations

    a, b = machine(stock), machine(patched)
    for cpu in (a,b):
        cpu.mem_map(0x103c9000, 0x1000)
        cpu.mem_map(0x10390000, 0x1000)
    pairs = [(0x416000, (576, 999, 1, 455)), (0x417000, (2112, 45678, 0, 181))]
    for alloc, fields in pairs:
        for flags in (0,4):
            for attr in (0,4):
                for cpu in (a,b):
                    cpu.mem_write(TRACK+0xec, METADATA.pack(*fields))
                    cpu.mem_write(alloc, bytes([0x75]) * 720)
                baseline = one(a, flags, attr, alloc)
                result = one(b, flags, attr, alloc)
                assert result.pop('size') == 716, result
                assert baseline.pop('size') == 700
                assert baseline == result, (baseline, result)
                assert METADATA.unpack(b.mem_read(alloc+700, 16)) == fields
                assert bytes(b.mem_read(alloc+716, 4)) == b'uuuu'
    one(b, 0, 0, 0x416000, fail=True)
    b.mem_write(TRACK+0xec, bytes(16))
    # Drive the actual busy branch and subsequent event-handler retry.
    alloc, fields = pairs[1]
    b.mem_write(alloc+0x14, WORD.pack(0x74706c79))
    b.mem_write(ENGINE_GLOBALS+0x14, WORD.pack(1))
    b.reg_write(REGS[0], alloc+0x14)
    b.reg_write(arm.UC_ARM_REG_LR, STOP)
    run(b, 0xdcc74, STOP)
    assert getword(b, ENGINE_GLOBALS+0x1c) == alloc+0x14
    b.mem_write(ENGINE_GLOBALS+0x14, WORD.pack(0))
    b.reg_write(REGS[6], ENGINE_GLOBALS)
    b.reg_write(REGS[7], 0)
    b.ctl_remove_cache(0xdcc74, 0xdcd00)
    run(b, 0x7c4e0, 0xdcc74)
    assert b.reg_read(REGS[0]) == alloc+0x14
    assert METADATA.unpack(b.mem_read(b.reg_read(REGS[0])+0x2a8, 16)) == fields
    assert getword(b, ENGINE_GLOBALS+0x1c) == 0
    assert METADATA.unpack(b.mem_read(pairs[0][0]+700, 16)) == pairs[0][1]
    print("Two independent track requests, 8 constructor argument comparisons, allocation failure, busy dispatch and retry passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock_osos", type=Path)
    parser.add_argument("patched_osos", type=Path)
    args = parser.parse_args()
    verify_request(args.stock_osos.read_bytes(), args.patched_osos.read_bytes())
