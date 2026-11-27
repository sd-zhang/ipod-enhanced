"""MP3 -> MP3 automatic advance with real LAME files on the trim build."""
import struct, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from verify_mixed_album import MixedAlbum, File, RETAIL_ARENA_BYTES
from verify_mp3 import Decoder
from elftools.elf.elffile import ELFFile
import math, subprocess, wave

def mp3_track(tmp, name, frames, hz, info):
    wav_path = tmp / f'{name}.wav'
    with wave.open(str(wav_path), 'wb') as w:
        w.setparams((2, 2, 44100, frames, 'NONE', ''))
        w.writeframes(b''.join(struct.pack('<hh', int(14000 * math.cos(i * hz * math.tau / 44100)),
                                           int(17000 * math.sin((i + 37) * hz * math.tau / 44100)))
                               for i in range(frames)))
    out = tmp / f'{name}.mp3'
    subprocess.run(['lame', '--silent', *([] if info else ['-t']), '-b', '128', str(wav_path), str(out)], check=True)
    data = out.read_bytes()
    with elf.open('rb') as binary:
        blocks = Decoder(stock, ELFFile(binary)).decode(data)
    extra = 1152 if info else 0
    start = 576 + 529 + extra
    native = b''.join(blocks)
    padding = len(native) // 4 - start - frames + 529
    return File(0, 576, frames, padding, blocks, mp3_header=data[:2048], extra_frames=extra,
                reference_pcm=native[4 * start:4 * (start + frames)])

from verify_tx import Machine
from track_end import end_track
stock = Path(sys.argv[2]).read_bytes(); installed = Path(sys.argv[3]).read_bytes()
elf = Path(sys.argv[4]); ref = Path(sys.argv[5]).read_bytes()

def continuous(got, real, join):
    lead = 0
    while lead < len(got) and got[lead] == 0:
        lead += 1
    body = got[lead:]; k = 0
    while k < len(body) and k < len(real) and body[k] == real[k]:
        k += 1
    if any(body[k:]):
        return False, f'mismatch at {k} of {len(real)}'
    if k < join + min(1152, len(real) - join):
        return False, f'stopped at {k}, join at {join}: next track missing'
    if len(real) - k > 1152 + 1152 + 512 + 128:
        return False, f'stopped at {k} of {len(real)}: audio missing'
    return True, f'{lead} lead, {k} of {len(real)} real frames, {len(body) - k} trailing silent'

class Album(MixedAlbum):
    def song(self, frames, *, remaining=0):
        m = self.m
        self.frames = list(frames)
        m.prepare(0x144a91fc, 0x10034000)
        m.run(0x1164a0, 0x1164a4)
        assert not self.frames
    def play_trim(self, a, b):
        self.start(a)
        n = 0
        while not self.pending and n < len(a.blocks):
            self.worker(); n += 1
        self.advance(b)
        self.worker(); self.retire(limit=1)
        self.song(b.blocks)
        for _ in range(len(a.blocks) - n - 1 + len(b.blocks)):
            self.worker()
        self.retire()
        return list(struct.unpack(f'<{len(self.delivered)//4}I', bytes(self.delivered)))

# The comparison must see audio on both sides of the join. Empty output,
# silence, and an otherwise correct prefix of A used to count as success.
comparison_pcm = list(range(1, 6001))
for name, got in (('empty', []), ('silence', [0] * 1152),
                  ('before join', comparison_pcm[:2000]),
                  ('at join', comparison_pcm[:3000])):
    ok, why = continuous(got, comparison_pcm, 3000)
    assert not ok, f'comparison accepted {name}: {why}'
assert continuous([0] * 1152 + comparison_pcm[:5000], comparison_pcm, 3000)[0]
assert continuous(comparison_pcm, comparison_pcm, 3000)[0]
assert not continuous(comparison_pcm[:5000], list(range(1, 12001)), 3000)[0], 'unbounded missing tail'
assert not continuous(comparison_pcm[:4000] + [0] + comparison_pcm[4001:], comparison_pcm, 3000)[0], 'internal silence'

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    long = {'Info MP3': mp3_track(tmp, 'la', 24000, 733, True), 'headerless MP3': mp3_track(tmp, 'lb', 24000, 911, False)}
    short = {'Info MP3': mp3_track(tmp, 'sa', 5000, 523, True), 'headerless MP3': mp3_track(tmp, 'sb', 5000, 613, False)}
    tiny = {'Info MP3': mp3_track(tmp, 'ta', 900, 733, True), 'headerless MP3': mp3_track(tmp, 'tb', 900, 911, False)}
# (A LAME-tagged file on both sides runs past the harness's fixed instruction
# budget inside the native pump; the two mixed cases cover each side.)
for source, an, bn in ((long, 'Info MP3', 'headerless MP3'),
                       (long, 'headerless MP3', 'Info MP3'),
                       (tiny, 'Info MP3', 'headerless MP3'),
                       (tiny, 'headerless MP3', 'Info MP3')):
    a, b = source[an], short[bn]
    for frontend in (0x21eb4, 0x21f3c):
        with elf.open('rb') as binary:
            album = Album(Machine(stock, binary, frontend), installed, arena_bytes=RETAIL_ARENA_BYTES)
        album.m.cpu.mem_map(0x14466000, 0x1000)   # MPEG decoder state the LAME-to-LAME path reads
        from unicorn import UC_HOOK_MEM_UNMAPPED
        from unicorn import arm_const as arm
        album.m.cpu.hook_add(UC_HOOK_MEM_UNMAPPED, lambda uc, acc, addr, size, val, data: print('UNMAPPED', hex(addr), 'pc', hex(uc.reg_read(arm.UC_ARM_REG_PC)), 'lr', hex(uc.reg_read(arm.UC_ARM_REG_LR))) or False)
        got = album.play_trim(a, b)
        real = list(struct.unpack(f'<{len(a.expected)//4}I', a.expected)) + \
               list(struct.unpack(f'<{len(b.expected)//4}I', b.expected))
        ok, why = continuous(got, real, len(a.expected) // 4)
        d = album.m.symbols.get('gapless_diag'); rd = lambda i: album.m.read(d + 4 * i) if d else 0
        print(f'{an} ({a.count}) -> {bn} ({b.count}) via {frontend:#x}:', 'OK' if ok else 'MISMATCH', why,
              '| reuse', rd(41), 'drained', rd(42), 'trimmed', rd(2), 'bypass', rd(34))
        if not ok:
            sys.exit(1)
        for name, track in ((an, a), (bn, b)):
            end_track(stock, installed, elf, track, frontend, delayed=True)
            print(f'{name} ({track.count}) end via {frontend:#x}: every final decoded sample delivered')
