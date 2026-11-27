"""Check native ALAC packet boundaries in a packaged 4G candidate.

Run with the repository's .venv outside the macOS sandbox (Unicorn uses JIT).
The original MP4 parser, ALAC decoder, seek method, ready method and SRAM
pump execute. Only output allocation/publication and OS services are supplied.
Production start, decoded-count and publish hooks execute without replacement.
This fixture checks PCM before the DSP; output-worker/I2S checks live separately.
"""
import argparse
import hashlib
import json
import mmap
import struct
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from elftools.elf.elffile import ELFFile
from unicorn import UC_HOOK_CODE
from unicorn import arm_const as arm

from make_test_album import write_wav
from verify_aac import AACDecoder, HANDLE, OUTPUT, SERVICES, STOCK_ENGINE
from verify_track_metadata import REGS, TRACK

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "navigation_qol"))
from native_fixture import load_image

FRAMES, FRAME_BYTES = 4096, 4
BYTES = FRAMES * FRAME_BYTES
ALAC_TABLE = 0x2419c
OPEN, DECODE, READY, SEEK = 0xdc040, 0x4f2d4, 0x4ec5c, 0xc5118
SUCCESS, PUMP, NATIVE_PUBLISH = 0x4f540, 0x40015914, 0x400154f0
QUEUE = 0x620000
COMPRESSED = 0x630000
FIELDS = (0x8050, 0x8054, 0x8058, 0x805c, 0x8070, 0x8084, 0x8088)


def symbols(path):
    with path.open("rb") as file:
        table = ELFFile(file).get_section_by_name(".symtab")
        return {entry.name: entry["st_value"] for entry in table.iter_symbols()}


def packet_info(path):
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_streams",
        "-show_packets", "-show_entries",
        "stream=codec_name,sample_rate,channels,bits_per_raw_sample:packet=pos,size,duration",
        "-of", "json", str(path),
    ], check=True, capture_output=True, text=True)
    info = json.loads(result.stdout)
    streams = info.get("streams", [])
    if not streams:
        return [], None
    stream = streams[0]
    assert stream["codec_name"] == "alac", (path, stream)
    assert int(stream["sample_rate"]) == 44100 and stream["channels"] == 2, (path, stream)
    assert int(stream["bits_per_raw_sample"]) == 16, (path, stream)
    packets = [{key: int(value) for key, value in packet.items()}
               for packet in info.get("packets", [])]
    return packets, stream


class PacketFile(AACDecoder):
    """Supply one compressed read without mapping an entire album into ARM RAM."""

    def __init__(self, image, data):
        super().__init__(image, b"")
        self.data = data
        self.cpu.mem_map(COMPRESSED, 0x10000)

    def service(self, cpu, address, size, unused):
        if address != SERVICES + 12:
            return super().service(cpu, address, size, unused)
        destination = cpu.reg_read(REGS[1])
        count = cpu.reg_read(REGS[2])
        assert 0 < count <= 0x10000 and self.file_pos + count <= len(self.data)
        cpu.mem_write(COMPRESSED, self.data[self.file_pos:self.file_pos + count])
        self.write(destination, COMPRESSED)
        cpu.reg_write(REGS[0], count)
        cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))


