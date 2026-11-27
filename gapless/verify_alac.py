"""Decode a generated Apple Lossless track with the stock and installed 4G firmware."""
import argparse
import struct
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from make_test_album import write_wav
from verify_aac import AACDecoder, HANDLE, OUTPUT, STOCK_ENGINE
from verify_cold_output import cold_playback
from verify_track_metadata import TRACK

FRAMES = 4096
BLOCK_BYTES = FRAMES * 4
BLOCKS = 13  # Enough for the playback clock to pass one second with a packet pending.


def first_blocks(image, data):
    machine = AACDecoder(image, data)
    # The native file allocator at 0x1237e4 assigns this singleton parser.
    # The ALAC open method reads its configuration directly at 0x1451e24c.
    machine.write(HANDLE + 8, 0x1451e030)
    machine.cpu.mem_map(0x60003000, 4096)
    for constructor in (0x1d5c8, 0x1d5d4, 0x1d5e0, 0x1d608, 0x1d718, 0x1d740):
        machine.call(constructor)
    registry = STOCK_ENGINE + 12
    machine.write(registry, 0x1d16d4)  # Codec table installed at 0xf9b88.
    info = STOCK_ENGINE + 0x2e3c
    assert machine.call(0x131f60, 77, TRACK, info) == 0
    kind = machine.cpu.mem_read(info + 24, 1)[0]
    assert kind == 11 and machine.read(info + 4) == 44100
    assert machine.cpu.mem_read(info + 12, 2) == b'\x02\x10'
    index = machine.call(0x208a0, registry, kind)
    assert index == 4
    decoder = machine.call(0x20730, registry, index, STOCK_ENGINE)
    vtable = machine.read(decoder)
    open_track = vtable + machine.read(vtable + 4)
    decode_block = vtable + machine.read(vtable + 12)
    machine.parser_pos = 0
    machine.call(open_track, decoder, info, 77, TRACK)
    assert machine.read(decoder + 0x8070) == 0  # Native open error flag.
    assert machine.read(decoder + 0x8058) == FRAMES
    blocks = []
    for _ in range(BLOCKS):
        assert machine.call(decode_block, decoder, OUTPUT, 1000, instruction_limit=25000000) == 1
        blocks.append(bytes(machine.cpu.mem_read(OUTPUT, BLOCK_BYTES)))
    return blocks


def verify(stock, installed, elf):
    source = bytearray()
    for frame in range((BLOCKS + 2) * FRAMES):
        left = (frame * 17) % 19309 - 9654
        right = (frame * 29) % 21817 - 10908
        source.extend(struct.pack('<hh', left, right))
    with TemporaryDirectory() as scratch:
        directory = Path(scratch)
        wav, encoded = directory / 'source.wav', directory / 'track.m4a'
        write_wav(wav, source)
        subprocess.run(['ffmpeg', '-v', 'error', '-i', wav, '-c:a', 'alac', encoded], check=True)
        data = encoded.read_bytes()
    for name, image in (('stock', stock), ('installed', installed)):
        blocks = first_blocks(image, data)
        # The unchanged ALAC reader first opens packet one at normal speed.
        assert b''.join(blocks) == source[BLOCK_BYTES:(BLOCKS + 1) * BLOCK_BYTES], name
        entries, clock, seconds = cold_playback(stock, image, elf, 11, blocks)
        assert (entries, clock, seconds) == (BLOCKS, BLOCKS * FRAMES, 1)
        print(f'{name} ALAC decoded {clock} source frames, completed {entries} FIFO entries '
              f'and reached {seconds}s')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('stock', 'installed', 'elf'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    verify(args.stock.read_bytes(), args.installed.read_bytes(), args.elf)
