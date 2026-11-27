"""Exercise the 4G PCM descriptor, DSP byte count, I2S FIFO, IRQ and reset paths."""

import argparse
import hashlib
import struct
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MODE_ARM, Uc
from unicorn import arm_const as arm

STOCK_SHA256 = "e6068744c2922845029591b54834b5447425701441daee1a7f11cfc5ba4bbd2a"
QUEUE, RING, DESCRIPTORS, OUT = (0x10000000 + i * 0x1000 for i in range(4))
BUFFER, ENGINE = 0x10010000, 0x10020000
DRIVER, I2S, TX, FIFO, LISTENER, CPUID = (0x10040000 + i * 0x1000 for i in range(6))
STACK, RESET_STACK, IRQ_STACK, RETURN = 0x1007fe00, 0x1007de00, 0x1007be00, 0x1007ff00
REG = [getattr(arm, f"UC_ARM_REG_R{i}") for i in range(13)]
HOOKS = (
    (0x133548, 0xe1a02007, "tx_submit_hook", False),
    (0x133be8, 0xebfbb33a, "gapless_release", True),
    (0x15a88c, 0xe59400e0, "tx_poll_hook", False),
    (0x158040, 0xe1a0f003, "tx_cancel_hook", False),
    (0x14dd08, 0xe584302c, "tx_reset_hook", False),
    (0x14dd8c, 0xe1a00007, "tx_reset_done_hook", False),
    (0x158068, 0xe5845014, "tx_flush_hook", False),
)
# Stock 0x1fc40/0x1fc6c relocations and the runtime software arithmetic overlay.
OVERLAYS = (
    (0x40000000, 0x33bf14, 0xf1c),
    (0x40015350, 0x345d68, 0x84c),
    (0x40016ca8, 0x355d30, 0x11e0),
)


