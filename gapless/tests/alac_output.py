"""Exercise ALAC short blocks through native publish, DSP, I2S and EOF drain.

Only compressed decoding is supplied here. The fixture enters the real ALAC
success tail at 0x4f540 with its native stack frame, so the installed count hook,
zero fill, pump, trim notes, worker, retained handoff and final drain all execute.
The separate verify_alac_boundaries.py test supplies the real compressed decoder.
Run with .venv/bin/python outside the macOS sandbox (Unicorn uses JIT).
"""
import argparse
import json
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "gapless"))
sys.path.insert(0, str(REPO / "menu_scroll"))
from unicorn import arm_const as arm
from verify_cold_output import ColdOutput, SAMPLE_CLOCK
from verify_mixed_album import File, RETAIL_ARENA_BYTES, marked
from verify_resources import archive, payload
from verify_transition import CALLBACKS, DECODER, ENGINE, VTABLE
from verify_tx import DESCRIPTORS, QUEUE, REG, RING, STACK, TX
# The legacy script takes its module path from argv[1] at import time.
_argv = sys.argv
try:
    sys.argv = [__file__, str(REPO / "gapless")]
    from track_end import DrainMachine
finally:
    sys.argv = _argv

ALAC_TABLE, NATIVE_SUCCESS = 0x2419c, 0x4f540
FRAMES, BYTES = 4096, 4096 * 4


class OutputMachine(DrainMachine):
    retention_limit = None

    def complete_transfers(self):
        if self.hold_irqs and self.retention_limit is not None:
            # Keep several physical descriptors live while allowing a long
            # source to wrap its eight-slot ring rather than blocking forever.
            while ((self.read(TX + 0x14) - self.read(TX + 0x18)) % self.read(TX + 12)
                   >= self.retention_limit):
                self.retire_one()
        else:
            super().complete_transfers()


def image(path):
    return payload(archive(path)["Firmware-4.3.1.1"], 0x4200)


def numbered(count, start=0x100001):
    """Nonzero stereo words make any lost, repeated or inserted frame visible."""
    pcm = struct.pack(f"<{count}I", *range(start, start + count))
    blocks = [pcm[offset:offset + BYTES].ljust(BYTES, b"\xa5")
              for offset in range(0, len(pcm), BYTES)]
    return File(11, 0, count, 0, blocks, reference_pcm=pcm)


