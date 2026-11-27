"""Feed marked decoded PCM into the installed 4G pump and collect its actual I2S spans."""
import argparse
import struct
from pathlib import Path

from unicorn import UC_HOOK_CODE
from unicorn import arm_const as arm

from verify_tx import (DESCRIPTORS, OUT, OVERLAYS, QUEUE, REG, RING,
                       STACK, Machine)

DECODER, VTABLE, CALLBACKS = 0x10034000, 0x10035000, 0x10036000
NODE, SOURCE, ENGINE = 0x10038000, 0x10039000, 0x144a91fc
FRAMES_PER_BLOCK = 1152
WIRE_BYTES = FRAMES_PER_BLOCK * 4
SLOT_BYTES = WIRE_BYTES * 2  # MP3 decoder scratch before stereo16 packing


class Album:
    def __init__(self, machine, installed):
        self.m = m = machine
        # Use the actual candidate, including branch encoding and SRAM relocation.
        m.cpu.mem_write(0, installed)
        def breakpoint(cpu, pc, size, user):
            if pc == m.finish:
                cpu.emu_stop()
        for address in (0x1164a4, 0x116490, 0x1165c0, 0x115ed8, 0x116008, 0x11640c):
            m.cpu.hook_add(UC_HOOK_CODE, breakpoint, begin=address, end=address)
        for address, offset, length in OVERLAYS:
            m.cpu.mem_write(address, installed[offset:offset + length])
        self.frames = []
        self.ready = False
        self.captured = []
        m.write(DECODER, VTABLE)
        for method in (8, 12, 16, 20):
            address = CALLBACKS + method
            m.write(VTABLE + method, address - VTABLE)
            m.cpu.hook_add(UC_HOOK_CODE, self.codec, begin=address, end=address)
        # No external message or scheduler interaction in this fixture.
        for address in (0x11c518, 0x14942c, 0x11b648, 0x133bf4, 0x11b688):
            m.cpu.hook_add(UC_HOOK_CODE, self.empty_queue, begin=address, end=address)
        m.cpu.mem_map(0x144a9000, 0x9000)
        m.write(QUEUE + 0x5c, ENGINE)
        m.write(QUEUE + 0xc0, 1)  # primary playback route is configured
        m.write(ENGINE + 0x2e80, QUEUE)
        m.write(ENGINE + 0x2e7c, SOURCE)
        m.write(ENGINE + 0x1940, 1)
        m.write(ENGINE + 0x90cc, FRAMES_PER_BLOCK)
        m.write(ENGINE + 0x90c8, SLOT_BYTES)
        m.write(0x144b22ec, WIRE_BYTES)
        m.reset(SLOT_BYTES)

    def return_to_caller(self, value):
        cpu = self.m.cpu
        cpu.reg_write(REG[0], value)
        cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))

    def empty_queue(self, cpu, pc, size, user):
        self.return_to_caller(0)

    def codec(self, cpu, pc, size, user):
        method = pc - CALLBACKS
        if method == 12:
            assert self.frames and not self.ready
            cpu.mem_write(cpu.reg_read(REG[1]), struct.pack(f"<{FRAMES_PER_BLOCK}I", *self.frames.pop(0)))
            self.ready = True
            result = 1
        elif method == 16:
            result = int(self.ready)
            self.ready = False
        elif method == 20:
            result = int(not self.frames and not self.ready)
        else:
            result = 0
        self.return_to_caller(result)

    def file(self, cookie, delay, count):
        m = self.m
        m.cpu.mem_write(NODE + 0x40, struct.pack("<5I", delay, count, 0, 1, cookie))
        m.cpu.mem_write(NODE + 0x18, bytes([0]))  # Layer III
        m.write(NODE + 4, 44100)
        m.cpu.mem_write(NODE + 12, bytes([2, 16]))
        m.cpu.reg_write(REG[4], NODE)
        m.call("gapless_note_append")

    def select(self):
        m = self.m
        m.cpu.reg_write(REG[6], NODE)
        m.cpu.reg_write(REG[7], ENGINE + 0x2e3c)
        m.call("gapless_bind_selection", ENGINE + 0x2e3c, NODE, 48)
        m.call(0x14f5d0, QUEUE, ENGINE + 0x2e3c)

    def song(self, frames, *, remaining=0):
        m = self.m
        self.frames = list(frames)
        m.prepare(ENGINE, DECODER)
        m.run(0x1164a0, 0x1164a4)
        assert not self.frames
        cursor = m.symbols["gapless_engine_timing"] + 16
        assert m.read(cursor + 4) == remaining, (
            struct.unpack("<4I", m.cpu.mem_read(cursor, 16)), m.statuses(),
            m.read(ENGINE + 0x90c8), m.read(ENGINE + 0x90cc),
            m.read(0x144b22ec), m.read(QUEUE + 0xd0), m.read(QUEUE + 0xd4))

    def dispatch(self, fifo_index):
        m = self.m
        assert not m.call(0x40015528, QUEUE, OUT)
        assert not m.call(0x4001556c, QUEUE)
        assert not m.call(0x1333d4, QUEUE, 0, WIRE_BYTES)
        address, length = m.fifo(fifo_index)
        assert address and 0 < length <= WIRE_BYTES and length % 4 == 0, (
            fifo_index, [m.fifo(i) for i in range(4)], m.statuses(),
            m.read(m.symbols["gapless_engine_timing"] + 28))
        self.captured.extend(struct.unpack(f"<{length // 4}I", m.cpu.mem_read(address, length)))
        return address, length


