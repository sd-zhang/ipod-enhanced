"""Run every emulator check of a gapless build from the original IPSWs.

    verify.py iPod_4.3.1.1.ipsw iPod_25.1.3.ipsw build/gapless [build/gapless-diag]

The 5.5G 1.3 archive supplies the reference MPEG decoder used to encode and
check the MP3 tests. Checks that read diagnostic counters run only on a
build made with --diag. Exits non-zero if any check fails.
"""
import argparse
import hashlib
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile

from build import MEMBER
from image_layout import unpack_stock
from patch_music_request import patch_requests
from patch_track_metadata import patch_osos

SOURCE = Path(__file__).resolve().parent
TESTS = SOURCE / "tests"
REFERENCE_MEMBER = "Firmware-25.6.3"
REFERENCE_HASH = "784ae3d5540fd2f89e8c947b93629e97f62a06dc311d9745aab143e4db6bb251"  # as verify_clip.py


def archive_in(build_dir):
    for name in ("iPod_4.3.1.1-gapless.ipsw", "iPod_4.3.1.1-gapless-diag.ipsw"):
        if (build_dir / name).exists():
            return build_dir / name
    raise FileNotFoundError(f"no gapless IPSW in {build_dir}")


def is_diag(elf):
    with elf.open("rb") as file:
        return bool(ELFFile(file).get_section_by_name(".symtab").get_symbol_by_name("gapless_diag"))


def run(label, script, *args):
    start = time.time()
    result = subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True)
    lines = (result.stdout + result.stderr).strip().splitlines() or [""]
    status = "PASS" if result.returncode == 0 else "FAIL"
    print(f"{status} {time.time() - start:5.0f}s  {label:28s} {lines[-1][:100]}", flush=True)
    if result.returncode:
        print("\n".join("        " + line for line in lines[-12:]))
    return result.returncode == 0


def suite(stock_archive, reference_archive, build_dirs):
    with ZipFile(stock_archive) as ipsw:
        stock = unpack_stock(ipsw.read(MEMBER))
    with ZipFile(reference_archive) as ipsw:
        reference_osos = ipsw.read(REFERENCE_MEMBER)[0x5000:0x73b000]
    if hashlib.sha256(reference_osos).hexdigest() != REFERENCE_HASH:
        raise ValueError("unexpected 5.5G reference firmware")
    passed = True
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        raw, reference = root / "stock.osos", root / "reference.osos"
        timing, requests = root / "timing.osos", root / "requests.osos"
        raw.write_bytes(stock)
        reference.write_bytes(reference_osos)
        timing.write_bytes(patch_osos(stock))
        requests.write_bytes(patch_requests(timing.read_bytes()))
        passed &= run("track metadata", SOURCE / "verify_track_metadata.py", raw, timing)
        passed &= run("music request", SOURCE / "verify_music_request.py", raw, requests)
        for build_dir in build_dirs:
            elf, installed = build_dir / "playback.elf", build_dir / "patched.osos"
            diag = is_diag(elf)
            tag = f"[{build_dir.name}] "
            checks = [
                ("image and hooks", SOURCE / "verify_image.py", stock_archive, archive_in(build_dir), elf),
                ("startup layout", TESTS / "layout_check.py", SOURCE, build_dir),
                ("engine binding", SOURCE / "verify_engine_binding.py", raw, elf),
                ("LAME timing", SOURCE / "verify_lame_timing.py", elf),
                ("Apple Lossless output", SOURCE / "verify_alac.py", raw, installed, elf),
                ("AAC decoder positions", TESTS / "decoder_positions.py", SOURCE, raw, installed, elf),
                ("AAC transitions", TESTS / "aac_transitions.py", SOURCE, raw, installed, elf),
                ("AAC short/priming edges", SOURCE / "repro_aac_edges.py", SOURCE, raw, installed, elf),
                ("empty-block retirement", TESTS / "empty_retirement.py", SOURCE, raw, installed, elf),
                ("cold starts", TESTS / "cold_start.py", SOURCE, raw, installed, elf),
                ("complete track endings", TESTS / "track_end.py", SOURCE, raw, installed, elf),
                ("MP3 transitions and endings", TESTS / "mp3_transitions.py", SOURCE, raw, installed, elf, reference),
                ("request dispose", TESTS / "dispose.py", installed, elf),
            ]
            if diag:
                checks += [
                    ("log file write", TESTS / "diag_log.py", installed),
                ]
            for label, *args in checks:
                passed &= run(tag + label, *args)
    return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stock_4g_ipsw", type=Path)
    parser.add_argument("reference_55g_ipsw", type=Path)
    parser.add_argument("build_dirs", type=Path, nargs="+")
    args = parser.parse_args()
    sys.exit(0 if suite(args.stock_4g_ipsw, args.reference_55g_ipsw, args.build_dirs) else 1)
