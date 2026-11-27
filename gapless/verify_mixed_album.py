"""Check ordered stereo PCM across MP3/AAC changes on the installed 4G image."""
import argparse
import hashlib
import math
import subprocess
import tempfile
import wave
import struct
from dataclasses import dataclass, replace
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_HOOK_CODE

from verify_aac import AACDecoder, FILE_INFO
from verify_aac_parity import Retail55AAC
from verify_mp3 import Decoder
from verify_mp3_parity import ReferenceMP3
from verify_pcm_parity import Retail55Pump
from verify_transition import Album, CALLBACKS, DECODER, ENGINE, SOURCE, VTABLE
from verify_tx import DESCRIPTORS, QUEUE, REG, RING, STACK, TX, Machine

TRACK, LIBRARY, MANAGER, PATH = 0x501000, 0x502000, 0x503000, 0x504000
REQUEST, UTF16, TEMP_INFO, PCM = 0x505000, 0x506000, 0x507000, 0x510000
MAX_SLOT = 1152 * 8
RETAIL_ARENA_BYTES = 0x40000
MIN_SLOT_BYTES = 576


@dataclass
class File:
    kind: int
    delay: int
    count: int
    padding: int
    blocks: list[bytes]
    primed: bool = False
    file_info: bytes | None = None
    mp3_header: bytes = b''
    extra_frames: int = 0
    reference_pcm: bytes | None = None

    @property
    def block_size(self):
        return len(self.blocks[0]) // 4

    @property
    def expected(self):
        if self.reference_pcm is not None:
            return self.reference_pcm
        start = self.delay
        if self.kind == 0:
            start += 529
        elif not self.primed:
            start -= 1024
        return b''.join(self.blocks)[4 * start:4 * (start + self.count)]


def marked(kind, start):
    size = 1024 if kind == 10 else 1152
    delay = 1041 if kind == 10 else 12
    count = 1801 if kind == 10 else 1503
    blocks = [struct.pack(f'<{size}I', *range(pos, pos + size)) for pos in (start, start + size)]
    # Numbered AAC blocks represent the old reader's packet-one origin. The
    # real media case below exercises the guarded packet-zero path.
    coded_frames = 2 * size + (1024 if kind == 10 else 0)
    return File(kind, delay, count, coded_frames - delay - count, blocks)


class StockOutputDrain(AssertionError):
    """The decoder entered the firmware's blocking drain at 0x116e94."""