class NativeALAC:
    """Observe actual counts while running the packaged decoder/pump hooks."""

    def __init__(self, image, linked, data, total_frames=0):
        self.linked = linked
        self.m = m = PacketFile(image, data)
        m.write(HANDLE + 8, 0x1451e030)
        m.cpu.mem_map(0x60003000, 4096)
        m.cpu.mem_map(QUEUE, 0x10000)
        for ctor in (0x1d5c8, 0x1d5d4, 0x1d5e0, 0x1d608, 0x1d718, 0x1d740):
            m.call(ctor)
        registry = STOCK_ENGINE + 12
        m.write(registry, 0x1d16d4)
        info = STOCK_ENGINE + 0x2e3c
        self.metadata_result = m.call(0x131f60, 77, TRACK, info)
        self.successes, self.callbacks, self.published, self.seeks = [], [], [], []
        self.decoder = 0
        if self.metadata_result:
            return
        assert m.cpu.mem_read(info + 24, 1) == b"\x0b"
        self.decoder = m.call(0x20730, registry, m.call(0x208a0, registry, 11), STOCK_ENGINE)
        assert m.read(self.decoder) == ALAC_TABLE
        m.write(STOCK_ENGINE + 0x29b0, 1000)
        m.write(STOCK_ENGINE + 0x29c0, 0)
        m.parser_pos = 0
        # Open builds packet-table state; a continuous album has many more
        # packets than a single song, so allow its native parser to finish.
        m.call(OPEN, self.decoder, info, 77, TRACK, instruction_limit=25000000)
        m.write(STOCK_ENGINE + 0x2e80, QUEUE)
        m.write(STOCK_ENGINE + 0x90cc, FRAMES)
        m.write(STOCK_ENGINE + 0x90c8, BYTES)
        m.write(0x144b22ec, BYTES)
        m.cpu.mem_write(QUEUE + 0x94, bytes((2, 16)))
        m.write(QUEUE + 0xd0, 1)
        timing = linked["gapless_engine_timing"]
        m.cpu.mem_write(timing, struct.pack("<9I", 0, total_frames, 0, 0, 0, 0, 0, 0, 1))
        for address in (SUCCESS, SEEK):
            m.cpu.hook_add(UC_HOOK_CODE, self.observe, begin=address, end=address)
        callback = linked.get("gapless_alac_decoded")
        if callback:
            m.cpu.hook_add(UC_HOOK_CODE, self.observe, begin=callback, end=callback)

    def state(self):
        return {offset: self.m.read(self.decoder + offset) for offset in FIELDS}

    def observe(self, cpu, pc, size, unused):
        m = self.m
        if pc == SUCCESS:
            sp = cpu.reg_read(arm.UC_ARM_REG_SP)
            actual, output = m.read(sp + 8), m.read(sp + 0x2c)
            assert actual <= FRAMES, ("native ALAC count exceeds capacity", actual)
            self.successes.append({"actual": actual, "output": output,
                                   "packet": m.read(self.decoder + 0x8050),
                                   "sequence": m.read(self.decoder + 0x8084)})
        elif pc == SEEK:
            self.seeks.append(cpu.reg_read(REGS[1]))
        else:
            self.callbacks.append(tuple(cpu.reg_read(reg) for reg in REGS[:4]))
        # Observation only: let every native/production instruction execute.

    def start(self):
        assert self.decoder and not self.m.read(self.decoder + 0x8070), self.state()
        self.m.call(self.linked["gapless_trim_start"], STOCK_ENGINE, self.decoder)

    def return_to_caller(self, value=0):
        cpu = self.m.cpu
        cpu.reg_write(REGS[0], value)
        cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))

    def output_service(self, cpu, pc, size, unused):
        m = self.m
        if pc == 0x40017a30:  # Output allocator; native decode/ready still run.
            m.write(STOCK_ENGINE + 0x1948, OUTPUT)
        elif pc == NATIVE_PUBLISH:
            # The installed publish hook has already executed at 0x40015a78.
            assert len(self.successes) == len(self.published) + 1
            capture = dict(self.successes[-1])
            capture["pcm"] = bytes(cpu.mem_read(capture["output"], BYTES))
            self.published.append(capture)
            assert m.read(self.decoder + 0x8088) == len(self.published)
        self.return_to_caller()

    def pump(self):
        self.start()
        m = self.m
        m.write(STOCK_ENGINE + 0x1948, OUTPUT)
        m.write(STOCK_ENGINE + 0x1940, 1)
        # Services only. In particular, neither SUCCESS nor the production
        # start/count/publish hook is skipped or replaced by fixture output.
        for address in (0x40017a30, NATIVE_PUBLISH, 0x11c518, 0x13bc00, 0xdad6c):
            m.cpu.hook_add(UC_HOOK_CODE, self.output_service, begin=address, end=address)
        m.call(PUMP, STOCK_ENGINE, self.decoder, instruction_limit=150000000)
        assert not self.seeks, ("unexpected cold-pump reposition", self.seeks)
        assert m.read(self.decoder + 0x8084) == m.read(self.decoder + 0x8088) == len(self.published)
        assert m.read(self.decoder + 0x8070) == 1
        self.check_callbacks()
        return self.published

    def decode(self, speed=1000):
        m = self.m
        before = len(self.successes)
        already_finished = m.read(self.decoder + 0x8070)
        m.cpu.mem_write(OUTPUT, b"\xa5" * BYTES)
        result = m.call(DECODE, self.decoder, OUTPUT, speed & 0xffffffff,
                        instruction_limit=25000000)
        ready = m.call(READY, self.decoder)
        assert result == ready, (result, ready, self.state())
        assert len(self.successes) == before + result
        assert not m.call(READY, self.decoder), "ready published the same block twice"
        if not result:
            # A failing core may have touched its scratch output. Only an
            # already-latched EOF guarantees it never enters the core.
            if already_finished:
                assert bytes(m.cpu.mem_read(OUTPUT, BYTES)) == b"\xa5" * BYTES
            self.check_callbacks()
            return None
        block = dict(self.successes[-1])
        block["pcm"] = bytes(m.cpu.mem_read(OUTPUT, BYTES))
        self.check_callbacks()
        return block

    def check_callbacks(self):
        if "gapless_alac_decoded" not in self.linked:
            return  # Baseline red remains the observed PCM mismatch.
        expected = [(item["output"], item["actual"], FRAMES, STOCK_ENGINE)
                    for item in self.successes]
        assert self.callbacks == expected, ("decoded-count callback ABI", self.callbacks, expected)