def verify(m, installed, b_count=550):
    album = Album(m, installed)
    album.file(0x80000001, 1, 700)
    album.select()
    album.song((range(0, 1152), range(1152, 2304)))
    first, second = album.dispatch(0), album.dispatch(1)
    # A is still playing. B sees the slot already reserved by A's decoder.
    prior = m.read(ENGINE + 0x1948)
    assert prior and m.read(DESCRIPTORS + 32 + 8) == 7
    # B is queued, as if the host task has delivered its next-song request.
    # Drive the installed EOF decision, decoder match and both ring setup paths.
    m.write(m.symbols["gapless_handoff_state"], 2)
    album.file(2, 2, b_count)
    m.prepare(1)
    for register, value in ((4, ENGINE + 0x2000), (5, ENGINE), (6, ENGINE + 0x9000),
                            (7, DECODER), (8, ENGINE + 0x1000), (11, 0)):
        m.cpu.reg_write(REG[register], value)
    m.write(STACK + 0x68, 0)
    m.run(0x11656c, 0x1165c0)
    album.select()
    m.cpu.reg_write(REG[7], DECODER)
    m.cpu.reg_write(REG[4], ENGINE + 0x2000)
    m.cpu.reg_write(REG[6], ENGINE + 0x9000)
    m.prepare(0)
    m.run(0x115ed0, 0x115ed8)
    m.run(0x115ff8, 0x116008)
    m.cpu.reg_write(REG[0], SLOT_BYTES)
    m.run(0x116400, 0x11640c)
    m.run(0x116480, 0x116490)
    assert m.read(ENGINE + 0x1948) == prior, "unfilled producer slot lost at the handoff"
    assert m.read(ENGINE + 0x1940) == 1
    album.song((range(10000, 11152),))
    third = album.dispatch(2)
    assert album.captured == list(range(530, 1230)) + list(range(10531, 10531 + b_count))
    for pointer, length in (first, second, third):
        m.done(pointer, length)
    print(f"4G decode pump → retained ring → I2S FIFO delivered A's 700 and B's {b_count} frames in order")
    return album.captured


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock", type=Path)
    parser.add_argument("installed", type=Path)
    parser.add_argument("elf", type=Path)
    args = parser.parse_args()
    with args.elf.open("rb") as file:
        verify(Machine(args.stock.read_bytes(), file, 0x21f3c), args.installed.read_bytes())