class ALACOutput(ColdOutput):
    def __init__(self, machine, installed, **kwargs):
        super().__init__(machine, installed, **kwargs)
        # Keep the native type identity used by the production guards. Supply
        # the same codec methods as MixedAlbum, replacing compressed work only.
        for method in (8, 12, 16, 20, 24):
            machine.write(ALAC_TABLE + method, (CALLBACKS + method - ALAC_TABLE) & 0xffffffff)
        self.actual_counts = []
        self.success_counts = []
        self.decoded_buffers = []
        self.note(NATIVE_SUCCESS, self.observe_success)

    def empty_queue(self, cpu, address, size, unused):
        if self.selecting and address == 0x133bf4:
            self.return_to_caller(1)
            return
        super().empty_queue(cpu, address, size, unused)

    def append_track(self, *args, **kwargs):
        identity = bytes(self.m.cpu.mem_read(0x60000000, 1))
        self.m.cpu.mem_write(0x60000000, b"\x55")
        try:
            return super().append_track(*args, **kwargs)
        finally:
            self.m.cpu.mem_write(0x60000000, identity)

    def select_file(self):
        identity = bytes(self.m.cpu.mem_read(0x60000000, 1))
        self.m.cpu.mem_write(0x60000000, b"\x55")
        try:
            return super().select_file()
        finally:
            self.m.cpu.mem_write(0x60000000, identity)

    def draining(self, cpu, address, size, unused):
        pass  # Execute the installed/native EOF drain, including its mailbox.

    def configure(self, track):
        self.m.write(ENGINE + 0x2e64, 44100)
        super().configure(track)

    def advance(self, track):
        super().advance(track)
        self.pump_prepared = True

    def open_codec(self, track):
        m = self.m
        if track.kind != 11:
            m.write(DECODER, VTABLE)
            return super().open_codec(track)
        m.write(DECODER, ALAC_TABLE)
        m.write(DECODER + 4, ENGINE)
        for offset, value in ((0x8050, 0), (0x8054, 0), (0x8058, FRAMES),
                              (0x805c, BYTES), (0x8060, 44100), (0x8068, 2),
                              (0x8070, 0), (0x8084, 0), (0x8088, 0)):
            m.write(DECODER + offset, value)
        m.write(ENGINE + 0x29b0, 1000)
        m.write(ENGINE + 0x29c0, 0)
        m.write(ENGINE + 0x29c4, 0)

    def observe_success(self, cpu, address, size, unused):
        self.success_counts.append(self.m.read(cpu.reg_read(arm.UC_ARM_REG_SP) + 8))

    def song(self, frames, *, remaining=0):
        self.frames = list(frames)
        if self.incoming.kind == 11:
            count = self.incoming.count
            self.actual_counts = [min(FRAMES, count - n * FRAMES) for n in range(len(frames))]
            assert all(0 < n <= FRAMES for n in self.actual_counts)
        self.m.write(0x144b22f0, len(frames[0]))
        if getattr(self, "pump_prepared", False):
            # MixedAlbum.advance already ran 116480..116490, including the
            # retained reservation. Enter its next pump without reserving twice.
            self.pump_prepared = False
            self.m.prepare(ENGINE, DECODER)
            self.m.run(0x1164a0, 0x1164a4)
        else:
            self.registers()
            self.m.run(0x116480, 0x1164a4)
        assert not self.frames and not self.actual_counts

    def codec(self, cpu, address, size, unused):
        m = self.m
        if self.incoming.kind != 11:
            return super().codec(cpu, address, size, unused)
        if address == CALLBACKS + 12:
            assert self.frames and not self.ready
            output = cpu.reg_read(REG[1])
            self.decoded_buffers.append(output)
            cpu.mem_write(output, self.frames.pop(0))
            actual = self.actual_counts.pop(0)
            self.ready = True
            # Reconstruct dc040/4f2d4's public decoder state and the frame
            # immediately after the ALAC core returned success. Native 4f538
            # adds 52 and pops r4-r11/pc, restoring the original caller exactly.
            frame = cpu.reg_read(arm.UC_ARM_REG_SP) - 88
            saved = [cpu.reg_read(REG[n]) for n in range(4, 12)]
            saved.append(cpu.reg_read(arm.UC_ARM_REG_LR))
            cpu.mem_write(frame, bytes(88))
            cpu.mem_write(frame + 52, struct.pack("<9I", *saved))
            m.write(frame + 8, actual)
            m.write(frame + 44, output)
            m.write(DECODER + 0x8050, (m.read(DECODER + 0x8050) + 1) & 0xffffffff)
            cpu.reg_write(arm.UC_ARM_REG_SP, frame)
            cpu.reg_write(REG[4], DECODER + 0x8000)
            cpu.reg_write(REG[5], DECODER + 0x1c)
            cpu.reg_write(arm.UC_ARM_REG_PC, NATIVE_SUCCESS)
            return
        super().codec(cpu, address, size, unused)
        if address == CALLBACKS + 16 and cpu.reg_read(REG[0]) == 1:
            m.write(DECODER + 0x8088, m.read(DECODER + 0x8088) + 1)


