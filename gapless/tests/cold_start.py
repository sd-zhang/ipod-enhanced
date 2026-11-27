"""Cold-start playback through the native pump and worker on the trim build."""
import struct, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import verify_cold_output as vco
from verify_cold_output import ColdMachine, ColdOutput, ENGINE, QUEUE, TX, SAMPLE_CLOCK
from verify_mixed_album import File, RETAIL_ARENA_BYTES
from unicorn import arm_const as arm

stock = Path(sys.argv[2]).read_bytes(); image = Path(sys.argv[3]).read_bytes(); elf = Path(sys.argv[4])

class Trimmed(ColdOutput):
    finishing = False

    def draining(self, cpu, address, size, unused):
        if not self.finishing:
            super().draining(cpu, address, size, unused)

    def play(self, kind, blocks, timing, *, finish=False):
        m = self.m
        frames = len(blocks[0]) // 4
        info = ENGINE + 0x2e3c
        m.write(info + 4, 44100)
        m.cpu.mem_write(info + 12, bytes((2, 16)))
        m.cpu.mem_write(info + 24, bytes((kind,)))
        m.write(ENGINE + 0x2e64, 44100)
        m.cpu.mem_write(QUEUE + 0x94, bytes(2))
        m.write(QUEUE + 0xd0, 0)
        self.registers()
        m.run(0x115ff8, 0x116008)
        track = File(kind, 1024, frames * (len(blocks) - 1), 0, blocks)
        sel = m.symbols['gapless_engine_timing']
        if timing:
            delay, count = timing
            m.cpu.mem_write(sel, struct.pack('<4I', delay, count, 0, 0))
            m.write(sel + 32, 7)
        self.configure(track)
        m.write(0x144b22f0, 4 * frames)
        self.registers()
        self.frames = list(blocks)
        m.run(0x116480, 0x1164a4)
        m.complete_transfers()
        if finish:
            self.finishing = True
            clock = m.read(SAMPLE_CLOCK)
            self.registers()
            m.write(m.cpu.reg_read(arm.UC_ARM_REG_SP) + 0x68, 0)
            m.run(0x116554, 0x1165c0)
            m.complete_transfers()
            assert m.read(SAMPLE_CLOCK) == clock, 'end drain counted synthetic input as music'
        base = m.symbols.get('gapless_diag')
        diag = [m.read(base + 4 * i) if base else 0 for i in range(103)]
        return b''.join(m.output), diag

def run(kind, blocks, timing, *, finish=False):
    with elf.open('rb') as binary:
        m = ColdMachine(stock, binary, 0x21f3c)
    album = Trimmed(m, image, arena_bytes=RETAIL_ARENA_BYTES)
    album.initialize_worker()
    return album.play(kind, blocks, timing, finish=finish)

def frames_of(data):
    return list(struct.unpack(f'<{len(data)//4}I', data))


def continuous(got, real, lead=1024):
    """lead zeros, then a prefix of real, then only zeros (end-of-play fill)."""
    if got[:lead] != [0] * lead:
        return False, 'no cold-start lead'
    body = got[lead:]
    k = 0
    while k < len(body) and k < len(real) and body[k] == real[k]:
        k += 1
    if any(body[k:]):
        return False, f'mismatch at {k}: got {body[k:k+3]} want {real[k:k+3]}'
    return True, f'{k} of {len(real)} real frames, {len(body) - k} trailing silent'

N = 1024
nblocks = 12
stream = [0x10000 + i for i in range(N * nblocks)]       # numbered nonzero frames
blocks = [struct.pack(f'<{N}I', *stream[b*N:(b+1)*N]) for b in range(nblocks)]
S = 7000
# A reserve-first startup can make short-track joins look correct while
# swallowing this entire standalone track. Exercise the cold worker too:
# its three AAC packets contain 1088 priming + 1024 audible + 960 padding.
out, _ = run(10, blocks[:3], (2112, 1024))
short_got = frames_of(out)
lead = 0
while lead < len(short_got) and short_got[lead] == 0:
    lead += 1
assert short_got[lead:lead + 1024] == stream[1088:2112], 'standalone AAC audio missing or discontinuous'
assert not any(short_got[lead + 1024:]), 'standalone AAC padding leaked'
print('standalone 1024-sample AAC: all audible samples delivered')

for label, kind, timing, expect in (
    ('AAC with database timing', 10, (2112, S), [0]*1024 + stream[1088:1088+S] + [0]*256),
    ('AAC without timing (Apple 2112 front)', 10, (0, 0), [0]*1024 + stream[1088:N*nblocks-256]),
    ('MP3 untouched', 0, (2112, S), stream),
):
    out, d = run(kind, blocks, timing)
    got = frames_of(out)
    ok = got == expect if kind != 10 else continuous(got, expect[1024:])[0]
    print(f'{label}: {"OK" if ok else "MISMATCH"} out={len(got)} expected={len(expect)}',
          'slots', d[23], 'A', [d[0], d[32], d[33], d[1], d[4]], 'B', [d[2], d[5], d[35], d[34], d[37]])
    if not ok:
        for i, (g, e) in enumerate(zip(got, expect)):
            if g != e:
                print('  first diff at', i, hex(g), hex(e)); break
        sys.exit(1)

# Run the actual EOF decision and stock drain after the final decoder block.
# A correct prefix is insufficient here: the last audible sample must arrive.
for count in (900, 24, 500, 1024, 1536, 2048, 3001, 7000, 22000):
    front = 2112 - 1024
    packets = (front + count + 1023) // 1024
    pcm = [0x20000 + i for i in range(packets * 1024)]
    encoded = [struct.pack('<1024I', *pcm[i:i + 1024]) for i in range(0, len(pcm), 1024)]
    out, _ = run(10, encoded, (2112, count), finish=True)
    got = frames_of(out)
    lead = 0
    while lead < len(got) and got[lead] == 0:
        lead += 1
    assert got[lead:lead + count] == pcm[front:front + count], f'AAC {count}: final audible samples missing'
    assert not any(got[lead + count:]), f'AAC {count}: padding leaked at album end'
    print(f'AAC {count}: every audible sample delivered through natural EOF')