class MixedAlbum(Album):
    def __init__(self, m, installed, *, arena_bytes=8 * MAX_SLOT):
        self.selecting = False
        super().__init__(m, installed)
        self.heap = PCM + arena_bytes
        self.pending = []
        self.delivered = bytearray()
        self.next_codec_number = None
        self.closed = []
        self.mode_events = 0
        self.observed_wire = []
        self.codec_number = None
        m.write(VTABLE + 24, CALLBACKS + 24 - VTABLE)
        self.note(CALLBACKS + 24, self.codec)
        self.note(0x116e94, self.draining)
        self.note(0x115b68, self.worker_size)
        for address in (0xdb5a0, 0x115e64, 0x115f70, 0x115f9c, 0x402fc, 0x15a9a0, 0x116394,
                        0xa7dc8, 0xa7e24):
            m.cpu.hook_add(UC_HOOK_CODE, m.stop_at_boundary, begin=address, end=address)
        m.cpu.mem_map(0x10080000, 0x1000)
        m.cpu.mem_map(0x103c9000, 0x1000)
        m.cpu.mem_map(0x60007000, 0x1000)
        m.cpu.mem_map(0x14467000, 0x1000)
        m.cpu.mem_map(0x1450a000, 0xc000)
        m.cpu.mem_write(0x60000000, b'\x55')
        for address in (0x1045b8, 0x1044cc, 0x9b8e8, 0x11b5e0, 0x11dd5c, 0xecad0,
                        0x3b4c4, 0xfac94, 0xfaca4, 0x3b3d4, 0xa2bf0, 0xfd03c, 0xab790):
            self.note(address, self.file_service)
        m.write(MANAGER, SOURCE)
        m.write(0x103c99ac, LIBRARY)
        m.write(0x103c99b0, MANAGER)
        m.write(PATH, 2)
        m.cpu.mem_write(UTF16, 'track\0'.encode('utf-16-le'))
        for constructor in (0x1d5c8, 0x1d5d4, 0x1d5e0, 0x1d608, 0x1d718, 0x1d740):
            m.call(constructor)
        m.write(ENGINE + 12, 0x1d16d4)
        # Mirror the bounds and descriptor count set by the retail allocator.
        m.write(RING + 0x34, arena_bytes // MIN_SLOT_BYTES)
        m.write(RING + 0x38, PCM)
        m.write(RING + 0x40, MIN_SLOT_BYTES)
        m.write(RING + 0x44, arena_bytes)
        m.write(RING + 0x4c, arena_bytes)

    def note(self, address, handler):
        self.m.cpu.hook_add(UC_HOOK_CODE, handler, begin=address, end=address)

    def draining(self, cpu, address, size, unused):
        raise StockOutputDrain('stock output drain reached, '
                               f'outgoing/incoming registry indices {self.codec_number}/{self.next_codec_number}')

    def empty_queue(self, cpu, address, size, unused):
        if address == 0x14942c and self.m.read(cpu.reg_read(REG[1])) == 0x29860:
            self.mode_events += 1
        self.return_to_caller(int(self.selecting and address == 0x133bf4))

    def file_service(self, cpu, address, size, unused):
        m = self.m
        a, b, c = [cpu.reg_read(reg) for reg in REG[:3]]
        result = 0
        if address in (0x1045b8, 0x1044cc):
            result = self.heap
            self.heap += (a + 7) & ~7
        elif address == 0x9b8e8:
            assert a == 720
            result = self.request
        elif address == 0xecad0:
            m.write(a, UTF16)
            result = a
        elif address == 0x3b4c4:
            # Only the file I/O/probe is synchronous here. The actual list
            # validation, metadata binding and registry lookup run in RetailOS.
            if self.incoming.file_info is not None:
                cpu.mem_write(c, self.incoming.file_info)
            else:
                m.write(c + 4, 44100)
                cpu.mem_write(c + 12, bytes((2, 16)))
                cpu.mem_write(c + 24, bytes((self.incoming.kind,)))
            m.write(c + 0x24, 1)
        self.return_to_caller(result)

    def append_track(self, track, automatic, item=0):
        self.incoming = track
        self.tracks_appended = getattr(self, 'tracks_appended', 0) + 1
        self.request = REQUEST + 0x400 * automatic
        m = self.m
        m.cpu.mem_write(TRACK + 0xec, struct.pack('<4I', track.delay, track.count, 0, track.padding))
        m.prepare()
        m.cpu.reg_write(REG[5], TRACK)
        m.run(0xa7da4, 0xa7dc8)
        m.prepare(LIBRARY, self.request + 20, 0, 0x46494c45)
        m.cpu.reg_write(REG[4], self.request)
        m.cpu.reg_write(REG[9], item)
        m.write(STACK, PATH)
        for offset, value in ((0x26c, 0xa8cf8), (0x2a4, 0xa89ec), (0x2c4, 0xa8004)):
            m.write(STACK + offset, value if automatic else 0)
        m.run(0xa7e20, 0xa7e24)
        timing = struct.unpack('<5I', m.cpu.mem_read(m.read(m.read(SOURCE)) + 64, 20))
        assert timing[:4] == (track.delay, track.count, 0, track.padding)
        assert timing[4] == self.tracks_appended | (0 if automatic else 0x80000000)

    def registers(self):
        m = self.m
        m.prepare()
        for i, value in ((4, ENGINE + 0x2000), (5, ENGINE), (6, ENGINE + 0x9000),
                         (7, DECODER), (8, ENGINE + 0x1000), (9, ENGINE + 0x2e00),
                         (10, 1), (11, 0)):
            m.cpu.reg_write(REG[i], value)

    def select_file(self):
        m = self.m
        self.registers()
        m.write(STACK + 0x78, ENGINE + 0x2e3c)
        m.write(STACK + 0x88, TEMP_INFO)
        self.selecting = True
        assert m.run(0x115e54, 0x115e64) == 1
        self.selecting = False
        index = m.call(0x208a0, ENGINE + 12, self.incoming.kind)
        assert index != 0xffffffff
        return index

    def configure(self, track):
        m = self.m
        self.registers()
        # Stock copies selected file information into the output queue at
        # 0x1162e8, after opening the codec at 0x116200.
        m.call(0x14f5d0, QUEUE, ENGINE + 0x2e3c)
        assert m.cpu.mem_read(QUEUE + 0xa0, 1)[0] == track.kind
        m.write(ENGINE + 0x90cc, track.block_size)
        m.write(STACK + 0x34, track.block_size)
        m.run(0x116394, 0x11640c)
        assert m.read(0x144b22ec) == 4 * track.block_size

    def open_codec(self, track):
        if track.mp3_header:
            # Run the installed MPEG probe with the selected file's real bytes
            # and cookie. The in-memory stream supplies file I/O.
            m = self.m
            compressed, info = 0x600000, 0x601000
            m.cpu.mem_write(compressed, track.mp3_header)
            m.prepare(len(track.mp3_header))
            m.cpu.reg_write(REG[8], compressed)
            m.cpu.reg_write(REG[7], info)
            m.run(0xdb594, 0xdb5a0)
            cookie = m.read(m.symbols['gapless_engine_timing'] + 32)
            assert m.call('gapless_mp3_extra_frames', cookie) == track.extra_frames
        elif track.primed:
            # The PCM was decoded in its own firmware context. Bind the same
            # packet-zero decision to the selected cookie in this transport.
            index = DECODER + 0x2cd4
            self.m.write(index, 0)
            assert self.m.call('gapless_aac_open', ENGINE, ENGINE + 0x2e3c, index) == 1
            assert self.m.read(index) == 0

    def codec(self, cpu, address, size, unused):
        if address == CALLBACKS + 12:
            assert self.frames and not self.ready
            cpu.mem_write(cpu.reg_read(REG[1]), self.frames.pop(0))
            self.ready = True
            self.return_to_caller(1)
        elif address == CALLBACKS + 24:
            self.closed.append(self.m.read(cpu.reg_read(REG[0]) + 8))
            self.return_to_caller(0)
        else:
            super().codec(cpu, address, size, unused)

    def worker_size(self, cpu, address, size, unused):
        self.observed_wire.append(cpu.reg_read(REG[3]))

    def worker(self):
        m = self.m
        before = m.read(TX + 0x14)
        m.prepare()
        m.cpu.reg_write(REG[4], QUEUE)
        m.cpu.reg_write(REG[7], 0)
        # The fixture schedules a worker when a block is ready. Enter at the
        # retail wait-loop poll with the native wake flag already set.
        m.write(QUEUE + 0xe0, 1)
        m.run(0x15a88c, 0x15a9a0)
        m.write(QUEUE + 0xe0, 0)  # normally cleared later at 0x15a9d8
        if before != m.read(TX + 0x14):
            self.pending.append(m.fifo(before))

    def retire(self, limit=None):
        m = self.m
        for _ in range(len(self.pending) if limit is None else min(limit, len(self.pending))):
            address, length = self.pending.pop(0)
            self.delivered.extend(m.cpu.mem_read(address, length))
            m.done(address, length)

    def start(self, a):
        """Open A and publish its decoded blocks. The caller schedules output."""
        m = self.m
        self.append_track(a, automatic=False)
        self.codec_number = self.select_file()
        m.write(DECODER + 8, self.codec_number)
        # Cold playback has no prior format in the output queue. Stock sets
        # the primary route first and copies the new file format after open.
        m.cpu.mem_write(QUEUE + 0x94, bytes(2))
        m.write(QUEUE + 0xd0, 0)
        self.registers()
        m.run(0x115ff8, 0x116008)
        assert m.read(QUEUE + 0xd0) and m.cpu.mem_read(QUEUE + 0x94, 2) == bytes(2)
        self.mode_events = 0
        self.open_codec(a)
        self.configure(a)
        self.song(a.blocks)
        if a.primed:
            assert m.read(DECODER + 0x2cd4) == 0xffffffff

    def advance(self, b, dispatch=None):
        """Process A's natural EOF and open B, retaining the outgoing ring."""
        m = self.m
        same_codec = self.incoming.kind == b.kind
        if dispatch is None:
            self.append_track(b, automatic=True)
        self.registers()
        m.write(STACK + 0x68, 0)
        # The stock worker queries A's decoder vtable +20 for EOF.
        m.run(0x116554, 0x1165c0)
        phase = m.symbols['gapless_handoff_state'] + 32
        assert m.read(phase) == 1
        if dispatch is not None:
            self.append_track(b, automatic=True, item=dispatch())
        index = self.select_file()
        self.next_codec_number = index
        assert (index == self.codec_number) == same_codec
        self.registers()
        m.write(STACK + 0x84, ENGINE + 12)
        if not same_codec:
            m.run(0x115ec4, 0x115f70)
            assert m.read(phase) == 5
            # The switch closes A and returns B's registry ID to the factory.
            # Supply B's separately decoded blocks via the new codec callback.
            m.write(STACK + 0xac, 0x402fc)
            assert m.run(0x115f70, 0x402fc) == index
            assert self.closed == [self.codec_number]
            m.write(DECODER + 8, index)
            assert self.select_file() == index
            self.registers()
            m.write(STACK + 0x84, ENGINE + 12)
        m.run(0x115ec4, 0x115ed8)
        assert m.cpu.reg_read(REG[0]) == index
        # Follow the native BEQ into the matching decoder's reuse path.
        m.run(0x115ed8, 0x115f9c)
        assert m.read(phase) == 2
        if same_codec:
            assert not self.closed
        self.registers()
        m.run(0x115ff8, 0x116008)
        self.open_codec(b)
        self.configure(b)
        m.run(0x116480, 0x116490)
        assert m.read(ENGINE + 0x1940) == 1
        assert self.mode_events == 0, 'unnecessary FIFO reconfiguration at the boundary'

    def play(self, a, b):
        m = self.m
        self.start(a)
        a_processed = 0
        # A valid AAC priming packet can be entirely silent in the output
        # window. Acquire through the first real hardware submission.
        while not self.pending and a_processed < len(a.blocks):
            self.worker()
            a_processed += 1
        assert self.pending and a_processed < len(a.blocks)
        assert m.read(DESCRIPTORS + 16 * a_processed + 8) == 8
        self.advance(b)
        # The first A descriptor is in the hardware FIFO. Retire only that
        # IRQ after another A descriptor has traversed the worker under B's
        # geometry. This also leaves enough free slots for B's last reservation.
        self.worker()
        self.retire(limit=1)
        self.song(b.blocks)
        if b.primed:
            assert m.read(DECODER + 0x2cd4) == 0xffffffff
        for _ in range(len(a.blocks) - a_processed - 1 + len(b.blocks)):
            self.worker()
        self.retire()
        sizes = [4 * a.block_size] * len(a.blocks) + [4 * b.block_size] * len(b.blocks)
        assert self.observed_wire == sizes, (self.observed_wire, sizes)
        expected = a.expected + b.expected
        assert bytes(self.delivered) == expected, (len(self.delivered), len(expected), next(
            (i for i, (x, y) in enumerate(zip(self.delivered, expected)) if x != y), None))
        slots = [DESCRIPTORS + 16 * i for i in range(m.read(RING + 0x1c))]
        assert all(m.read(slot + 8) in (3, 7) for slot in slots)
        reserved = [m.read(slot) for slot in slots if m.read(slot + 8) == 7]
        pointer = m.read(ENGINE + 0x1948)
        assert reserved == ([pointer] if pointer else [])


def encoded_files(stock, installed, elf, directory, reference, frame_counts=(3003, 2851),
                  *, check_reference_pump=True):
    files = {}
    mp3_frames, aac_frames = frame_counts
    for name, kind, frames, hz in (('headerless MP3', 0, mp3_frames, 733), ('Info MP3', 0, mp3_frames, 733),
                                   ('AAC', 10, aac_frames, 1127)):
        wav_path = directory / f'source-{kind}.wav'
        with wave.open(str(wav_path), 'wb') as writer:
            writer.setparams((2, 2, 44100, frames, 'NONE', ''))
            # Distinct non-silent signals in each channel expose swaps,
            # stale PCM and gaps even when they occur exactly at the join.
            signal = b''.join(struct.pack('<hh', int(14000 * math.cos(i * hz * math.tau / 44100)),
                                          int(17000 * math.sin((i + 37) * hz * math.tau / 44100)))
                              for i in range(frames))
            writer.writeframes(signal)
        file_info = None
        header, extra = b'', 0
        if kind == 0:
            out = directory / ('headerless.mp3' if name == 'headerless MP3' else 'track.mp3')
            options = ['-t'] if name == 'headerless MP3' else []
            subprocess.run(['lame', '--silent', *options, '-b', '128', str(wav_path), str(out)], check=True)
            data = out.read_bytes()
            with elf.open('rb') as binary:
                decoder = Decoder(stock, ELFFile(binary))
            blocks = decoder.decode(data)
            five = ReferenceMP3(reference)
            mode, offset = five.file_info(data)
            assert offset == five.first_codec_input(data) == 0 and mode == 2
            ref_blocks = ReferenceMP3(reference).decode(data[offset:])
            header, extra = data[:2048], 1152 if name == 'Info MP3' else 0
            delay, start = 576, 576 + 529 + extra
        else:
            out = directory / 'track.m4a'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(wav_path), '-c:a', 'aac',
                            '-b:a', '192k', str(out)], check=True)
            data = out.read_bytes()
            delay = 1024
            stock_blocks = AACDecoder(stock, data).decode()
            padding = len(stock_blocks) * 1024 - frames
            decoder = AACDecoder(installed, data)
            blocks = decoder.decode(timing=(delay, frames, padding), elf=elf)
            ref_blocks = Retail55AAC(reference, data).decode()
            file_info = bytes(decoder.cpu.mem_read(FILE_INFO, 0x1c))
            mode = 2
            assert len(blocks) == len(stock_blocks) + 1
            start = delay
        assert all(len(block) == (4608 if kind == 0 else 4096) for block in blocks)
        padding = sum(map(len, blocks)) // 4 - start - frames + (529 if kind == 0 else 0)
        assert blocks == ref_blocks
        reference_native = b''.join(ref_blocks)
        source_window = reference_native[4 * start:4 * (start + frames)]
        if check_reference_pump:
            # The reference pump holds the entire track in its 16-slot ring.
            physical = Retail55Pump(reference).track(ref_blocks, codec=kind, delay=delay, count=frames,
                                                      padding=padding, mp3_mode=mode)
            # Retail 5.5G classifies the LAME CBR Info frame as mode 2. Its
            # published audio lags the source by one frame for that file.
            stock_start = start - extra
            assert physical == reference_native[4 * stock_start:4 * (stock_start + frames)]
        track = files[name] = File(kind, delay, frames, padding, blocks, primed=kind == 10,
                                   file_info=file_info, mp3_header=header, extra_frames=extra,
                                   reference_pcm=source_window)
        assert delay + padding + (529 if kind == 0 else 0) < frames
        assert len(track.expected) == 4 * frames
        assert len(set(track.expected[:64])) > 8 and len(set(track.expected[-64:])) > 8
    return files


