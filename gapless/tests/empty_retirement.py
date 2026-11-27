"""Empty trim output must not free PCM still owned by the I2S driver.

Interrupts arrive before/after the submit hook, or after all output work.
Every pending real descriptor must remain owned until its own completion.
"""
import sys
sys.path.insert(0, sys.argv[1])
import repro_aac_edges as fixture
from verify_tx import DESCRIPTORS, IRQ_STACK, LISTENER, QUEUE, REG, RING, TX
from unicorn import arm_const as arm


class Album(fixture.Album):
    interrupt_at = None
    drain_each = False
    empty_submits = 0

    def ownership(self):
        m = self.m
        slots = m.read(RING + 0x1c)
        for base, length in self.pending:
            assert length >= 512 * 4 and length % 512 == 0, 'zero or undersized hardware submission'
            slot = next(DESCRIPTORS + 16 * i for i in range(slots)
                        if m.read(DESCRIPTORS + 16 * i) == base)
            assert m.read(slot + 8) == 9 and m.read(slot + 12) == 1, 'queued PCM freed before its IRQ'

    def retire(self, limit=None):
        count = len(self.pending) if limit is None else min(limit, len(self.pending))
        for _ in range(count):
            m = self.m
            address, length = self.pending.pop(0)
            index = m.read(TX + 0x18)
            assert m.fifo(index) == (address, length)
            self.delivered.extend(m.cpu.mem_read(address, length))
            masks = m.cpu.reg_read(arm.UC_ARM_REG_CPSR) & 0xc0
            m.call(0x132f90, TX, 1, 0, address, stack=IRQ_STACK)
            assert m.cpu.reg_read(arm.UC_ARM_REG_CPSR) & 0xc0 == masks, 'IRQ masks not restored'
            assert m.fifo(index)[0] == 0 and m.read(TX + 0x18) != index
            self.ownership()

    def worker(self):
        m = self.m
        masks = m.cpu.reg_read(arm.UC_ARM_REG_CPSR) & 0xc0
        before = m.read(TX + 0x14)
        m.prepare()
        m.cpu.reg_write(REG[4], QUEUE)
        m.cpu.reg_write(REG[7], 0)
        m.write(QUEUE + 0xe0, 1)
        stop = self.interrupt_at or 0x133548
        m.run(0x15a88c, stop)
        empty = m.cpu.reg_read(REG[7]) == 0
        if empty:
            self.empty_submits += 1
            if self.interrupt_at and self.pending:
                context, finish = m.cpu.context_save(), m.finish
                self.retire(limit=1)
                m.cpu.context_restore(context)
                m.finish = finish
        m.run(stop, 0x15a9a0)
        assert m.cpu.reg_read(arm.UC_ARM_REG_CPSR) & 0xc0 == masks, 'worker IRQ masks not restored'
        m.write(QUEUE + 0xe0, 0)
        if before != m.read(TX + 0x14):
            self.pending.append(m.fifo(before))
        self.ownership()
        if self.drain_each:
            self.retire()


def make_album(frontend):
    with fixture.elf.open('rb') as binary:
        album = Album(fixture.Machine(fixture.stock, binary, frontend), fixture.installed,
                      arena_bytes=fixture.RETAIL_ARENA_BYTES)
    m = album.m
    m.write(TX, 0x24094)
    m.write(TX + 0x30, LISTENER)
    for pc in (0x133548, 0x133554):
        album.note(pc, m.stop_at_boundary)
    return album


for count, delay in ((900, 2112), (1024, 2112), (900, 4096), (900, 16384)):
    a, a_pcm = fixture.numbered(0x100000, count)
    b, b_pcm = fixture.numbered(0x200000, 6000, delay)
    for frontend in (0x21eb4, 0x21f3c):
        for masks, mode in ((0, 'late IRQs'), (0x40, 'early IRQs'), (0x80, 'before skip'), (0xc0, 'after skip')):
            album = make_album(frontend)
            m = album.m
            m.cpu.reg_write(arm.UC_ARM_REG_CPSR, (m.cpu.reg_read(arm.UC_ARM_REG_CPSR) & ~0xc0) | masks)
            album.drain_each = mode == 'early IRQs'
            album.interrupt_at = {'before skip': 0x133548, 'after skip': 0x133554}.get(mode)
            got = album.play_trim(a, b)
            assert album.empty_submits, 'fully trimmed block was filled and sent to hardware'
            assert got[:4096] == bytes(4096), 'cold-start descriptor changed'
            body, want = got[4096:], a_pcm + b_pcm
            assert len(body) > len(a_pcm) + 4096, 'did not reach the next track'
            assert body == want[:len(body)], 'audio discontinuity'
            assert m.read(RING + 4) == m.read(RING + 24), 'completion cursor missed an empty block'
            assert all(m.read(DESCRIPTORS + 16 * i + 8) != 9 for i in range(m.read(RING + 0x1c)))
            print(f'{count} samples, priming {delay}, {frontend:#x}, {mode}: contiguous; descriptors retired in order')

# A run of short tracks must reuse slots across several ring wraps without
# releasing a later real block or accumulating orphaned empty descriptors.
for frontend in (0x21eb4, 0x21f3c):
    album = make_album(frontend)
    tracks = [fixture.numbered(0x100000 + i * 0x10000, 900) for i in range(40)]
    tracks.append(fixture.numbered(0x400000, 6000))
    current = tracks[0][0]
    album.start(current)
    for following, _ in tracks[1:]:
        for _ in range(len(current.blocks) - 1):
            album.worker()
        album.advance(following)
        album.worker()
        album.retire()
        album.song(following.blocks)
        current = following
    for _ in current.blocks:
        album.worker()
    album.retire()
    got = bytes(album.delivered)
    want = b''.join(pcm for _, pcm in tracks)
    assert got[:4096] == bytes(4096)
    assert got[4096:] == want[:len(got) - 4096], 'discontinuity after ring wrap'
    assert len(got) - 4096 > 4 * (40 * 900 + 1024), 'did not reach final track'
    assert album.m.read(RING + 4) == album.m.read(RING + 24), 'ring not fully retired'
    print(f'40 short tracks, {frontend:#x}: contiguous through repeated slot reuse')

# Native stop calls the listener for queued buffers. Empty successors must
# be released too, so native ring reconfiguration does not hang. This
# fixture stubs transport reinitialization, so cold startup is checked by
# cold_start.py using the real worker mailbox in a fresh transport.
for frontend in (0x21eb4, 0x21f3c):
    album = make_album(frontend)
    a, _ = fixture.numbered(0x100000, 900)
    b, _ = fixture.numbered(0x200000, 6000)
    album.start(a)
    album.worker()
    album.advance(b)
    album.worker()
    album.song(b.blocks)
    album.worker()
    m = album.m
    assert album.empty_submits and len(album.pending) == 2
    assert m.call(0x157fac, TX) == 0, 'native stop failed'
    album.pending.clear()
    assert m.read(RING + 4) == m.read(RING + 24), 'stop stranded an empty descriptor'
    assert m.call(0x14dcd4, RING, 0, 0, m.read(RING + 0x40), 0) == 0, 'native ring rebuild blocked'
    assert all(m.read(DESCRIPTORS + 16 * i + 8) == 3 for i in range(m.read(RING + 0x1c)))
    print(f'native stop and ring rebuild, {frontend:#x}: every descriptor available')
