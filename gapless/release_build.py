"""Remove disabled trace-counter work in place from the pinned r33 release."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'clock_pomodoro'))
sys.path.insert(0, str(ROOT / 'navigation_qol'))
from pattern_build import MEMBER, read_archive, repack, word

BASE = ROOT / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r33.ipsw'
BASE_SHA = '43cdae25c131d2acddc1cb6db4746ea1e4ddc7192510225c435c96a8d9ab2f9f'
# Retain addresses/state ABI; the active r28 playback overlay is not relocated.
# Publish retains NOTE_TRIM and jumps over the removed counter operations.
PATCHES = ((0x38ca6c, 0xe3a0000c, 0xe1a00000),
           (0x38ca78, 0xe5870030, 0xe1a00000),
           (0x38d480, 0xe5983030, 0xe38cc004),
           (0x38d484, 0xe38cc004, 0xea000002),
           (0x38d488, 0xe3530000, 0xe1a00000),
           (0x38d48c, 0x12433001, 0xe1a00000),
           (0x38d490, 0x15883030, 0xe1a00000))


def build(output):
    from verify_wheel_package import validated_parent
    if hashlib.sha256(BASE.read_bytes()).hexdigest() != BASE_SHA:
        raise ValueError('r33 source pin')
    validated_parent(BASE)
    art = output.with_suffix('.artifacts')
    if any(p.exists() or p.is_symlink() for p in (output, art)):
        raise ValueError('output exists; preserve previous evidence')
    infos, members, comment = read_archive(BASE)
    firmware = members[MEMBER]
    start, size = 512 + word(firmware, 0x420c), word(firmware, 0x4210)
    image = bytearray(firmware[start:start + size])
    carried = {p.name:p.read_bytes()
               for pattern in ('*.elf', '*-symbols.json', '*-layout.json', '*-edits.json')
               for p in BASE.with_suffix('.artifacts').glob(pattern)}
    raw = bytearray(carried['alac.elf'])
    code = ELFFile(io.BytesIO(raw)).get_section_by_name('.text')
    base, count, offset = code['sh_addr'], code['sh_size'], code['sh_offset']
    if image[base:base + count] != code.data():
        raise ValueError('ALAC ELF/image mismatch')
    for at, before, after in PATCHES:
        if not base <= at < base + count or word(image, at) != before:
            raise ValueError(f'unexpected diagnostic instruction at {at:#x}')
        struct.pack_into('<I', image, at, after)
        struct.pack_into('<I', raw, offset + at - base, after)
    carried['alac.elf'] = bytes(raw)
    carried['diagnostics-cleanup.json'] = (json.dumps(dict(
        source_sha256=BASE_SHA, edits=[p[0] for p in PATCHES],
        added_code_bytes=0, added_state_bytes=0), indent=2) + '\n').encode()
    stock = read_archive(ROOT / 'iPod_4.3.1.1.ipsw')[1][MEMBER]
    members[MEMBER] = bytes(repack(stock, image))
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temporary:
        staging = Path(temporary) / 'candidate.ipsw'
        with ZipFile(staging, 'w') as archive:
            archive.comment = comment
            for info in infos:
                archive.writestr(info, members[info.filename])
        if read_archive(staging)[1:] != (members, comment):
            raise ValueError('archive readback differs')
        art.mkdir()
        for name, data in carried.items():
            (art / name).write_bytes(data)
        os.link(staging, output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (art / 'build.json').write_text(json.dumps(dict(source_sha256=BASE_SHA,
        sha256=digest, osos_sha256=hashlib.sha256(image).hexdigest(),
        osos_bytes=len(image), headroom=0x390000-len(image),
        added_code_bytes=0, added_state_bytes=0, flashed=False), indent=2)+'\n')
    return digest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(args.output)
    print('SHA-256', build(args.output))
