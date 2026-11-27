"""Check archive preservation, startup memory bounds and every installed branch."""
import argparse
import ctypes
import mmap
import struct
from pathlib import Path
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_MODE_ARM, UC_PROT_ALL, Uc
from unicorn import arm_const as arm

from build import CONDITIONAL_HOOKS, HOOKS, MEMBER, OVERLAY_HOOKS
from image_layout import AUPD_ENTRY, BSS_BASE, END_MARKER, OSOS_ENTRY, SECTOR, TEXT_BASE, unpack_stock, word
from patch_engine_binding import BINDING_HOOKS


def verify_runtime_layout(osos, load_address, state_size, symbols):
    # The PP5020 can address the same SDRAM through cached and uncached
    # windows. Use one allocation so a copy through any alias can clobber
    # the code fetched through another. Unicorn does not model cache timing.
    ram_size = 0x600000  # Only the first 6 MiB of the 32 MiB are used here.
    ram = mmap.mmap(-1, ram_size)
    ram_ptr = ctypes.addressof(ctypes.c_char.from_buffer(ram))
    cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    for base in (0, 0x04000000, 0x10000000, 0x14000000):
        cpu.mem_map_ptr(base, ram_size, UC_PROT_ALL, ram_ptr)
    cpu.mem_write(load_address, osos)
    cpu.mem_map(0x40000000, 0x18000)  # SRAM
    cpu.mem_map(0x60000000, 0x1000)   # Processor ID
    cpu.mem_map(0x60006000, 0x1000)   # Cache priority
    cpu.mem_map(0x6000c000, 0x1000)   # Cache control
    cpu.mem_map(0xf0004000, 0x2000)   # Cache tags
    cpu.mem_map(0xf000f000, 0x1000)   # PP5020 address map registers
    scratch = 0x40017f00
    stop = 0x40017ffc

    def run(pc, end=stop, stack=0x40003ffc):
        cpu.reg_write(arm.UC_ARM_REG_SP, stack)
        cpu.reg_write(arm.UC_ARM_REG_LR, stop)
        cpu.emu_start(pc, end, count=3000000)
        actual_pc = cpu.reg_read(arm.UC_ARM_REG_PC)
        assert actual_pc == end, f"startup stopped at {actual_pc:#x}"

    # Both reset paths program the address map before dispatch by core ID.
    # Ask RetailOS's own map-entry translator to resolve each low address.
    # Entry 1 covers SDRAM. Its unmodified bit 26 selects the uncached alias.
    for core_id, dispatch, cache_init in ((0x55, 0x248, 0x10f7e0), (0xaa, 0x23c, 0x15ba68)):
        cpu.mem_write(0xf000f000, bytes(16))
        cpu.mem_write(0x60000000, bytes([core_id]))
        run(load_address, dispatch)
        for logical in (TEXT_BASE, len(osos) - 4, 0x04000000 + TEXT_BASE):
            cpu.mem_write(scratch, struct.pack("<I", logical))
            cpu.reg_write(arm.UC_ARM_REG_R0, 0xf000f008)
            cpu.reg_write(arm.UC_ARM_REG_R1, scratch)
            run(0x565dc)
            assert cpu.reg_read(arm.UC_ARM_REG_R0) == 1
            assert word(cpu.mem_read(scratch, 4), 0) == load_address + logical, f"bad mapping for {logical:#x}"

        # Construct the stock cache controller in caller-supplied storage.
        # Each core retrieves this object before the call sequence below.
        cpu.reg_write(arm.UC_ARM_REG_R0, scratch)
        run(0xf4610)
        cpu.mem_write(0xf000f040, bytes(4))
        cpu.mem_write(0x40003ffc, struct.pack("<I", scratch))
        run(cache_init, cache_init + 0x24)
        assert cpu.reg_read(arm.UC_ARM_REG_R0) == 0
        cache_match = word(cpu.mem_read(0xf000f040, 4), 0)
        assert cache_match == 0x0c00
        cache_mask = cache_match << 16
        assert (TEXT_BASE & cache_mask) == 0 and (BSS_BASE & cache_mask) != 0, "wrong playback cache aliases"

    # The memory planner at 0x350 places the first stack after static RAM.
    # 0x508 takes the same bank geometry supplied to 0x8a0 during reset.
    plan = 0x40006000
    cpu.mem_write(0x60000000, b"\x55")
    cpu.reg_write(arm.UC_ARM_REG_R0, plan)
    cpu.reg_write(arm.UC_ARM_REG_R1, 0x700)
    run(0x508)
    cpu.reg_write(arm.UC_ARM_REG_R0, plan)
    run(0x350)
    first_free = word(cpu.mem_read(plan + 0x28, 4), 0)
    # 0x350 keeps the uncached 0x14 alias when the boundary is already
    # 16-byte aligned and stores the 0x10 alias when it rounds up (stock).
    # 0x5c4 clears bit 26 before placing stacks, so both are equivalent.
    assert first_free & ~0x04000000 == ((BSS_BASE + state_size + 15) & ~15) & ~0x04000000, \
        f"wrong static RAM boundary {first_free:#x}"
    cpu.reg_write(arm.UC_ARM_REG_R0, plan)
    run(0x5c4)
    supervisor_stack = word(cpu.mem_read(plan + 0x2c + 2 * 8, 4), 0)

    cpu.mem_write(BSS_BASE, b"\xa5" * (state_size + 1))
    run(0x1fbe4)
    assert cpu.mem_read(BSS_BASE, state_size + 1) == bytes(state_size) + b"\xa5", "incorrect startup zero-fill"

    # MP3 and AAC reuse SRAM when their decoder is selected. Check the
    # persistent veneers, code and live state after each installed loader.
    live_state = b"\xa5" * state_size
    cpu.mem_write(BSS_BASE, live_state)
    for loader in (0x1fd54, 0x202e4):
        run(loader, stack=supervisor_stack)
        assert cpu.mem_read(TEXT_BASE, len(osos) - TEXT_BASE) == osos[TEXT_BASE:], "startup overwrote playback code"
        assert cpu.mem_read(BSS_BASE, state_size) == live_state, "codec loader overwrote playback state"
        for offset, _, _, symbol in OVERLAY_HOOKS:
            sram_pc = 0x40015350 + offset - 0x345d68
            assert cpu.mem_read(sram_pc, 8) == struct.pack("<II", 0xe51ff004, symbols[symbol]), symbol


