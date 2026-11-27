"""Build the gapless playback IPSW from a stock 4G monochrome 4.3.1.1 archive."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from image_layout import branch, extend_osos, repack, replace_word, unpack_stock
from patch_engine_binding import install_binding
from patch_music_request import patch_requests
from patch_track_metadata import patch_osos

SOURCE = Path(__file__).resolve().parent
MEMBER = "Firmware-4.3.1.1"
STEMS = ("clip_pcm.c", "mp3_origin.c", "lame_timing.c", "mp3_origin_hook.S", "tx_span.c",
         "handoff.S", "engine_binding.S", "trim.c", "trim_hooks.S", "copy_frames.S", "alac_hooks.S")

# These sites execute at their OSOS offsets in the low memory mapping.
# Track change (handoff.S), request and file-list plumbing (engine_binding.S,
# patch_*.py), and the MP3 timing probe.
HOOKS = (
    (0x89d80, 0xe1a00006, "gapless_monitor_request", True),
    (0xa9f04, 0xe5c682b6, "gapless_retire_request", True),
    (0xa7f20, 0xeb00d388, "gapless_dispose_request", True),
    (0xdb594, 0xe1a01008, "gapless_mp3_probe_hook", False),
    (0xa7e20, 0xeb00d185, "gapless_stage_successor", True),
    (0x115df0, 0xeb000258, "gapless_decoder_command", True),
    (0x115e64, 0xe3500000, "gapless_check_file_result", True),
    (0x115ed4, 0xe1500001, "gapless_match_decoder", True),
    (0x115f58, 0xeb0003a1, "gapless_cleanup_source", True),
    (0x11656c, 0xe59d1068, "gapless_classify_eof", True),
    (0x1165bc, 0xeb000234, "gapless_drain_at_eof", True),
    (0x113128, 0xeb00c57f, "gapless_reset_output_pointers", True),
    (0x14cef0, 0xeb000377, "gapless_configure_ring", True),
    (0x116484, 0xeb004cfa, "gapless_reset_decoder_output", True),
    (0x11648c, 0xe5880940, "gapless_clear_running", True),
    # trim.c: decoder start, output worker, soft file selection, and the
    # play-mode state test that would silence a held output in state 2.
    (0x1164a0, 0xebfc2900, "gapless_trim_start_hook", True),
    (0x4f540, 0xe5942058, "gapless_alac_decoded_hook", True),
    (0x15a8cc, 0xe1a05000, "gapless_trim_worker_hook", True),
    (0x133548, 0xe1a02007, "gapless_trim_submit_hook", False),
    (0x133be8, 0xebfbb33a, "gapless_trim_release_hook", True),
    (0x116e94, 0xe92d4010, "gapless_trim_finish_hook", False),
    (0x116ea8, 0xeb0056b0, "gapless_trim_finish_poll_hook", True),
    (0x15a9d0, 0xebff3af0, "gapless_trim_account_hook", True),
    (0x116da0, 0xe92d4010, "gapless_trim_select_hook", False),
    (0x123f98, 0xeb0023b2, "gapless_trim_state_hook", True),
)

CONDITIONAL_HOOKS = (
    (0xa7fe4, 0x15d002b8, "gapless_defer_old_request", True, 1),
    (0x14a964, 0x83a00008, "gapless_limit_primary_slots", False, 8),
)

# These pairs are copied to SRAM by RetailOS before execution. A literal
# jump can reach the low SDRAM code from the installed SRAM address.
OVERLAY_HOOKS = (
    (0x346490, 0xe5940e80, 0xebfffe9b, "gapless_trim_publish_hook"),
    (0x346130, 0xe595131c, 0xe1a0f002, "gapless_trim_seek_message_hook"),
    (0x346188, 0xe595131c, 0xe1a0f002, "gapless_trim_seek_message_hook"),
    (0x346408, 0xe59419c0, 0xe1a0f002, "gapless_trim_seek_timer_hook"),
)

# Settings > About > Version calls the stock version formatter here. The
# diagnostic build replaces that call with a page of playback counters.
DIAG_HOOK = (0x766c0, 0xeb02c921, "gapless_diag_version", True)

# Diagnostic event taps (see trim_hooks.S).
DIAG_EVENT_HOOKS = (
    (0x157df4, 0xe92d4070, "gapless_ev_stop_hook", False),
    (0x116de4, 0xe92d4010, "gapless_ev_cleanup_hook", False),
    (0x116774, 0xe5d40314, "gapless_ev_command_hook", True),
    (0x133070, 0xe5dd0000, "gapless_ev_txmode_hook", False),
    (0x1572fc, 0xe92d4030, "gapless_ev_statecast_hook", False),
)

def link_code(directory, clang, linker, diag=False):
    objects = []
    stems = STEMS + (("diag.c",) if diag else ())
    defines = ["-DGAPLESS_STOCK_STRIDE", "-DGAPLESS_TRIM"] + (["-DGAPLESS_DIAG"] if diag else [])
    for name in stems:
        obj = directory / (Path(name).stem + ".o")
        subprocess.run([clang, "--target=armv4t-none-eabi", "-mcpu=arm7tdmi", "-marm",
                        "-Os", "-ffreestanding", "-fno-builtin", "-Wall", "-Wextra",
                        "-Werror", *defines, "-c", SOURCE / name, "-o", obj], check=True)
        objects.append(obj)
    elf = directory / "playback.elf"
    subprocess.run([*linker, "-m", "armelf", "-T", SOURCE / "playback.ld",
                    "-e", "gapless_trim_start_hook", "-o", elf, *objects], check=True)
    return elf


def install(stock, elf, diag=False):
    with elf.open("rb") as file:
        image, symbols = extend_osos(patch_requests(patch_osos(stock)), file)
    install_binding(image, symbols)
    if diag:
        hooks = (DIAG_HOOK,) + DIAG_EVENT_HOOKS
        for offset, original, symbol, link in hooks:
            branch(image, offset, original, symbols[symbol], link=link)
    for offset, original, symbol, link in HOOKS:
        branch(image, offset, original, symbols[symbol], link=link)
    for offset, original, symbol, link, condition in CONDITIONAL_HOOKS:
        branch(image, offset, original, symbols[symbol], link=link, condition=condition)
    for offset, first, second, symbol in OVERLAY_HOOKS:
        replace_word(image, offset, first, 0xe51ff004)
        replace_word(image, offset + 4, second, symbols[symbol])
    return image


def build(archive, directory, clang, linker, diag=False):
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / ("iPod_4.3.1.1-gapless-diag.ipsw" if diag else "iPod_4.3.1.1-gapless.ipsw")
    if archive.resolve() == output.resolve():
        raise ValueError("choose a different destination from the stock archive")
    with TemporaryDirectory(dir=directory) as tmp:
        scratch = Path(tmp)
        elf = link_code(scratch, clang, linker, diag)
        with ZipFile(archive) as ipsw:
            firmware = ipsw.read(MEMBER)
            osos = install(unpack_stock(firmware), elf, diag)
            repacked = repack(firmware, osos)
            candidate = scratch / output.name
            with ZipFile(candidate, "w") as result:
                result.comment = ipsw.comment
                for info in ipsw.infolist():
                    result.writestr(info, repacked if info.filename == MEMBER else ipsw.read(info))
        with ZipFile(candidate) as check:
            if check.testzip() or check.read(MEMBER) != repacked:
                raise ValueError("archive readback failed")
        os.replace(elf, directory / elf.name)
        (scratch / "patched.osos").write_bytes(osos)
        os.replace(scratch / "patched.osos", directory / "patched.osos")
        os.replace(candidate, output)
    print(f"{output}  SHA-256 {hashlib.sha256(output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock_ipsw", type=Path)
    parser.add_argument("build_dir", type=Path)
    parser.add_argument("--clang", default="clang")
    parser.add_argument("--diag", action="store_true",
                        help="show playback counters on Settings > About > Version")
    linkers = parser.add_mutually_exclusive_group()
    linkers.add_argument("--lld", default="ld.lld")
    linkers.add_argument("--zig", help="use this Zig executable's bundled ld.lld")
    args = parser.parse_args()
    build(args.stock_ipsw, args.build_dir, args.clang,
          [args.zig, "ld.lld"] if args.zig else [args.lld], args.diag)
