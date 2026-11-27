"""Build the navigation candidate without rebuilding playback or accessing a device."""
import argparse
import hashlib
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from patch import install

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "gapless"))
from image_layout import OSOS_ENTRY, SECTOR, repack, word

MEMBER = "Firmware-4.3.1.1"


def read_archive(path):
    with ZipFile(path) as archive:
        infos = archive.infolist()
        if len({info.filename for info in infos}) != len(infos):
            raise ValueError("duplicate archive members")
        return infos, {info.filename: archive.read(info) for info in infos}


def build(source, stock, output, installer=install):
    if output.exists() or output.is_symlink():
        raise ValueError("output already exists; choose a new path")
    infos, members = read_archive(source)
    _, original = read_archive(stock)
    if members.keys() != original.keys() or MEMBER not in members:
        raise ValueError("expected a monochrome 4G 4.3.1.1 archive")
    if any(members[name] != original[name] for name in members if name != MEMBER):
        raise ValueError("source archive metadata differs from stock")
    firmware = members[MEMBER]
    if len(firmware) < 0x4400:
        raise ValueError("truncated firmware header")
    start = SECTOR + word(firmware, OSOS_ENTRY + 12)
    size = word(firmware, OSOS_ENTRY + 16)
    image = firmware[start:start + size]
    if firmware != repack(original[MEMBER], image):
        raise ValueError("source firmware is not an exact checked stock repack")
    members[MEMBER] = bytes(repack(original[MEMBER], installer(image)))
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temporary:
        candidate = Path(temporary) / "candidate.ipsw"
        with ZipFile(candidate, "w") as archive:
            for info in infos:
                archive.writestr(info, members[info.filename])
        if read_archive(candidate)[1] != members:
            raise ValueError("archive readback differs")
        os.link(candidate, output)  # atomic publication; never overwrite
    return hashlib.sha256(output.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPO / "build/gapless-menu-scroll/iPod_4.3.1.1-gapless-menu-scroll.ipsw")
    parser.add_argument("--stock", type=Path, default=REPO / "iPod_4.3.1.1.ipsw")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        digest = build(args.source, args.stock, args.output)
    except (ValueError, OSError) as error:
        parser.exit(1, f"build failed: {error}\n")
    print(f"{args.output}\nSHA-256 {digest}\nAdded 64 bytes; compilation artist filtering and Hold Menu → home.")