class Machine:
    def __init__(self, osos, elf_file, frontend):
        if hashlib.sha256(osos).hexdigest() != STOCK_SHA256:
            raise ValueError("expected stock 4G monochrome 4.3.1.1 OSOS")
        elf = ELFFile(elf_file)
        assert elf["e_machine"] == "EM_ARM"
        symbols = elf.get_section_by_name(".symtab").iter_symbols()
        self.symbols = {s.name: s["st_value"] for s in symbols}
        code = elf.get_section_by_name(".text")
        cpu = self.cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.finish = RETURN
        for boundary in (RETURN, 0x106818, 0x106868, 0x13bc00):
            cpu.hook_add(UC_HOOK_CODE, self.stop_at_boundary, begin=boundary, end=boundary)
        for addr, size in ((0, 0x800000), (QUEUE, 0x80000), (0x40000000, 0x20000),
                           (0x144b2000, 0x1000), (0x60000000, 0x1000), (0x60005000, 0x1000)):
            cpu.mem_map(addr, size)
        # Map every page of the extension's static RAM.
        bss = elf.get_section_by_name(".bss")
        state_page = bss["sh_addr"] & ~0xfff
        state_end = bss["sh_addr"] + bss["sh_size"]
        cpu.mem_map(state_page, ((state_end - state_page + 4095) // 4096) * 4096)
        cpu.mem_write(0, osos)
        cpu.mem_write(code["sh_addr"], code.data())
        for target, source, length in OVERLAYS:
            cpu.mem_write(target, osos[source:source + length])
        # The original output-path hooks, where an image still links them.
        # Fixtures that load an installed image overwrite this low memory.
        for addr, stock, name, link in HOOKS:
            if name not in self.symbols:
                continue
            assert self.read(addr) == stock
            delta = self.symbols[name] - addr - 8
            assert delta % 4 == 0 and -(1 << 25) <= delta < (1 << 25)
            self.write(addr, (0xeb000000 if link else 0xea000000) | ((delta >> 2) & 0xffffff))
        for address, value in (
            (QUEUE + 0x60, RING), (RING + 0x1c, 3), (RING + 0x30, DESCRIPTORS),
            (RING + 0x38, BUFFER), (DRIVER, frontend), (DRIVER + (0x1c if frontend == 0x21eb4 else 0x38), I2S),
            (I2S, 0x236ec), (I2S + 0xc, 1), (I2S + 0x18, TX), (QUEUE + 0x70, DRIVER),
            (QUEUE + 0xd0, 2), (TX + 8, FIFO), (TX + 12, 455), (TX + 0x3c, CPUID),
            (LISTENER, 0x25a74), (LISTENER + 4, 1), (LISTENER + 12, RING),
            (QUEUE + 0x5c, ENGINE),
        ):
            self.write(address, value)
        cpu.mem_write(QUEUE + 0x94, bytes((2, 16)))
        cpu.mem_write(CPUID, bytes((85,)))
        cpu.mem_write(0x60000000, b'\xaa')  # COP by default; worker fixtures switch to CPU.
        cpu.mem_write(RING + 0x24, bytes((3,)))

    def stop_at_boundary(self, cpu, address, size, userdata):
        if address == self.finish:
            cpu.emu_stop()

    def read(self, address):
        return struct.unpack("<I", self.cpu.mem_read(address, 4))[0]

    def write(self, address, value):
        self.cpu.mem_write(address, struct.pack("<I", value))

    def prepare(self, *args, stack=STACK):
        self.cpu.reg_write(arm.UC_ARM_REG_SP, stack)
        self.cpu.reg_write(arm.UC_ARM_REG_LR, RETURN)
        for reg, arg in zip(REG[:4], args):
            self.cpu.reg_write(reg, arg)
        if len(args) > 4:
            self.write(stack, args[4])

    def run(self, start, finish=RETURN):
        self.finish = finish
        self.cpu.emu_start(start, RETURN, count=100000)
        pc = self.cpu.reg_read(arm.UC_ARM_REG_PC)
        assert pc == finish, f"from {start:#x}, stopped at {pc:#x}, expected {finish:#x}"
        return self.cpu.reg_read(REG[0])

    def call(self, address, *args, stack=STACK):
        self.prepare(*args, stack=stack)
        return self.run(self.symbols[address] if isinstance(address, str) else address)

    def reset(self, capacity=24, wire_bytes=None):
        self.capacity = capacity
        if wire_bytes is not None:
            self.write(0x144b22ec, wire_bytes)
        self.write(RING + 0x40, capacity)
        self.write(RING + 0x44, capacity)
        self.write(RING + 0x4c, 3 * capacity)
        assert self.call(0x14dcd4, RING, 0, 0, capacity, 0) == 0
        self.cpu.mem_write(FIFO, bytes(4 * 8))
        self.write(TX + 0x14, 0)

    def ready(self, idx, skip, keep):
        assert self.call(0x40017a30, QUEUE, OUT) == 0
        assert self.read(OUT) == BUFFER + idx * self.capacity
        assert self.call("gapless_arm_span", RING, DESCRIPTORS + 16 * idx, skip, keep) == 1
        assert self.call(0x400154f0, QUEUE) == 0
        assert self.call(0x40015528, QUEUE, OUT) == 0
        assert self.call(0x4001556c, QUEUE) == 0

    def submit(self, idx, skip, keep, wire_bytes=12):
        self.ready(idx, skip, keep)
        assert self.call(0x1333d4, QUEUE, 0, wire_bytes) == 0

    def done(self, addr, length):
        self.call(0x133ba4, LISTENER, addr, length, stack=IRQ_STACK)

    def statuses(self):
        return [self.read(DESCRIPTORS + 16*i + 8) for i in range(3)]

    def fifo(self, index):
        return struct.unpack("<II", self.cpu.mem_read(FIFO + 8*index, 8))


def verify_core_lock(m):
    # Unicorn implements SWP as a working atomic exchange. Reject it on the
    # executed first-play path, where that assumption is invalid on PP5020.
    saved_context = m.cpu.context_save()
    saved_id = bytes(m.cpu.mem_read(0x60000000, 1))
    forbidden = []
    def check_swap(cpu, pc, size, unused):
        if m.read(pc) & 0x0fb00ff0 == 0x01000090:
            forbidden.append(pc)
            cpu.emu_stop()

    hook = m.cpu.hook_add(UC_HOOK_CODE, check_swap)
    try:
        m.prepare(RING)
        m.cpu.emu_start(m.symbols["gapless_reset_ring"], RETURN, count=10000)
        assert not forbidden, f"first ring reset executes PP5020-unsafe SWP at {forbidden[0]:#x}"
        assert m.cpu.reg_read(arm.UC_ARM_REG_PC) == RETURN
        m.call("gapless_reset_done", RING)
    finally:
        m.cpu.hook_del(hook)

    # Pause both real ARM contexts just after publishing interest. A read of
    # retry_slot is inside the protected region; dropping interest ends it.
    # Run both tie orders, using the actual CPU/COP IDs and distinct stacks.
    for first in (0, 1):
        ids = (0x55, 0xaa)
        masks = (0x00, 0x40)
        contexts = []
        claimed, entered, returned = set(), [], set()
        owner = None
        active = None
        def watch_read(cpu, access, address, size, value, unused):
            nonlocal owner
            assert owner is None and active not in entered, "both cores entered the TX lock"
            assert cpu.reg_read(arm.UC_ARM_REG_CPSR) & 0xc0 == 0xc0
            owner = active
            entered.append(active)

        def watch_interest(cpu, access, address, size, value, unused):
            nonlocal owner
            assert address == m.state + active and size == 1
            if value:
                claimed.add(active)
            else:
                assert owner == active
                owner = None

        for core in range(2):
            m.prepare(QUEUE, stack=(STACK, RESET_STACK)[core])
            flags = m.cpu.reg_read(arm.UC_ARM_REG_CPSR)
            m.cpu.reg_write(arm.UC_ARM_REG_CPSR, (flags & ~0xc0) | masks[core])
            m.cpu.reg_write(arm.UC_ARM_REG_PC, m.symbols["gapless_retry"])
            contexts.append(m.cpu.context_save())
        readers = m.cpu.hook_add(UC_HOOK_MEM_READ, watch_read, begin=m.state + 8, end=m.state + 8)
        writers = m.cpu.hook_add(UC_HOOK_MEM_WRITE, watch_interest, begin=m.state, end=m.state + 1)
        def step(core):
            nonlocal active
            active = core
            m.cpu.context_restore(contexts[core])
            m.cpu.mem_write(0x60000000, bytes([ids[core]]))
            m.cpu.emu_start(m.cpu.reg_read(arm.UC_ARM_REG_PC), RETURN, count=1)
            contexts[core] = m.cpu.context_save()
            if m.cpu.reg_read(arm.UC_ARM_REG_PC) == RETURN:
                assert m.cpu.reg_read(arm.UC_ARM_REG_CPSR) & 0xc0 == masks[core]
                returned.add(core)

        try:
            for core in (first, 1 - first):
                for _ in range(100):
                    step(core)
                    if core in claimed:
                        break
                assert core in claimed and not entered
            for _ in range(200):
                for core in (first, 1 - first):
                    if core not in returned:
                        step(core)
                if len(returned) == 2:
                    break
            assert sorted(entered) == [0, 1] and len(returned) == 2
            assert owner is None and bytes(m.cpu.mem_read(m.state, 2)) == b'\0\0'
        finally:
            m.cpu.hook_del(readers)
            m.cpu.hook_del(writers)
    m.cpu.mem_write(0x60000000, saved_id)
    m.cpu.context_restore(saved_context)
    print("First ring reset avoids PP5020 SWP; both CPU/COP contention orders release and restore masks")


def verify_dsp(m):
    m.reset(9216)
    # Stereo MP3's genuine 0x14ce68 sets the scratch, wire and ring extents.
    assert m.call(0x14ce68, QUEUE, 9216) != 0
    assert (m.read(0x144b22e8), m.read(0x144b22ec), m.read(RING + 0x2c)) == (9216, 4608, 9216)
    # DSP bypass and the genuine 16-bit result-size routine used with DSP active.
    assert m.call(0x115b68, ENGINE, QUEUE + 0x88, BUFFER, 4608) == 4608
    m.cpu.mem_write(ENGINE + 0x2e48, bytes((2, 16)))
    assert m.call(0x113d5c, ENGINE, BUFFER, BUFFER, 1152) == 4608
    m.submit(0, 529*4, (1152-529)*4, wire_bytes=4608)
    assert m.fifo(0) == (BUFFER + 529*4, (1152-529)*4)
    assert m.read(0x40017e8c) == BUFFER + 9216
    m.done(*m.fifo(0))


def verify_order(m):
    m.reset(wire_bytes=12)
    m.submit(0, 4, 8)
    m.submit(1, 0, 0)
    m.submit(2, 0, 4)
    assert (m.fifo(0), m.fifo(1)) == ((BUFFER + 4, 8), (BUFFER + 48, 4))
    assert m.read(TX + 0x14) == 2 and m.statuses() == [9, 9, 9]
    assert m.read(0x40017e8c) == BUFFER
    m.done(*m.fifo(0))
    assert m.statuses() == [3, 3, 9]
    m.done(*m.fifo(1))
    assert m.statuses() == [3, 3, 3]
    m.reset(wire_bytes=12)
    m.submit(0, 0, 0)
    assert m.read(TX + 0x14) == 0 and m.statuses() == [3, 3, 3]
    assert not m.call("gapless_arm_span", RING, DESCRIPTORS, 0, 28)
    assert not m.call("gapless_arm_span", RING, DESCRIPTORS, 2, 4)
    assert not m.call("gapless_arm_span", RING, DESCRIPTORS + 16, 12, 4)
    # The requested (offset, length) would fit even after the DSP changed
    # the frame count. Sample indices no longer correspond, so play the
    # entire native post-DSP buffer for this descriptor.
    m.submit(1, 4, 4, wire_bytes=8)
    assert m.fifo(0) == (BUFFER + 24, 8)
    m.done(*m.fifo(0))


def verify_retry(m):
    m.reset(wire_bytes=12)
    m.write(FIFO, 0xcafebabc)
    m.submit(0, 0, 8)
    assert m.read(m.state + 8) == 1 and m.statuses() == [9, 3, 3]
    assert m.call("gapless_retry", QUEUE) == 1
    m.write(FIFO, 0)
    assert not m.call("gapless_retry", QUEUE)
    assert m.fifo(0) == (BUFFER, 8)
    assert not m.call("gapless_retry", QUEUE) and m.read(TX + 0x14) == 1
    m.done(*m.fifo(0))
    m.reset(wire_bytes=12)
    abandoned = m.read(m.state + 40)
    m.write(FIFO, 0xcafebabc)
    m.submit(0, 0, 4)
    for _ in range(7):
        assert m.call("gapless_retry", QUEUE) == 1
    assert m.call("gapless_retry", QUEUE) == 0
    assert m.statuses() == [3, 3, 3] and m.read(m.state + 40) == abandoned + 1
    assert m.read(TX + 0x14) == 0


def verify_worker_poll(m):
    entry, resume = 0x15a88c, 0x15a890
    stop = m.cpu.hook_add(UC_HOOK_CODE, m.stop_at_boundary, begin=resume, end=resume)

    def poll(native_count):
        m.write(QUEUE + 0xe0, native_count)
        saved = (11, 22, 33)
        m.prepare(0, *saved)
        m.cpu.reg_write(REG[4], QUEUE)
        assert m.run(entry, resume) == native_count
        assert tuple(m.cpu.reg_read(reg) for reg in REG[1:4]) == saved
        assert m.cpu.reg_read(arm.UC_ARM_REG_SP) == STACK
        assert m.cpu.reg_read(arm.UC_ARM_REG_LR) == RETURN

    m.reset(wire_bytes=12)
    # An idle lock may retain either turn. It must not send every poll
    # through the lock even when its third byte is nonzero.
    m.cpu.mem_write(m.state + 2, b'\x01')
    writes = []
    trace = []
    counter = m.cpu.hook_add(UC_HOOK_CODE, lambda cpu, pc, size, data: trace.append(pc))
    monitor = m.cpu.hook_add(UC_HOOK_MEM_WRITE, lambda *args: writes.append(args[4]),
                             begin=m.state, end=m.state + 3)
    poll(0)
    m.cpu.hook_del(counter)
    m.cpu.hook_del(monitor)
    print(f"Idle output poll: {len([pc for pc in trace if pc != resume])} ARM instructions,"
          f" {len(writes)} shared lock writes")
    assert not writes, "idle polling should leave the shared audio lock free"

    # A refusal is being published on the other CPU when the worker polls.
    # Complete publication at entry to the locked retry path.
    for writer in (0, 1):
        m.reset(wire_bytes=12)
        m.write(FIFO, 0xcafebabc)
        m.submit(0, 4, 8)
        assert m.read(m.state + 8) == 1
        m.write(FIFO, 0)
        m.cpu.mem_write(m.state + writer, b'\x01')
        m.write(m.state + 8, 0)

        def finish_publication(cpu, address, size, unused):
            m.write(m.state + 8, 1)
            cpu.mem_write(m.state + writer, b'\x00')

        retry = m.symbols["gapless_retry"]
        publish = m.cpu.hook_add(UC_HOOK_CODE, finish_publication, begin=retry, end=retry)
        poll(2)
        m.cpu.hook_del(publish)
        assert m.fifo(0) == (BUFFER + 4, 8) and m.read(m.state + 8) == 0
        m.done(*m.fifo(0))

    # A full FIFO must go through the wait and poll again before starting
    # another block. Model the scheduler giving the driver room on yield.
    m.reset(wire_bytes=12)
    m.write(FIFO, 0xcafebabc)
    m.submit(0, 4, 8)
    events = []

    def schedule(cpu, address, size, unused):
        events.append(address)
        if address == 0x13bc00:
            m.write(FIFO, 0)
        cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))

    services = [m.cpu.hook_add(UC_HOOK_CODE, schedule, begin=pc, end=pc)
                for pc in (0x11c518, 0x13bc00)]
    poll(1)
    for hook in services:
        m.cpu.hook_del(hook)
    assert events == [0x11c518, 0x13bc00]
    assert m.fifo(0) == (BUFFER + 4, 8) and m.read(TX + 0x14) == 1
    m.done(*m.fifo(0))
    m.cpu.hook_del(stop)


