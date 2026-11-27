"""Compare a linked ARM build of clip_pcm.c with the stock 5.5G sample window."""
import argparse
import hashlib
import struct
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_ARM, Uc
from unicorn import arm_const as arm

REFERENCE_HASH = "784ae3d5540fd2f89e8c947b93629e97f62a06dc311d9745aab143e4db6bb251"
HEAP = 0x900000
CURSOR, TIMING, RING, SLOT = (HEAP + x for x in (0, 0x100, 0x200, 0x300))
SP, STOP = HEAP + 0x1f000, HEAP + 0x1f100
REG = [getattr(arm, f"UC_ARM_REG_R{i}") for i in range(13)]
WORDS = struct.Struct("<4I")


def call(cpu, pc, *args):
    for reg, arg in zip(REG, args):
        cpu.reg_write(reg, arg)
    cpu.reg_write(arm.UC_ARM_REG_SP, SP)
    cpu.reg_write(arm.UC_ARM_REG_LR, STOP)
    execute(cpu, pc, STOP)
    return cpu.reg_read(REG[0])


def execute(cpu, pc, end):
    cpu.emu_start(pc, end, count=1000)
    assert cpu.reg_read(arm.UC_ARM_REG_PC) == end


class Port:
    def __init__(self, file):
        elf = ELFFile(file)
        assert elf["e_machine"] == "EM_ARM"
        symbols = elf.get_section_by_name(".symtab")
        self.symbol = {s.name: s["st_value"] for s in symbols.iter_symbols()}
        text = elf.get_section_by_name(".text")
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.cpu.mem_map(0, 0x800000)
        self.cpu.mem_write(text["sh_addr"], text.data())
        self.cpu.mem_map(HEAP, 0x20000)
        self.span = None
        self.accept = True
        publish = self.symbol["gapless_arm_span"]
        self.cpu.hook_add(UC_HOOK_CODE, self.publish, begin=publish, end=publish)

    def publish(self, cpu, pc, size, user):
        assert (cpu.reg_read(REG[0]), cpu.reg_read(REG[1])) == (RING, SLOT)
        self.span = (cpu.reg_read(REG[2]), cpu.reg_read(REG[3]))
        cpu.reg_write(REG[0], int(self.accept))
        cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))

    def start(self, delay, count, padding, bias=0):
        self.cpu.mem_write(TIMING, WORDS.pack(delay, count & 0xffffffff, count >> 32, padding))
        call(self.cpu, self.symbol["gapless_start_clip"], CURSOR, TIMING, bias)

    def block(self, frames):
        self.span = None
        return call(self.cpu, self.symbol["gapless_clip_stereo16"], CURSOR, RING, SLOT, frames)

    def state(self):
        return WORDS.unpack(self.cpu.mem_read(CURSOR, WORDS.size))


class Reference:
    def __init__(self, firmware):
        if hashlib.sha256(firmware).hexdigest() != REFERENCE_HASH:
            raise ValueError("expected stock 5.5G OSOS from iPod_25.1.3.ipsw")
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.cpu.mem_map(0, 0x740000)
        self.cpu.mem_write(0, firmware)
        self.cpu.mem_map(HEAP, 0x20000)

    def start(self, delay, count, padding):
        self.cpu.mem_write(HEAP + 0x90b4, struct.pack("<3I", count, padding, delay))
        self.cpu.mem_write(HEAP + 0x1948, bytes(1))

    def block(self, position, size):
        cpu = self.cpu
        for index, value in ((4, HEAP + 0x1000), (8, HEAP + 0x9000),
                             (6, size), (7, size), (11, 0)):
            cpu.reg_write(REG[index], value)
        cpu.mem_write(HEAP + 0x193c, struct.pack("<I", position))
        execute(cpu, 0x723330, 0x7233c0)
        return cpu.reg_read(REG[11]) * 4, cpu.reg_read(REG[7]) * 4


def verify(port, reference):
    for block in (576, 1024, 1152):
        for delay in (1, 256, 529, 576, 1105, 2112):
            for count in (44100, 44101, 115199, 115200):
                port.start(delay, count, 352)
                reference.start(delay, count, 352)
                emitted = 0
                for position in range(0, delay + count + block, block):
                    assert port.block(block)
                    skip, keep = port.span
                    if reference.cpu.mem_read(HEAP + 0x1948, 1)[0] == 2:
                        assert keep == 0
                    else:
                        ref_skip, ref_keep = reference.block(position, block)
                        assert keep == ref_keep and (not keep or skip == ref_skip)
                    if keep:
                        assert position + skip // 4 == delay + emitted
                        emitted += keep // 4
                assert emitted == count
    print("4G ARM code and original 5.5G code agree on 72 track windows")

    for delay, count, padding, bias in ((0, 0, 0, 0), (0, 100, 2, 529),
                                        (100, 0, 3, 0), (0xffffffff, 2000, 0, 529)):
        port.start(delay, count, padding, bias)
        assert port.block(1152) and port.span is None
    port.start(576, (1 << 32) + 1, 352, 529)
    initial = port.state()
    port.accept = False
    assert not port.block(1152) and port.span == (1105 * 4, 47 * 4)
    assert port.state() == initial
    port.accept = True
    assert port.block(1152) and port.span == (1105 * 4, 47 * 4)
    assert port.state() == (0, (1 << 32) - 46, 0, 1)
    before = port.state()
    port.accept = False
    assert not port.block(1152) and port.span == (0, 1152 * 4)
    assert port.state() == before
    port.accept = True
    assert port.block(1152) and port.state() == (0, before[1] - 1152, 0, 1)
    before = port.state()
    assert not port.block(1 << 30) and port.span is None and port.state() == before
    port.start(2, 5, 1)
    assert port.block(1152) and port.span == (8, 20)
    assert port.block(1152) and port.span == (0, 0)
    port.start(100, 103, 3)
    assert port.block(1152) and port.span == (400, 412)
    print("Validated retry, 64-bit sample count, short tracks and untagged input")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clip_elf", type=Path)
    parser.add_argument("reference_osos", type=Path)
    args = parser.parse_args()
    with args.clip_elf.open("rb") as file:
        verify(Port(file), Reference(args.reference_osos.read_bytes()))