def assert_pcm(got, expected, label, leading_frames=0):
    # A cold startup may need one leading silence descriptor, and the last
    # descriptor may have hardware alignment padding after the final sample.
    # Every source sample between those two points must be contiguous/exact.
    lead = leading_frames * 4
    assert not any(got[:lead]), (label, "cold startup descriptor contains source audio")
    body = got[lead:lead + len(expected)]
    mismatch = next((n // 4 for n, (a, b) in enumerate(zip(body, expected)) if a != b), None)
    assert body == expected, (label, "PCM differs", "first differing frame", mismatch,
                              "output frames", len(got) // 4, "source frames", len(expected) // 4)
    assert not any(got[lead + len(expected):]), (label, "nonzero data after the source")
    assert len(got) - lead - len(expected) <= 511 * 4, (label, "excess padding after the final sample")


def run_case(stock, installed, elf, tracks, frontend, delayed, *, retention_limit=None,
             lose_count_at=None, count_fault="missing"):
    with elf.open("rb") as binary:
        m = OutputMachine(stock, binary, frontend)
    album = ALACOutput(m, installed, arena_bytes=RETAIL_ARENA_BYTES)
    album.initialize_worker()
    # A real gapless handoff requires some of A's PCM to remain queued while
    # B opens. Immediate completion of every A descriptor would correctly
    # select the stock drain path instead of testing the retained boundary.
    m.hold_irqs = delayed or len(tracks) > 1
    m.retention_limit = retention_limit
    album.note(0x116eb4, m.wait_poll)
    faults = []
    if lose_count_at is not None:
        def lose_one_count(cpu, address, size, unused):
            if len(album.success_counts) == lose_count_at + 1 and not faults:
                position = m.read(RING + 20)
                # A missing native count must not discard already-held audio.
                # This is a deliberate producer-note fault, not an alternate
                # implementation of the count capture or publication logic.
                note = m.symbols["notes"] + 28 * position
                if count_fault == "missing":
                    m.write(note, 0)
                elif count_fault == "zero":
                    m.write(note + 12, 0)
                elif count_fault == "oversized":
                    m.write(note + 12, FRAMES + 1)
                else:
                    raise ValueError(count_fault)
                faults.append(position)
        album.note(m.symbols["gapless_trim_publish"], lose_one_count)
    album.start(tracks[0])
    for track in tracks[1:]:
        album.advance(track)
        if not delayed:
            m.hold_irqs = False
            m.complete_transfers()
        album.song(track.blocks)
    before_clock = m.read(SAMPLE_CLOCK)
    album.registers()
    m.write(STACK + 0x68, 0)
    m.run(0x116554, 0x1165c0)
    label = f"{[t.count for t in tracks]} frontend={frontend:#x} delayed={delayed}"
    assert m.read(SAMPLE_CLOCK) == before_clock, (label, "drain advanced the source clock")
    assert m.read(TX + 0x18) == m.read(TX + 0x14), (label, "drain returned with PCM pending")
    assert not m.read(m.symbols["fifo_len"]), (label, "drain left audible carry")
    assert not m.read(m.symbols["empty_mask"]), (label, "drain stranded skipped slots")
    # The supplied ALAC source can start with genuine zero samples. Check the
    # known startup descriptor length instead of stripping source silence.
    leading = FRAMES if tracks[0].kind == 11 and tracks[0].count < 512 else 0
    assert_pcm(b"".join(m.output), b"".join(t.expected for t in tracks), label, leading)
    assert album.success_counts == [min(FRAMES, t.count - n * FRAMES)
                                    for t in tracks if t.kind == 11 for n in range(len(t.blocks))]
    for n in range(m.read(RING + 0x1c)):
        slot = DESCRIPTORS + 16 * n
        status = m.read(slot + 8)
        assert status == 3 or (status == 7 and m.read(slot) == m.read(ENGINE + 0x1948))
    assert m.read(DECODER + 0x8058) == FRAMES and m.read(DECODER + 0x805c) == BYTES
    if retention_limit is not None:
        assert len(album.decoded_buffers) > 2 * m.read(RING + 0x1c)
        assert len(set(album.decoded_buffers)) < len(album.decoded_buffers), "physical ring did not wrap"
    if lose_count_at is not None:
        assert len(faults) == 1
    return m.blocked_polls


def mixed_fallback(stock, installed, elf, outgoing, incoming, frontend):
    """Both physical stride changes reject reuse and must finish the old PCM."""
    with elf.open("rb") as binary:
        m = OutputMachine(stock, binary, frontend)
    album = ALACOutput(m, installed, arena_bytes=RETAIL_ARENA_BYTES)
    album.initialize_worker()
    m.hold_irqs = True
    album.note(0x116eb4, m.wait_poll)
    album.start(outgoing)
    album.append_track(incoming, automatic=True)
    album.registers()
    m.write(STACK + 0x68, 0)
    m.run(0x116554, 0x1165c0)
    phase = m.symbols["gapless_handoff_state"] + 32
    assert m.read(phase) == 1
    index = album.select_file()
    album.next_codec_number = index
    album.registers()
    m.write(STACK + 0x84, ENGINE + 12)
    m.run(0x115ec4, 0x115f70)
    if incoming.kind == 11:
        # The incoming ALAC decoder is rejected before replacing the codec.
        assert m.read(phase) == 0
    else:
        # ALAC -> AAC/MP3 initially permits a new decoder, then rejects the
        # smaller physical slot geometry. Follow both checks in native code.
        assert m.read(phase) == 5
        m.write(STACK + 0xac, 0x402fc)
        assert m.run(0x115f70, 0x402fc) == index
        assert album.closed == [album.codec_number]
        m.write(DECODER + 8, index)
        assert album.select_file() == index
        album.registers()
        m.write(STACK + 0x84, ENGINE + 12)
        m.run(0x115ec4, 0x115ed8)
        m.run(0x115ed8, 0x115f9c)
        assert m.read(phase) == 2
        album.registers()
        m.run(0x115ff8, 0x116008)
        album.open_codec(incoming)
        album.configure(incoming)
        m.run(0x116480, 0x116490)
        assert m.read(phase) == 0 and not m.read(ENGINE + 0x1940)
    label = f"{outgoing.kind}->{incoming.kind} frontend={frontend:#x}"
    assert not m.read(m.symbols["fifo_len"]), (label, "fallback left audible carry")
    assert not m.read(m.symbols["empty_mask"]), (label, "fallback stranded skipped descriptors")
    assert m.read(TX + 0x14) == m.read(TX + 0x18), (label, "fallback left hardware owners")
    assert_pcm(b"".join(m.output), outgoing.expected, label)


def verify(stock, installed, elf):
    cases = [(4096 + 220, 8192 + 881), (137,), (4096,), (8192,), (8192 + 137,),
             (8192, 8192), (137, 4096 + 1),
             (4096 + 1, 4096 + 511), (4096 + 4095, 4096 + 137),
             (3 * FRAMES + 1, 2 * FRAMES + 220)]
    for frontend in (0x21eb4, 0x21f3c):
        for counts in cases:
            tracks = [numbered(count, 0x100001 + n * 0x100000) for n, count in enumerate(counts)]
            for delayed in (False, True):
                blocked = run_case(stock, installed, elf, tracks, frontend, delayed)
                if delayed and counts == (3 * FRAMES + 1, 2 * FRAMES + 220):
                    assert blocked, "full-ring drain did not wait for a hardware completion"
                print(f"ALAC {counts}, {frontend:#x}, delayed={delayed}: exact PCM through final EOF; blocked={blocked}")
        for kind in (0, 10):
            alac, other = numbered(4316), marked(kind, 0x200001)
            if kind == 0:
                # No compressed MP3/LAME header is supplied by this fixture.
                # Its untimed stream must retain every decoded sample; real
                # MP3 trimming and headers have their own complete codec tests.
                other = File(0, 0, 0, 0, other.blocks, reference_pcm=b"".join(other.blocks))
            for outgoing, incoming in ((alac, other), (other, alac)):
                mixed_fallback(stock, installed, elf, outgoing, incoming, frontend)
                print(f"{outgoing.kind}->{incoming.kind}, {frontend:#x}: native fallback drains all outgoing PCM")
        tracks = [numbered(count, 0x100001 + n * 0x100000)
                  for n, count in enumerate((17 * FRAMES + 137, 18 * FRAMES + 220, 21 * FRAMES + 881))]
        run_case(stock, installed, elf, tracks, frontend, True, retention_limit=4)
        print(f"long ALAC tracks, {frontend:#x}: repeated eight-slot ring wrap with delayed IRQs and exact PCM")
        for fault in ("missing", "zero", "oversized"):
            run_case(stock, installed, elf, [numbered(3 * FRAMES + 137)], frontend,
                     False, lose_count_at=1, count_fault=fault)
            print(f"ALAC {fault} count, {frontend:#x}: prior carry preserved; later tail trim recovers")
            run_case(stock, installed, elf, [numbered(FRAMES + 137)], frontend,
                     False, lose_count_at=0, count_fault=fault)
            print(f"ALAC first {fault} count, {frontend:#x}: following short tail stays contiguous")


def verify_replay(stock, installed, elf, directory):
    """Replay native-decoded real packets against an independent PCM reference."""
    manifest = json.loads((directory / "manifest.json").read_text())
    assert (manifest["sample_rate"], manifest["channels"], manifest["sample_bits"]) == (44100, 2, 16)
    tracks = []
    for track in manifest["tracks"]:
        blocks = [(directory / name).read_bytes() for name in track["block_files"]]
        assert all(len(block) == BYTES for block in blocks)
        actual = track["actual_frames"]
        assert actual == [min(FRAMES, track["frames"] - n * FRAMES) for n in range(len(blocks))]
        expected = (directory / track["expected_file"]).read_bytes()
        assert len(expected) == track["frames"] * 4
        tracks.append(File(11, 0, track["frames"], 0, blocks, reference_pcm=expected))
    for frontend in (0x21eb4, 0x21f3c):
        for delayed in (False, True):
            run_case(stock, installed, elf, tracks, frontend, delayed)
            print(f"real ALAC boundary {[t.count for t in tracks]}, {frontend:#x}, delayed={delayed}: exact independent PCM")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--stock", type=Path, default=REPO / "iPod_4.3.1.1.ipsw")
    parser.add_argument("--elf", type=Path)
    parser.add_argument("--replay", type=Path, help="directory of native packets and independent reference PCM")
    args = parser.parse_args()
    elf = args.elf or args.candidate.with_suffix(".artifacts") / "playback.elf"
    verify(image(args.stock), image(args.candidate), elf)
    if args.replay:
        verify_replay(image(args.stock), image(args.candidate), elf, args.replay)
