"""Drive the native cold PCM producer, output mailbox, worker and I2S FIFO."""
import argparse
import struct
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from elftools.elf.elffile import ELFFile
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn import arm_const as arm

from verify_aac import AACDecoder
from verify_mixed_album import File, MixedAlbum, RETAIL_ARENA_BYTES
from verify_mp3 import Decoder as MP3Decoder, encode as encode_mp3
from verify_transition import DECODER, ENGINE
from verify_tx import (DESCRIPTORS, DRIVER, I2S, IRQ_STACK, LISTENER, QUEUE,
                       REG, RESET_STACK, RETURN, RING, TX, Machine)

MAILBOX = QUEUE + 0x1f7c
MESSAGES, EVENT, EVENT_TABLE, SIGNAL, WAIT = (0x680000 + n * 0x2000 for n in range(5))
SAMPLE_CLOCK = ENGINE + 0x2e08


class ColdMachine(Machine):
    """Schedule the two ARM contexts only when the guest signals the mailbox."""
    worker = None

    def resume(self, start, stop):
        self.finish = stop
        self.cpu.emu_start(start, RETURN, count=100000)
        return self.cpu.reg_read(arm.UC_ARM_REG_PC)

    def run(self, start, finish=RETURN):
        while True:
            pc = self.resume(start, finish)
            if pc == finish:
                return self.cpu.reg_read(REG[0])
            assert pc == SIGNAL and self.worker is not None, (
                f'producer {start:#x} stopped at {pc:#x}, expected {finish:#x}')
            producer = self.cpu.context_save()
            self.cpu.context_restore(self.worker)
            self.cpu.mem_write(0x60000000, b'\x55')
            # The synchronous sender owns removal from the mailbox. Run it
            # after the receiver's acknowledgement, before another poll.
            pc = self.resume(self.cpu.reg_read(arm.UC_ARM_REG_LR), 0x11bf40)
            assert pc == 0x11bf40, f'output acknowledgement stopped at {pc:#x}'
            self.worker = self.cpu.context_save()
            self.cpu.context_restore(producer)
            self.cpu.mem_write(0x60000000, b'\xaa')
            pc = self.resume(self.cpu.reg_read(arm.UC_ARM_REG_LR), 0x1493fc)
            assert pc == 0x1493fc, f'sender cleanup stopped at {pc:#x}'
            producer = self.cpu.context_save()
            self.cpu.context_restore(self.worker)
            self.cpu.mem_write(0x60000000, b'\x55')
            pc = self.resume(0x11bf40, WAIT)
            assert pc == WAIT, f'output worker stopped at {pc:#x}'
            self.worker = self.cpu.context_save()
            self.complete_transfers()
            self.cpu.context_restore(producer)
            self.cpu.mem_write(0x60000000, b'\xaa')
            start = 0x1493fc

    def complete_transfers(self):
        # Model the hardware completion interrupt. The stock TX callback
        # notifies the listener, releases its descriptor and clears its entry.
        while self.read(TX + 0x18) != self.read(TX + 0x14):
            index = self.read(TX + 0x18)
            address, length = self.fifo(index)
            assert address and length
            self.output.append(bytes(self.cpu.mem_read(address, length)))
            table = self.read(TX)
            self.call(table + self.read(table + 16), TX, 1, 0, address, stack=IRQ_STACK)
            assert self.fifo(index)[0] == 0 and self.read(TX + 0x18) != index