def verify(stock_ipsw, patched_ipsw, elf_file):
    with ZipFile(stock_ipsw) as source, ZipFile(patched_ipsw) as output:
        assert source.namelist() == output.namelist() and output.testzip() is None
        for name in source.namelist():
            if name != MEMBER:
                assert source.read(name) == output.read(name)
        original, firmware = source.read(MEMBER), output.read(MEMBER)
    stock = unpack_stock(original)
    begin = SECTOR + word(firmware, OSOS_ENTRY + 12)
    length = word(firmware, OSOS_ENTRY + 16)
    osos = firmware[begin:begin + length]
    assert osos.endswith(END_MARKER) and word(osos, 0xe8) == length
    assert sum(osos) & 0xffffffff == word(firmware, OSOS_ENTRY + 28)
    shift = len(firmware) - len(original)
    assert shift % SECTOR == 0
    assert word(firmware, AUPD_ENTRY + 12) - word(original, AUPD_ENTRY + 12) == shift
    assert firmware[begin + len(stock) + shift:] == original[begin + len(stock):]
    header = bytearray(firmware[:begin])
    for at in (OSOS_ENTRY + 16, OSOS_ENTRY + 28, AUPD_ENTRY + 12):
        header[at:at + 4] = original[at:at + 4]
    assert header == original[:begin]

    elf = ELFFile(elf_file)
    symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols()}
    text = elf.get_section_by_name(".text")
    assert osos[TEXT_BASE:-4] == text.data()
    sites = [(pc, symbol, link, 14) for pc, _, symbol, link in HOOKS]
    sites.extend((pc, symbol, kind == "bl", 14) for pc, symbol, kind in BINDING_HOOKS)
    sites.extend((pc, symbol, link, cond) for pc, _, symbol, link, cond in CONDITIONAL_HOOKS)
    for pc, name, link, condition in sites:
        instruction = word(osos, pc)
        assert instruction >> 24 == (condition << 4) | (0xb if link else 0xa)
        displacement = ((instruction & 0xffffff) ^ 0x800000) - 0x800000
        assert pc + 8 + 4 * displacement == symbols[name], name
    for pc, _, _, name in OVERLAY_HOOKS:
        assert word(osos, pc) == 0xe51ff004 and word(osos, pc + 4) == symbols[name]
    assert word(osos, 0x2e8) == word(osos, 0x4a8) == 0x04000000 + length - 4
    assert length < word(osos, 0x4ac) & 0xffffff

    size = elf.get_section_by_name(".bss")["sh_size"]
    assert word(osos, 0x4b0) == BSS_BASE + size
    verify_runtime_layout(osos, word(firmware, OSOS_ENTRY + 0x14), size, symbols)
    print(f"Verified archive, updater, {len(sites) + len(OVERLAY_HOOKS)} hooks, CPU/COP memory maps, "
          f"MP3/AAC overlays and {size} bytes of initialized state")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock_ipsw", type=Path)
    parser.add_argument("patched_ipsw", type=Path)
    parser.add_argument("elf", type=Path)
    args = parser.parse_args()
    with args.elf.open("rb") as file:
        verify(args.stock_ipsw, args.patched_ipsw, file)