def verify_reset_race(m, busy):
    # Stop the submitting CPU exactly where the original I2S FIFO starts.
    # The second context, using its own stack, starts a genuine stock rebuild.
    m.reset(wire_bytes=12)
    if busy:
        m.write(FIFO, 0xcafebabc)
    m.ready(0, 4, 8)
    m.prepare(QUEUE, 0, 12)
    m.run(0x1333d4, 0x106818)
    producer = m.cpu.context_save()
    assert m.read(m.state + 28) == 1
    m.prepare(RING, 0, 0, m.capacity, 0, stack=RESET_STACK)
    m.run(0x14dcd4, 0x13bc00)
    control = m.cpu.context_save()
    assert m.read(m.state + 32) == RING and m.statuses()[0] == 9
    assert not m.call("gapless_arm_span", RING, DESCRIPTORS + 16, 0, 4, stack=IRQ_STACK)
    m.cpu.context_restore(producer)
    m.run(0x106818)
    assert not m.read(m.state + 28) and not m.read(m.state + 8)
    m.cpu.context_restore(control)
    if not busy:
        # A successful native call still owns physical PCM. Check for a second
        # yield, deliver the stock completion IRQ, then let reset finish.
        m.run(m.cpu.reg_read(arm.UC_ARM_REG_LR), 0x13bc00)
        control = m.cpu.context_save()
        assert m.fifo(0) == (BUFFER + 4, 8) and m.statuses()[0] == 9
        m.done(*m.fifo(0))
        m.cpu.context_restore(control)
    assert not m.run(m.cpu.reg_read(arm.UC_ARM_REG_LR))
    assert m.statuses() == [3, 3, 3] and not m.read(m.state + 32)
    assert not m.call("gapless_retry", QUEUE)


