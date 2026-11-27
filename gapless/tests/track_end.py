"""Complete final tracks through the native mailbox, EOF drain and TX IRQ.

The delayed schedule keeps PCM queued until the drain needs an interrupt,
including a full ring. It does not model disk time or physical I2S timing.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from verify_cold_output import ColdMachine, ColdOutput, SAMPLE_CLOCK
from verify_mixed_album import RETAIL_ARENA_BYTES
from verify_transition import CALLBACKS, DECODER, ENGINE
from verify_tx import DESCRIPTORS, IRQ_STACK, QUEUE, REG, RING, TX
from unicorn import arm_const as arm


class DrainMachine(ColdMachine):
    hold_irqs = False
    pass_wait = False
    last_remaining = None
    blocked_polls = 0

    def complete_transfers(self):
        if not self.hold_irqs:
            super().complete_transfers()

    def retire_one(self):
        index = self.read(TX + 0x18)
        if index == self.read(TX + 0x14):
            return
        address, length = self.fifo(index)
        assert address and length >= 512 * 4 and length % 512 == 0
        self.output.append(bytes(self.cpu.mem_read(address, length)))
        context = self.cpu.context_save()
        identity = bytes(self.cpu.mem_read(0x60000000, 1))
        finish = self.finish
        self.cpu.mem_write(0x60000000, b'\x55')
        self.call(0x132f90, TX, 1, 0, address, stack=IRQ_STACK)
        assert self.fifo(index)[0] == 0
        self.cpu.context_restore(context)
        self.cpu.mem_write(0x60000000, identity)
        self.finish = finish

    def wait_poll(self, cpu, pc, size, unused):
        if self.pass_wait:
            self.pass_wait = False
        else:
            cpu.emu_stop()

    def resume(self, start, stop):
        while True:
            pc = super().resume(start, stop)
            if pc != 0x116eb4:
                return pc
            remaining = self.read(self.symbols['finish_blocks'])
            if not remaining or remaining == self.last_remaining:
                self.blocked_polls += int(bool(remaining))
                self.retire_one()
            self.last_remaining = remaining
            self.pass_wait = True
            start = pc


class EndAlbum(ColdOutput):
    bad_block = None

    def draining(self, cpu, address, size, unused):
        pass  # Execute the native drain instead of the handoff fixture's sentinel.

    def start(self, track):
        # File selection and metadata import have separate integration tests.
        # Supply their result, then run cold transport setup and the real pump.
        self.incoming = track
        m = self.m
        info = ENGINE + 0x2e3c
        m.write(info + 4, 44100)
        m.cpu.mem_write(info + 12, bytes((2, 16)))
        m.cpu.mem_write(info + 24, bytes((track.kind,)))
        m.write(ENGINE + 0x2e64, 44100)
        m.cpu.mem_write(QUEUE + 0x94, bytes(2))
        m.write(QUEUE + 0xd0, 0)
        self.registers()
        m.run(0x115ff8, 0x116008)
        timing = m.symbols['gapless_engine_timing']
        m.cpu.mem_write(timing, struct.pack('<4I', track.delay, track.count, 0, track.padding))
        m.write(timing + 32, 7)
        self.open_codec(track)
        self.configure(track)
        self.song(track.blocks)

    def song(self, frames, *, remaining=0):
        m = self.m
        self.frames = list(frames)
        self.decodes = 0
        handle, parser = 0x10037000, 0x10037400
        m.write(DECODER + 0x14, handle)
        m.write(handle + 8, parser)
        m.write(parser + 0x234, len(frames) + 1)
        for offset in (0x2cd4, 0x2cf4, 0x2d10, 0x2d14):
            m.write(DECODER + offset, 0)
        m.write(0x144b22f0, len(frames[0]))
        self.registers()
        m.run(0x116480, 0x1164a4)
        assert not self.frames

    def codec(self, cpu, address, size, unused):
        m = self.m
        if self.incoming.kind == 10 and address == CALLBACKS + 12:
            m.write(ENGINE + 0x90cc, 1023 if self.decodes == self.bad_block else 1024)
            self.decodes += 1
            for offset in (0x2cd4, 0x2d10):
                m.write(DECODER + offset, m.read(DECODER + offset) + 1)
        super().codec(cpu, address, size, unused)
        if self.incoming.kind == 10 and address == CALLBACKS + 16 and cpu.reg_read(REG[0]) == 1:
            m.write(DECODER + 0x2d14, m.read(DECODER + 0x2d14) + 1)


def end_track(stock, installed, elf, track, frontend, *, delayed=False, bad_block=None,
              empty_dsp=False, abandoned=False):
    with elf.open('rb') as binary:
        m = DrainMachine(stock, binary, frontend)
    album = EndAlbum(m, installed, arena_bytes=RETAIL_ARENA_BYTES)
    album.initialize_worker()
    album.bad_block = bad_block
    m.hold_irqs = delayed
    album.note(0x116eb4, m.wait_poll)
    if empty_dsp:
        def empty_drain_output(cpu, pc, size, unused):
            position = m.read(RING + 16)
            if m.read(m.symbols['notes'] + 28 * position + 4) & 32:
                cpu.reg_write(REG[0], 0)
        album.note(m.symbols['gapless_trim_output'], empty_drain_output)
    album.start(track)
    before_clock = m.read(SAMPLE_CLOCK)
    handoff = m.symbols['gapless_handoff_state']
    if abandoned:
        m.write(handoff, 8)
        m.write(handoff + 4, 8)
    album.registers()
    m.write(m.cpu.reg_read(arm.UC_ARM_REG_SP) + 0x68, 0)
    m.run(0x116554, 0x1165c0)
    if abandoned:
        assert m.read(handoff + 32) == 1, 'EOF did not retain the proposed successor'
        album.note(0x115e68, m.stop_at_boundary)
        m.prepare(0)
        m.run(0x115e64, 0x115e68)  # actual failed-file-selection hook abandons the handoff
        assert m.read(handoff + 32) == 0
    assert m.read(SAMPLE_CLOCK) == before_clock, 'drain advanced the source clock'
    assert m.read(TX + 0x18) == m.read(TX + 0x14), 'native drain returned with PCM pending'
    assert m.read(m.symbols['fifo_len']) == 0, 'native drain left audible carry'
    assert not m.read(m.symbols['empty_mask']), 'native drain stranded skipped slots'
    got, want = b''.join(m.output), track.expected
    lead = 0
    while lead < len(got) and got[lead:lead + 4] == bytes(4):
        lead += 4
    assert got[lead:lead + len(want)] == want, f'final PCM differs: {len(got) // 4} output, {len(want) // 4} expected'
    assert not any(got[lead + len(want):]), 'padding leaked after the final sample'
    assert all(m.read(DESCRIPTORS + 16 * i + 8) == 3 for i in range(m.read(RING + 0x1c)))
    return m.blocked_polls


def changed_geometry(stock, installed, elf, track, frontend, change):
    with elf.open('rb') as binary:
        m = DrainMachine(stock, binary, frontend)
    album = EndAlbum(m, installed, arena_bytes=RETAIL_ARENA_BYTES)
    album.initialize_worker()
    album.start(track)
    assert m.call(0x12c970, QUEUE) == 1
    m.call('gapless_trim_finish_begin', ENGINE)
    assert m.read(m.symbols['finish_blocks']) > 0
    position = m.read(RING + 20)
    if change == 'capacity':
        m.write(DESCRIPTORS + 16 * position + 4, 1024)
    elif change == 'reservation':
        m.write(ENGINE + 0x1948, m.read(ENGINE + 0x1948) + 4)
    elif change == 'cursor':
        m.write(RING + 20, m.read(RING + 0x1c))
    elif change == 'stopped':
        m.write(ENGINE + 0x1940, 0)
    elif change == 'worker':
        m.write(QUEUE + 0x18, 0)
    elif change == 'route':
        m.write(QUEUE + 0xd0, 0)
    else:
        raise ValueError(change)
    assert m.call(0x12c970, QUEUE) == 1, 'fixture unexpectedly has hardware owners'
    assert m.call('gapless_trim_finish_poll', QUEUE) == 1, f'{change}: auxiliary drain prevents empty stock queue from finishing'
    assert m.read(m.symbols['finish_blocks']) == 0


if __name__ == '__main__':
    from repro_aac_edges import apple, numbered
    from verify_mixed_album import File
    import tempfile

    stock, installed = Path(sys.argv[2]).read_bytes(), Path(sys.argv[3]).read_bytes()
    elf = Path(sys.argv[4])
    for frontend in (0x21eb4, 0x21f3c):
        for count in (24, 900, 1024, 3001, 25000):
            track, _ = numbered(0x100000, count)
            for delayed in (False, True):
                blocked = end_track(stock, installed, elf, track, frontend, delayed=delayed)
                if delayed and count == 25000:
                    assert blocked, 'full-ring wait was not exercised'
                print(f'AAC {count}, {frontend:#x}, delayed={delayed}: complete final PCM; blocked polls={blocked}')
        # Normal tail trim has removed 504 frames before the last block's
        # size fault. Natural EOF must discard only its remaining padding.
        track, _ = numbered(0x200000, 21000, 2048)
        extra = struct.pack('<1024I', *range(0x300000, 0x300400))
        track = File(10, track.delay, track.count, track.padding + 1024, track.blocks + [extra])
        end_track(stock, installed, elf, track, frontend, bad_block=len(track.blocks) - 1)
        print(f'AAC final-block recovery, {frontend:#x}: complete final PCM without padding')
        track, _ = numbered(0x400000, 3001)
        end_track(stock, installed, elf, track, frontend, empty_dsp=True)
        print(f'empty DSP output during drain, {frontend:#x}: post-DSP carry still delivered')
        for change in ('capacity', 'reservation', 'cursor', 'stopped', 'worker', 'route'):
            changed_geometry(stock, installed, elf, track, frontend, change)
            print(f'changed {change}, {frontend:#x}: auxiliary drain cancelled without blocking stock drain')
        end_track(stock, installed, elf, track, frontend, delayed=True, abandoned=True)
        print(f'failed successor, {frontend:#x}: outgoing tail drained before abandoning the handoff')
        untimed = File(10, 0, 0, 0, track.blocks,
                       reference_pcm=b''.join(track.blocks)[1088 * 4:])
        end_track(stock, installed, elf, untimed, frontend, delayed=True)
        print(f'AAC without database timing, {frontend:#x}: all decoded audio after front trim delivered')
    if '--apple' in sys.argv[5:]:
        with tempfile.TemporaryDirectory() as tmp:
            for count in (24, 900, 1024, 3001, 25000):
                track, _ = apple(Path(tmp), f'final-{count}', count, 733)
                for frontend in (0x21eb4, 0x21f3c):
                    end_track(stock, installed, elf, track, frontend, delayed=True)
                    print(f'Apple AAC {count}, {frontend:#x}: every final decoded sample delivered')
