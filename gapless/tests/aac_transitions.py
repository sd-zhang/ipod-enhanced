"""AAC -> AAC automatic advance on the trim build through the native pump,
EOF handoff, retained ring and output worker. Checks every delivered frame."""
import struct, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from verify_mixed_album import MixedAlbum, File, RETAIL_ARENA_BYTES
from verify_tx import Machine, DESCRIPTORS, RING

stock = Path(sys.argv[2]).read_bytes(); installed = Path(sys.argv[3]).read_bytes(); elf = Path(sys.argv[4])

def track(start, count, delay=2112):
    front = delay - 1024                       # 4G reader starts at packet one
    blocks = -(-(front + count) // 1024)
    frames = [start + i for i in range(blocks * 1024)]
    data = [struct.pack('<1024I', *frames[b*1024:(b+1)*1024]) for b in range(blocks)]
    coded = (blocks + 1) * 1024
    return File(10, delay, count, coded - delay - count, data), frames[front:front + count]

class Album(MixedAlbum):
    def play_trim(self, a, b):
        # (The fixture cannot model a nearly drained queue at B's match: it
        # counts A's unprocessed published blocks as queued, and processing
        # them first leaves B's blocks behind the worker.)
        m = self.m
        self.start(a)
        self.a_processed = 0
        while not self.pending and self.a_processed < len(a.blocks):
            self.worker(); self.a_processed += 1
        self.advance(b)
        self.worker(); self.retire(limit=1)
        self.song(b.blocks, remaining=self.m.read(self.m.symbols['gapless_engine_timing'] + 20))
        for _ in range(max(0, len(a.blocks) - self.a_processed - 1) + len(b.blocks)):
            self.worker()
        self.retire()
        d = self.m.symbols.get('gapless_diag')
        return list(struct.unpack(f'<{len(self.delivered)//4}I', bytes(self.delivered))), \
            ([self.m.read(d + 4 * i) for i in range(103)] if d else [0] * 93)


HELD_AT_END = 1152 + 2048 + 640   # trim.c HOLD + MAX_TAIL + MIN_OUT + ALIGN: never flushed here


def continuous(got, real, lead=1024, join=None):
    """lead zeros, then real in order, then only zeros; everything except what
    the FIFO can still hold at the end of the album must have arrived."""
    if got[:lead] != [0] * lead:
        return False, 'no cold-start lead'
    body = got[lead:]
    k = 0
    while k < len(body) and k < len(real) and body[k] == real[k]:
        k += 1
    if any(body[k:]):
        return False, f'mismatch at {k}: got {body[k:k+3]} want {real[k:k+3]}'
    if len(real) - k > HELD_AT_END:
        return False, f'stopped at {k} of {len(real)} (join at {join}): audio missing'
    return True, f'{k} of {len(real)} real frames, {len(body) - k} trailing silent'

# (Longer A tracks reach the decoder's time-slice wait at 0x114208, which
# spins on the emulator's frozen microsecond timer.)
for counts in ((19000, 6000), (14000, 900), (20000, 2048)):
    a, a_real = track(0x100000, counts[0])
    b, b_real = track(0x200000, counts[1])
    for frontend in (0x21eb4, 0x21f3c):
        with elf.open('rb') as binary:
            album = Album(Machine(stock, binary, frontend), installed, arena_bytes=RETAIL_ARENA_BYTES)
        from unicorn import UC_HOOK_MEM_UNMAPPED
        from unicorn import arm_const as arm
        def bad(uc, access, addr, size, value, data):
            regs = [uc.reg_read(getattr(arm, f'UC_ARM_REG_R{i}')) for i in range(13)]
            print('UNMAPPED', hex(addr), 'pc', hex(uc.reg_read(arm.UC_ARM_REG_PC)), 'lr', hex(uc.reg_read(arm.UC_ARM_REG_LR)), [hex(r) for r in regs])
            return False
        album.m.cpu.hook_add(UC_HOOK_MEM_UNMAPPED, bad)
        got, d = album.play_trim(a, b)
        ok, why = continuous(got, a_real + b_real, join=len(a_real))
        held = why
        print(f'AAC {counts[0]} -> AAC {counts[1]} via {frontend:#x}:', 'OK' if ok else 'MISMATCH',
              f'delivered {len(got)} ({held})',
              'slots', d[23], 'A', [d[0], d[32], d[33], d[1], d[4]], 'B', [d[2], d[5], d[35], d[34], d[37]],
              'C', [d[26], d[40], d[41], d[42], d[43]])
        if not ok:
            sys.exit(1)