def verify_cancel(m):
    m.reset(wire_bytes=12)
    m.submit(0, 4, 8)
    # Without native release or a completed flush the bounded control-thread
    # wait returns the original stock "refused" result and keeps ownership.
    # Replace only the OS scheduler's yield, which has no work in this fixture.
    def empty_yield(cpu, address, size, data):
        cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))
    hook = m.cpu.hook_add(UC_HOOK_CODE, empty_yield, begin=0x13bc00, end=0x13bc00)
    assert m.call(0x14dcd4, RING, 0, 0, m.capacity, 0) == 8
    m.cpu.hook_del(hook)
    assert m.statuses() == [9, 3, 3] and m.read(m.state + 32) == 0
    # A detached listener acknowledges native cancellation but never forwards
    # a completion into 0x133ba4. Execute the entire real I2S stop/flush.
    noop_table = LISTENER + 0x100
    m.write(LISTENER, noop_table)
    m.write(noop_table + 12, (0x123ac8 - noop_table) & 0xffffffff)
    m.write(TX + 0x30, LISTENER)
    assert m.call(0x157fac, TX) == 0 and m.statuses() == [3, 3, 3]
    assert m.call(0x14dcd4, RING, 0, 0, m.capacity, 0) == 0
    m.write(LISTENER, 0x25a74)


