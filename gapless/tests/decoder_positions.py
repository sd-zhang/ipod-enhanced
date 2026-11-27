"""AAC positions from the native decoder's counters (+0x2cd4 packet index,
+0x2d10 submitted, +0x2d14 done, +0x2cf4 EOF). Track A may 'seek' (skip
packets, the index jumps) and its failed final read may advance the index
before the last block publishes. A -> B must be sample-exact."""
import struct, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from verify_mixed_album import MixedAlbum, File, RETAIL_ARENA_BYTES, CALLBACKS
from verify_tx import Machine
from verify_transition import DECODER, ENGINE
from unicorn import arm_const as arm
stock = Path(sys.argv[2]).read_bytes(); installed = Path(sys.argv[3]).read_bytes(); elf = Path(sys.argv[4])
PKT, EOF, SUB, DONE = DECODER + 0x2cd4, DECODER + 0x2cf4, DECODER + 0x2d10, DECODER + 0x2d14
HANDLE, PARSER = 0x10037000, 0x10037400

def track(start, count, delay=2048, extra=0):
    front = delay - 1024
    blocks = -(-(front + count) // 1024) + extra
    frames = [start + i for i in range(blocks * 1024)]
    data = [struct.pack('<1024I', *frames[b*1024:(b+1)*1024]) for b in range(blocks)]
    return File(10, delay, count, (blocks + 1) * 1024 - delay - count, data), frames, front

class Album(MixedAlbum):
    jumps = {}            # decode number -> extra packets skipped before it
    bias = 0              # constant error in the decoder's index (device-only offsets)
    hide = False          # the index does not show the jumps (only the seek hook fires)
    bad = None            # decode number whose block fails the size check
    start_ms = 0
    eof_early = False     # the failed read lands before the last publish
    def counters(self):
        m = self.m
        self.decodes = 0
        for a in (PKT, EOF, SUB, DONE):
            m.write(a, 0)
    def song(self, frames, *, remaining=0):
        m = self.m
        self.frames = list(frames); self.total = len(self.frames)
        self.counters()
        self.songs = getattr(self, 'songs', 0) + 1
        # The native parser chain: decoder +0x14 -> handle, +8 -> parser, +0x234 = packets.
        m.write(DECODER + 0x14, HANDLE); m.write(HANDLE + 8, PARSER)
        m.write(PARSER + 0x234, self.packets[self.songs - 1])
        m.prepare(ENGINE, DECODER)
        m.run(0x1164a0, 0x1164a4)
        assert not self.frames
    def codec(self, cpu, address, size, unused):
        m = self.m
        if address == CALLBACKS + 12:
            if self.songs == 1 and self.jumps.get(self.decodes, 0) and self.decodes:
                # A seek: the firmware's seek message reaches the patch's
                # seek hook, which sets producer.uncertain (field 8). The
                # harness cannot drive that message, so set the flag here.
                m.write(m.symbols['producer.8'], 1)
            if self.songs == 1 and self.bad is not None:
                m.write(ENGINE + 0x90cc, 1023 if self.decodes == self.bad else 1024)
            m.write(PKT, (m.read(PKT) + 1 + (self.jumps.get(self.decodes, 0) if self.songs == 1 and not self.hide else 0)
                          + (self.bias if self.decodes == 0 else 0)) & 0xffffffff)
            m.write(SUB, m.read(SUB) + 1)
            self.decodes += 1
            if self.eof_early and self.songs == 1 and self.decodes == self.total:
                m.write(PKT, m.read(PKT) + 1); m.write(EOF, 1)
            super().codec(cpu, address, size, unused)
        elif address == CALLBACKS + 16:
            super().codec(cpu, address, size, unused)
            if cpu.reg_read(arm.UC_ARM_REG_R0) == 1:
                m.write(DONE, m.read(DONE) + 1)
        else:
            super().codec(cpu, address, size, unused)
    def run_case(self, a, fed_a, b):
        self.packets = [len(a.blocks) + 1, len(b.blocks) + 1]
        self.m.write(ENGINE + 0x29c0, self.start_ms)
        self.start(File(a.kind, a.delay, a.count, a.padding, fed_a))
        self.m.write(ENGINE + 0x29c0, 0)
        n = 0
        while not self.pending and n < len(fed_a):
            self.worker(); n += 1
        self.advance(b)
        self.worker(); self.retire(limit=1)
        self.song(b.blocks)
        for _ in range(len(fed_a) - n - 1 + len(b.blocks)):
            self.worker()
        self.retire()
        d = self.m.symbols.get('gapless_diag')
        return list(struct.unpack(f'<{len(self.delivered)//4}I', bytes(self.delivered))), \
            [self.m.read(d + 4 * i) if d else 0 for i in range(103)]

cases = (  # name, A frames, B frames, {decode number: packets skipped}, early EOF read[, bias, start ms]
    ('plain', 21000, 3001, {}, False),
    ('plain, padding 900', 20604, 3001, {}, False),
    ('seek, padding 900', 20604, 3001, {6: 5}, False),
    ('plain, padding 1528', 21000, 3001, {}, False, 0, 0, 1),
    ('seek, padding 1528', 21000, 4000, {6: 5}, True, 0, 0, 1),
    ('plain, early EOF read', 21000, 3001, {}, True),
    ('seek mid-track', 21000, 3001, {6: 5}, False),
    ('seek mid-track, early EOF read', 22000, 4000, {6: 5}, True),
    ('seek to 0.4 s from the end', 30000, 5000, {2: 12}, False),
    ('two seeks', 23000, 5000, {3: 4, 8: 2}, False),
    ('start mid-track', 21000, 3001, {0: 4}, False, 0, 100),
    ('seek the counters do not show', 22000, 4000, {6: 5}, False, 0, 0, 0, True),
    ('seek near the end the counters do not show, padding 1528', 30000, 5000, {2: 12}, False, 0, 0, 1, True),
    ('one block fails a check', 21000, 3001, {}, False, 0, 0, 0, False, 9),
    ('final block fails after partial tail trim', 21000, 5000, {}, False, 0, 0, 1, False, 22),
    ('penultimate block fails before tail trim', 21000, 5000, {}, False, 0, 0, 1, False, 21),
    ('final block fails, two full padding blocks', 20480, 5000, {}, False, 0, 0, 2, False, 22),
    ('final block fails, padding 900', 20604, 5000, {}, False, 0, 0, 0, False, 21),
    ('final block fails, no padding', 21504, 5000, {}, False, 0, 0, 0, False, 21),
)
only = [c for c in cases if len(sys.argv) > 5 and c[0] == sys.argv[5]]
for name, a_count, b_count, jumps, early, *extra in (only or cases):
    defaults = [0, 0, 0, False, None]
    bias, start_ms, pad_blocks, hide, bad = list(extra) + defaults[len(extra):]
    a, a_frames, a_front = track(0x100000, a_count, extra=pad_blocks)
    b, b_frames, b_front = track(0x200000, b_count)
    # Blocks actually decoded: decode d reads packet (d + 1 + skipped so far), block = packet - 1.
    blocks, skipped = [], 0
    while True:
        skipped += jumps.get(len(blocks), 0)
        bl = len(blocks) + skipped
        if bl >= len(a.blocks): break
        blocks.append(bl)
    fed = [a.blocks[i] for i in blocks]
    front = 0 if start_ms else a_front
    a_real = [a_frames[i*1024 + j] for i in blocks for j in range(1024)
              if front <= i*1024 + j < a_front + a_count]
    b_real = b_frames[b_front:b_front + b_count]
    for frontend in (0x21eb4, 0x21f3c):
        with elf.open('rb') as binary:
            album = Album(Machine(stock, binary, frontend), installed, arena_bytes=RETAIL_ARENA_BYTES)
        album.jumps, album.eof_early, album.bias, album.start_ms = jumps, early, bias, start_ms
        album.hide, album.bad = hide, bad
        got, d = album.run_case(a, fed, b)
        lead = 0
        while lead < len(got) and got[lead] == 0: lead += 1
        body = got[lead:]; want = a_real + b_real; j = 0
        while j < len(body) and j < len(want) and body[j] == want[j]: j += 1
        b_tail = len(b.blocks) * 1024 - b_front - b_count     # held at the album's end, never flushed here
        ok = j >= len(a_real) + 1024 and len(want) - j <= 1152 + b_tail + 640 and not any(body[j:])
        print(f'{name} ({a_count}->{b_count}) via {frontend:#x}:', 'OK' if ok else 'MISMATCH',
              f'{j} of {len(want)} contiguous (join at {len(a_real)}), tail {d[96]} dropped {d[97]} resync {d[92]} skipped {d[95]} fills {d[35]} reuse {d[41]}')
        if len(sys.argv) > 6 and frontend == 0x21eb4:
            m = album.m; base, n = m.symbols['trace'], m.read(m.symbols['trace_count'])
            for k in range(max(0, n - 160), n):
                e = [m.read(base + 20 * (k % 160) + 4 * w) for w in range(5)]
                if e[1] >= 0x100: print('   trace', hex(e[1]), *(hex(x) for x in e[2:]))
        if not ok:
            print('  why', d[94], 'peaks', d[74:78], 'peak lo', d[82:86], 'peak pos', d[78:82])
            print('  around', [hex(x) for x in body[max(0,j-2):j+3]], [hex(x) for x in want[max(0,j-2):j+3]]); sys.exit(1)