class ColdOutput(MixedAlbum):
    def empty_queue(self, cpu, pc, size, unused):
        if pc == 0x133bf4:
            return  # Genuine CPU identity predicate in the mailbox and driver.
        super().empty_queue(cpu, pc, size, unused)

    def initialize_worker(self):
        m = self.m
        for offset, value in ((0x18, 1), (0x1c, I2S), (0x34, 1), (0x38, I2S),
                              (0x60, 1), (0xc0, 1)):
            m.write(DRIVER + offset, value)
        m.write(I2S + 0x10, EVENT + 0x100)
        m.write(TX, 0x24094)
        m.write(TX + 0x30, LISTENER)
        m.output = []
        # RetailOS constructs this ten-entry mailbox in the output object at
        # 0xf9690. The fixture supplies its storage and OS semaphore.
        m.write(EVENT, EVENT_TABLE)
        m.write(EVENT_TABLE, SIGNAL - EVENT_TABLE)
        m.write(EVENT_TABLE + 4, WAIT - EVENT_TABLE)
        for pc in (SIGNAL, WAIT):
            self.note(pc, lambda cpu, *args: cpu.emu_stop())
        for pc in (0x11bf40, 0x1493fc):
            self.note(pc, m.stop_at_boundary)
        for pc in (0xe8de4, 0x11de40):
            self.note(pc, lambda *args: self.return_to_caller(0))
        assert m.call(0xf8e9c, MAILBOX, EVENT, MESSAGES, 10, 536) == MAILBOX

        # The task trampoline obtains its argument from the OS. Driver
        # registration is represented by Machine's preconfigured primary route.
        m.write(QUEUE, 0x293ac)
        m.write(QUEUE + 0x1c, QUEUE)
        self.note(0x96630, lambda *args: self.return_to_caller(QUEUE + 0x1c))
        self.note(0x12a714, lambda *args: self.return_to_caller(4))
        m.prepare(stack=RESET_STACK)
        assert m.read(QUEUE + 0x18) == 0 and m.read(QUEUE + 0xe0) == 0
        assert m.resume(0x15aa24, WAIT) == WAIT
        m.worker = m.cpu.context_save()
        assert m.read(QUEUE + 0x18) == 1 and m.read(QUEUE + 0xe0) == 0
        m.cpu.mem_write(0x60000000, b'\xaa')

    def play_blocks(self, kind, blocks, trimmed):
        m = self.m
        frames = len(blocks[0]) // 4
        assert all(len(block) == frames * 4 for block in blocks)
        info = ENGINE + 0x2e3c
        m.write(info + 4, 44100)
        m.cpu.mem_write(info + 12, bytes((2, 16)))
        m.cpu.mem_write(info + 24, bytes((kind,)))
        m.write(ENGINE + 0x2e64, 44100)  # Format negotiated at 0x1162d4.
        m.cpu.mem_write(QUEUE + 0x94, bytes(2))
        m.write(QUEUE + 0xd0, 0)
        self.registers()
        m.run(0x115ff8, 0x116008)
        assert m.read(QUEUE + 0xd0)
        track = File(kind, 1024, frames * (len(blocks) - 1), 0, blocks, primed=trimmed)
        if trimmed:
            timing = m.symbols['gapless_engine_timing']
            m.cpu.mem_write(timing, struct.pack('<4I', track.delay, track.count, 0, 0))
            m.write(timing + 32, 1)
            self.open_codec(track)
        self.configure(track)
        m.write(0x144b22f0, 4 * frames)  # Decoder method +0x20 at 0x116294.
        self.registers()
        self.frames = list(blocks)
        assert not m.read(SAMPLE_CLOCK) and not m.read(TX + 0x14)
        writes = []
        elapsed = []
        def wake_write(cpu, access, address, size, value, unused):
            writes.append((cpu.reg_read(arm.UC_ARM_REG_PC), value))
        m.cpu.hook_add(UC_HOOK_MEM_WRITE, wake_write, begin=QUEUE + 0xe0, end=QUEUE + 0xe3)
        # The worker samples elapsed seconds before the completion IRQ. At
        # that point the native timer subtracts one pending physical stride.
        self.note(0x1295e8, lambda cpu, *args: elapsed.append(cpu.reg_read(REG[0])))
        m.run(0x116480, 0x1164a4)
        assert writes == [(0x11d688, 1), (0x15a9d8, 0)] * len(blocks), writes
        assert not self.frames and m.read(ENGINE + 0x1940) == 1
        assert self.observed_wire == [frames * 4] * len(blocks)
        assert m.read(SAMPLE_CLOCK) == frames * len(blocks), m.read(SAMPLE_CLOCK)
        expected = blocks[1:] if trimmed else blocks
        assert m.output == expected
        assert m.read(TX + 0x14) == len(expected) == m.read(TX + 0x18)
        for i in range(m.read(RING + 0x1c)):
            slot = DESCRIPTORS + 16 * i
            status = m.read(slot + 8)
            assert status == 3 or (status == 7 and m.read(slot) == m.read(ENGINE + 0x1948))
        pending_frames = m.read(RING + 0x2c) // 4
        expected_seconds = [max(0, n * frames - pending_frames) // 44100
                            for n in range(1, len(blocks) + 1)]
        assert elapsed == expected_seconds, (elapsed, expected_seconds)
        return len(expected), m.read(SAMPLE_CLOCK), elapsed[-1]


def cold_playback(stock, image, elf, kind, blocks, *, trimmed=False):
    with elf.open('rb') as binary:
        m = ColdMachine(stock, binary, 0x21f3c)
    album = ColdOutput(m, image, arena_bytes=RETAIL_ARENA_BYTES)
    album.initialize_worker()
    return album.play_blocks(kind, blocks, trimmed)


def verify(stock, installed, elf):
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / 'first.m4a'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'sine=frequency=733:sample_rate=44100:duration=1.2',
                        '-af', 'atrim=end_sample=49152', '-ac', '2', '-c:a', 'aac',
                        '-b:a', '192k', str(path)], check=True)
        data = path.read_bytes()
        mp3 = encode_mp3(Path(tmp), 'lame', 44100, ('-t', '-b', '128'), 52920, 32000)
    # The MP3 synthesis overlay is shared by both images. Pass its packed
    # stereo output through each image's cold worker and full-sized slots.
    with elf.open('rb') as binary:
        mp3_blocks = MP3Decoder(stock, ELFFile(binary)).decode(mp3)
    assert any(b''.join(mp3_blocks))
    reference = AACDecoder(stock, data).decode()
    assert len(reference) == 48 and any(reference[0]) and any(reference[-1])
    for image_name, image in (('stock', stock), ('candidate', installed)):
        entries, clock, seconds = cold_playback(stock, image, elf, 0, mp3_blocks)
        assert entries == len(mp3_blocks) and seconds == 1
        print(f'{image_name} MP3: {entries} FIFO completions, {clock} sample frames, elapsed {seconds}s')
        blocks = reference if image_name == 'stock' else AACDecoder(image, data).decode()
        assert blocks == reference
        entries, clock, seconds = cold_playback(stock, image, elf, 10, blocks)
        assert seconds == 1
        print(f'{image_name} AAC: {len(blocks)} native wakes, {entries} FIFO completions, '
              f'{clock} sample frames, elapsed {seconds}s')
    # A packet-zero AAC origin completely removes the first descriptor.
    decoder = AACDecoder(installed, data)
    blocks = decoder.decode(timing=(1024, 48 * 1024, 0), elf=elf)
    assert decoder.first_index == 0xffffffff and len(blocks) == 49
    entries, clock, seconds = cold_playback(stock, installed, elf, 10, blocks, trimmed=True)
    assert entries == 48 and seconds == 1
    print(f'candidate AAC with priming: {len(blocks)} native wakes, {entries} FIFO completions, '
          f'{clock} sample frames, elapsed {seconds}s')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('stock_osos', 'patched_osos', 'elf'):
        parser.add_argument(argument, type=Path)
    args = parser.parse_args()
    verify(args.stock_osos.read_bytes(), args.patched_osos.read_bytes(), args.elf)