def verify_flush_during_submit(m):
    # Stop on either side of native FIFO insertion. Exercise the stock
    # completion callback as well as teardown after its listener detached.
    for already_queued, listener_attached in ((False, False), (True, False), (True, True)):
        m.reset(wire_bytes=12)
        m.ready(0, 4, 8)
        m.prepare(QUEUE, 0, 12)
        pause = 0x106868 if already_queued else 0x106818
        m.run(0x1333d4, pause)
        submit = m.cpu.context_save()
        if not listener_attached:
            noop_table = LISTENER + 0x100
            m.write(LISTENER, noop_table)
            m.write(noop_table + 12, (0x123ac8 - noop_table) & 0xffffffff)
        m.write(TX + 0x30, LISTENER)
        assert not m.call(0x157fac, TX, stack=RESET_STACK)
        m.cpu.context_restore(submit)
        m.run(pause)
        m.write(LISTENER, 0x25a74)
        if not already_queued:
            assert m.statuses() == [9, 3, 3]
            assert m.fifo(0) == (BUFFER + 4, 8)
            m.done(*m.fifo(0))
        assert m.statuses() == [3, 3, 3], "a cancelled in-flight submission pinned the ring"


def verify_submit_while_reset_waits(m):
    m.reset(wire_bytes=12)
    m.submit(0, 4, 8)
    m.ready(1, 0, 4)
    m.prepare(RING, 0, 0, m.capacity, 0, stack=RESET_STACK)
    m.run(0x14dcd4, 0x13bc00)
    control = m.cpu.context_save()
    # The worker already owns the second descriptor when reset starts.
    assert not m.call(0x1333d4, QUEUE, 0, 12)
    assert m.fifo(1) == (BUFFER + m.capacity, 4), "reset discarded an acquired block"
    m.cpu.context_restore(control)
    assert m.run(m.cpu.reg_read(arm.UC_ARM_REG_LR)) == 8
    for index in (0, 1):
        m.done(*m.fifo(index))
    assert m.statuses() == [3, 3, 3]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock_osos", type=Path)
    parser.add_argument("playback_elf", type=Path)
    args = parser.parse_args()
    osos = args.stock_osos.read_bytes()
    for frontend in (0x21eb4, 0x21f3c):
        with args.playback_elf.open("rb") as file:
            machine = Machine(osos, file, frontend)
        verify_core_lock(machine)
        verify_dsp(machine)
        verify_order(machine)
        verify_retry(machine)
        verify_worker_poll(machine)
        verify_reset_race(machine, busy=False)
        verify_reset_race(machine, busy=True)
        verify_cancel(machine)
        verify_flush_during_submit(machine)
        verify_submit_while_reset_waits(machine)
        print(f"Verified 4G output table {frontend:#x}, packed MP3 extent, ordered empty blocks,"
              " retry ceiling and reset interleavings")