def valid_pcm(block):
    used = block["actual"] * FRAME_BYTES
    assert block["pcm"][used:] == bytes(BYTES - used), "native final-block padding changed"
    return block["pcm"][:used]


def source_pcm(count):
    return b"".join(struct.pack("<hh", (frame * 17) % 30000 + 1,
                                -((frame * 29) % 30000 + 1)) for frame in range(count))


def encode(directory, count):
    pcm = source_pcm(count)
    source, encoded = directory / f"{count}.wav", directory / f"{count}.m4a"
    write_wav(source, pcm)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source),
                    "-c:a", "alac", str(encoded)], check=True)
    return encoded, pcm


def check_track(image, linked, path, pcm):
    fixture = NativeALAC(image, linked, path.read_bytes(), len(pcm) // FRAME_BYTES)
    assert fixture.metadata_result == 0, (path.name, fixture.metadata_result)
    blocks = fixture.pump()
    actual = b"".join(valid_pcm(block) for block in blocks)
    expected_counts = [min(FRAMES, len(pcm) // FRAME_BYTES - start)
                       for start in range(0, len(pcm) // FRAME_BYTES, FRAMES)]
    assert actual == pcm, (f"{path.name}: native pump lost or changed PCM", len(actual) // 4,
                           len(pcm) // 4, [block["packet"] for block in blocks])
    assert [block["actual"] for block in blocks] == expected_counts
    assert [block["packet"] for block in blocks] == list(range(len(blocks)))
    assert fixture.state()[0x8058] == FRAMES and fixture.state()[0x805c] == BYTES
    print(f"native pump {path.name}: source PCM preserved; actual block frames {expected_counts}", flush=True)


def check_positions(image, linked, path, pcm):
    def expect(fixture, packet, speed=1000):
        block = fixture.decode(speed)
        assert block and block["packet"] == packet, (packet, block, fixture.state())
        assert valid_pcm(block) == pcm[packet * BYTES:(packet + 1) * BYTES]

    for target in (0, 1, 92, 93, 186):
        fixture = NativeALAC(image, linked, path.read_bytes())
        fixture.start()
        expect(fixture, 0)
        fixture.m.call(SEEK, fixture.decoder, target)
        expect(fixture, target * 44100 // (1000 * FRAMES) + 1)
    for name, state, target, speed, expected in (
        ("nonzero start", 0, 93, 1000, 2),
        ("forward scan", 3, 0, 2000, 2),
        ("reverse scan", 4, 186, -1000, 1),
    ):
        fixture = NativeALAC(image, linked, path.read_bytes())
        m = fixture.m
        m.cpu.mem_write(STOCK_ENGINE + 0x2994, bytes([state]))
        m.write(STOCK_ENGINE + 0x29b0, speed & 0xffffffff)
        m.write(STOCK_ENGINE + 0x29c0, target)
        if target:
            m.call(SEEK, fixture.decoder, target)
        index = m.read(fixture.decoder + 0x8050)
        fixture.start()
        assert m.read(fixture.decoder + 0x8050) == index, name
        expect(fixture, expected, speed)
    fixture = NativeALAC(image, linked, path.read_bytes())
    fixture.start()
    expect(fixture, 0)
    fixture.start()  # A second start call must not replay packet zero.
    expect(fixture, 1)
    fixture.m.parser_pos = 0
    fixture.m.call(OPEN, fixture.decoder, STOCK_ENGINE + 0x2e3c, 77, TRACK)
    fixture.m.write(linked["gapless_engine_timing"] + 32, 2)
    fixture.start()  # The singleton decoder now owns a newly opened track.
    expect(fixture, 0)
    print("native seek, forward scan, reverse scan and nonzero-start origins preserved", flush=True)


def check_errors(image, linked, path, pcm):
    packets, _ = packet_info(path)
    corrupt = bytearray(path.read_bytes())
    at = packets[1]["pos"]
    # Unsupported ALAC element type 2: deterministic decoder rejection,
    # without destroying the MP4 packet tables or changing packet lengths.
    corrupt[at] = (corrupt[at] & 0x1f) | 0x40
    fixture = NativeALAC(image, linked, bytes(corrupt))
    fixture.m.call(SEEK, fixture.decoder, 0)  # Native seek selects packet one next.
    assert fixture.decode() is None
    assert fixture.state()[0x8054] == 1 and fixture.state()[0x8084] == 0
    block = fixture.decode()
    assert block and block["packet"] == 2 and valid_pcm(block) == pcm[2 * BYTES:3 * BYTES]
    tail = fixture.decode()
    assert tail and tail["actual"] == 137 and valid_pcm(tail) == pcm[3 * BYTES:]
    assert fixture.decode() is None
    after_eof = fixture.state()
    assert fixture.decode() is None and fixture.state() == after_eof
    assert [item["actual"] for item in fixture.successes] == [FRAMES, 137]
    print("malformed packet, subsequent good packet and repeated EOF preserve count ownership", flush=True)


def check_empty(image, linked, directory):
    path, _ = encode(directory, 0)
    packets, stream = packet_info(path)
    if stream is None:
        print("zero-frame encode has no audio stream; no valid empty ALAC case", flush=True)
        return
    assert not packets
    fixture = NativeALAC(image, linked, path.read_bytes())
    if fixture.metadata_result or fixture.m.read(fixture.decoder + 0x8070):
        print("native parser/open cleanly rejects zero-packet ALAC", flush=True)
        return
    assert fixture.pump() == []
    print("zero-packet ALAC reaches EOF without publishing PCM", flush=True)


def reference_pcm(path, start, end):
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-af",
        f"atrim=start_sample={start}:end_sample={end}", "-f", "s16le", "-",
    ], check=True, capture_output=True)
    return result.stdout


def check_library_track(image, linked, path):
    packets, _ = packet_info(path)
    assert packets and all(0 < item["duration"] <= FRAMES for item in packets)
    # The artist directory also contains a 649 MB continuous album. Read-only
    # file mapping plus the bounded packet service avoids copying it into ARM
    # memory or retaining a second host copy just to test its two boundaries.
    with path.open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as data:
        fixture = NativeALAC(image, linked, data)
        fixture.start()
        first = fixture.decode()
        assert first and first["packet"] == 0
        assert valid_pcm(first) == reference_pcm(path, 0, packets[0]["duration"]), path
        if len(packets) > 1:
            # Set the normal native seek target immediately before the last
            # packet. No direct packet-index writes or decoder bypasses.
            wanted = len(packets) - 2
            milliseconds = (wanted * FRAMES * 1000 + 44099) // 44100
            fixture.m.call(SEEK, fixture.decoder, milliseconds)
            last = fixture.decode()
        else:
            last = first
        total = sum(item["duration"] for item in packets)
        assert last and last["packet"] == len(packets) - 1
        assert last["actual"] == packets[-1]["duration"]
        assert valid_pcm(last) == reference_pcm(path, total - last["actual"], total), path
        assert fixture.decode() is None
        print(f"library {path.name}: packet zero preserved; final {last['actual']}/{FRAMES} frames", flush=True)


def check_library(image, linked, directory):
    files = [directory] if directory.is_file() else sorted(directory.rglob("*.m4a"))
    assert files, f"no .m4a files found in {directory}"
    for path in files:
        check_library_track(image, linked, path)


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_boundary(image, linked, candidate, outgoing, incoming, directory):
    """Export only the native boundary packets and independent PCM references."""
    if directory.exists():
        raise FileExistsError(f"refusing to overwrite boundary export: {directory}")
    payloads, tracks, expected = {}, [], []
    for label, source, tail in (("a", outgoing, True), ("b", incoming, False)):
        packets, _ = packet_info(source)
        assert packets and all(0 < packet["duration"] <= FRAMES for packet in packets), source
        total = sum(packet["duration"] for packet in packets)
        with source.open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as data:
            fixture = NativeALAC(image, linked, data)
            fixture.start()
            if tail and len(packets) > 1:
                wanted = len(packets) - 2
                milliseconds = (wanted * FRAMES * 1000 + 44099) // 44100
                fixture.m.call(SEEK, fixture.decoder, milliseconds)
            indices = [len(packets) - 1] if tail else list(range(min(2, len(packets))))
            blocks = [fixture.decode() for _ in indices]
            assert all(block is not None for block in blocks), source
            assert [block["packet"] for block in blocks] == indices, source
            assert [block["actual"] for block in blocks] == [packets[i]["duration"] for i in indices]
            start = total - packets[-1]["duration"] if tail else 0
            count = sum(block["actual"] for block in blocks)
            reference = reference_pcm(source, start, start + count)
            assert b"".join(valid_pcm(block) for block in blocks) == reference, source
            if tail:
                assert fixture.decode() is None
        block_files = []
        for index, block in enumerate(blocks):
            name = f"{label}-{index}.pcm"
            payloads[name] = block["pcm"]
            block_files.append(name)
        reference_name = f"{label}-expected.pcm"
        payloads[reference_name] = reference
        expected.append(reference)
        tracks.append({
            "label": label, "source": str(source.resolve()),
            "source_sha256": file_sha256(source), "source_bytes": source.stat().st_size,
            "source_start_frame": start, "source_total_frames": total,
            "packet_indices": indices, "actual_frames": [block["actual"] for block in blocks],
            "block_files": block_files, "expected_file": reference_name, "frames": count,
        })
    payloads["expected.pcm"] = b"".join(expected)
    manifest = {
        "schema_version": 1,
        "candidate": str(candidate.resolve()), "candidate_sha256": file_sha256(candidate),
        "candidate_osos_sha256": hashlib.sha256(image).hexdigest(),
        "sample_rate": 44100, "channels": 2, "sample_bits": 16,
        "native_capacity_frames": FRAMES, "tracks": tracks,
        "expected_frames": sum(track["frames"] for track in tracks),
        "command": [sys.executable, *sys.argv], "working_directory": str(Path.cwd()),
        "artifacts": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                      for name, data in payloads.items()},
    }
    # Every native/reference comparison finishes before creating the export.
    # Exclusive directory and file creation also refuse an overwrite race.
    directory.mkdir(parents=True, exist_ok=False)
    for name, data in payloads.items():
        with (directory / name).open("xb") as file:
            file.write(data)
    with (directory / "manifest.json").open("x") as file:
        json.dump(manifest, file, indent=2)
        file.write("\n")
    print(f"exported native ALAC boundary: {directory / 'manifest.json'} "
          f"({manifest['expected_frames']} true frames)", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--elf", type=Path, help="playback ELF (default: candidate .artifacts/playback.elf)")
    parser.add_argument("--extension-elf", type=Path, help="ALAC extension ELF (default: alac.elf beside playback ELF)")
    parser.add_argument("--library", nargs="?", type=Path,
                        const=Path.home() / "Music/Music/Media.localized/Music/EURO TRANCE PROJECT",
                        help="check a read-only file or directory (default: EURO TRANCE PROJECT library)")
    parser.add_argument("--export-boundary", type=Path, metavar="DIR",
                        help="create a new directory with outgoing last/incoming first two native packets")
    parser.add_argument("--outgoing", type=Path, metavar="FILE", help="outgoing ALAC source for boundary export")
    parser.add_argument("--incoming", type=Path, metavar="FILE", help="incoming ALAC source for boundary export")
    args = parser.parse_args()
    if any((args.export_boundary, args.outgoing, args.incoming)):
        if not all((args.export_boundary, args.outgoing, args.incoming)):
            parser.error("--export-boundary, --outgoing and --incoming must be supplied together")
        if args.export_boundary.exists():
            parser.error(f"refusing to overwrite boundary export: {args.export_boundary}")
        for source in (args.outgoing, args.incoming):
            if not source.is_file():
                parser.error(f"ALAC source not found: {source}")
    elf = args.elf or args.candidate.with_suffix(".artifacts") / "playback.elf"
    linked = symbols(elf)
    extension = args.extension_elf or elf.with_name("alac.elf")
    if extension.exists():
        # Keep the stable playback entry so its installed branch is tested;
        # extension symbols describe the callback, not an alternate start.
        for name, address in symbols(extension).items():
            linked.setdefault(name, address)
    elif args.extension_elf:
        parser.error(f"extension ELF not found: {extension}")
    image = load_image(args.candidate)
    with TemporaryDirectory(prefix="alac-boundaries-") as temp:
        directory = Path(temp)
        # Start with the demonstrated regression: three full packets and a
        # 137-frame tail, then exact multiples and every one-packet boundary.
        for count in (3 * FRAMES + 137, 2 * FRAMES, FRAMES + 1, FRAMES, FRAMES - 1, 137, 1):
            path, pcm = encode(directory, count)
            check_track(image, linked, path, pcm)
            if count == 3 * FRAMES + 137:
                check_positions(image, linked, path, pcm)
                check_errors(image, linked, path, pcm)
        check_empty(image, linked, directory)
    if args.library:
        check_library(image, linked, args.library)
    assert "gapless_alac_decoded" in linked, "missing capture symbols: provide the candidate's alac.elf"
    if args.export_boundary:
        export_boundary(image, linked, args.candidate, args.outgoing, args.incoming, args.export_boundary)
    print("ALAC native boundary regressions passed", flush=True)


if __name__ == "__main__":
    main()