def profile_playback(album, a, b):
    count = 0
    def count_instruction(cpu, address, size, unused):
        nonlocal count
        count += 1
    hook = album.m.cpu.hook_add(UC_HOOK_CODE, count_instruction)
    try:
        album.play(a, b)
    finally:
        album.m.cpu.hook_del(hook)
    return f", {count} guest instructions, PCM SHA-256 {hashlib.sha256(album.delivered).hexdigest()}"


def verify(stock, installed, elf, reference, profile=False):
    with tempfile.TemporaryDirectory() as tmp:
        tracks = encoded_files(stock, installed, elf, Path(tmp), reference)
        # Preserve the independently decoded reference PCM while omitting the
        # MP3's database timing. This exercises LAME tag recovery at the join.
        tag_only = replace(tracks['Info MP3'], delay=0, count=0, padding=0)
        for source, mp3, aac in (('numbered', marked(0, 10000), marked(10, 20000)),
                                 ('headerless LAME/FFmpeg', tracks['headerless MP3'], tracks['AAC']),
                                 ('LAME Info/FFmpeg', tracks['Info MP3'], tracks['AAC']),
                                 ('LAME tag only/FFmpeg', tag_only, tracks['AAC'])):
            for a, b in ((mp3, aac), (aac, mp3)):
                for frontend in (0x21eb4, 0x21f3c):
                    with elf.open('rb') as binary:
                        album = MixedAlbum(Machine(stock, binary, frontend), installed)
                    detail = ''
                    if profile:
                        detail = profile_playback(album, a, b)
                    else:
                        album.play(a, b)
                    names = {0: 'MP3', 10: 'AAC'}
                    print(f'{source} {names[a.kind]} → {names[b.kind]} through {frontend:#x}, '
                          f'{len(a.expected) // 4} + {len(b.expected) // 4} frames matched byte for byte{detail}')


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('stock_4g', 'installed', 'elf', 'stock_55g'):
        cli.add_argument(name, type=Path)
    cli.add_argument('--profile', action='store_true',
                     help='count guest instructions during the two-track transport fixture')
    args = cli.parse_args()
    verify(args.stock_4g.read_bytes(), args.installed.read_bytes(), args.elf,
           args.stock_55g.read_bytes(), args.profile)
