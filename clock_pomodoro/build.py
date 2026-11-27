"""Build a separate Pomodoro/fast-scroll candidate; never flash or overwrite."""
import argparse
import hashlib
import io
import json
import os
import struct
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile
from pomodoro_patch import install

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'gapless'))
from image_layout import repack, word

SOURCE = REPO / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r19.ipsw'
MEMBER = 'Firmware-4.3.1.1'


def read_archive(path):
    with ZipFile(path) as archive:
        infos = archive.infolist()
        if len({i.filename for i in infos}) != len(infos):
            raise ValueError('duplicate archive members')
        return infos, {i.filename: archive.read(i) for i in infos}


def build_candidate(source, stock, output):
    if output.exists() or output.is_symlink():
        raise ValueError('output already exists; choose a new path')
    artifacts = output.with_suffix('.artifacts')
    if artifacts.exists() or artifacts.is_symlink():
        raise ValueError('review artifacts already exist; choose a new path')
    infos, members = read_archive(source)
    _, original = read_archive(stock)
    if members.keys() != original.keys() or MEMBER not in members:
        raise ValueError('expected monochrome 4G archive')
    if any(members[k] != original[k] for k in members if k != MEMBER):
        raise ValueError('source metadata differs from stock')
    firmware = members[MEMBER]
    start, size = 512 + word(firmware, 0x420c), word(firmware, 0x4210)
    image = firmware[start:start + size]
    if firmware != repack(original[MEMBER], image):
        raise ValueError('source is not an exact checked stock repack')
    carried = {}
    for name in ('letters', 'actions', 'volume', 'hotpaths', 'otg'):
        raw = (source.with_suffix('.artifacts') / (name + '.elf')).read_bytes()
        elf = ELFFile(io.BytesIO(raw))
        section = elf.get_section_by_name('.text')
        at, count = section['sh_addr'], section['sh_size']
        if image[at:at + count] != section.data():
            raise ValueError(f'{name} review ELF differs from source image')
        carried[name + '.elf'] = raw
        carried[name + '-symbols.json'] = (
            source.with_suffix('.artifacts') / (name + '-symbols.json')).read_bytes()
    candidate = install(image, artifacts)
    # Keep the hotpaths review ELF byte-exact to the candidate. Only its one
    # letter-mode call site changes; addresses/symbols and all other bytes stay.
    raw = bytearray(carried['hotpaths.elf'])
    section = ELFFile(io.BytesIO(raw)).get_section_by_name('.text')
    offset = section['sh_offset'] + 0x3802b4 - section['sh_addr']
    raw[offset:offset + 4] = candidate[0x3802b4:0x3802b8]
    carried['hotpaths.elf'] = bytes(raw)
    for name, raw in carried.items():
        (artifacts / name).write_bytes(raw)
    members[MEMBER] = bytes(repack(original[MEMBER], candidate))
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temporary:
        staging = Path(temporary) / 'candidate.ipsw'
        with ZipFile(staging, 'w') as archive:
            for info in infos:
                archive.writestr(info, members[info.filename])
        if read_archive(staging)[1] != members:
            raise ValueError('archive readback differs')
        os.link(staging, output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (artifacts / 'build.json').write_text(json.dumps({
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'sha256': digest, 'osos_sha256': hashlib.sha256(candidate).hexdigest(),
        'osos_bytes': len(candidate), 'headroom': 0x390000 - len(candidate),
        'fast_scroll_site': 0x3802b4, 'flashed': False,
    }, indent=2) + '\n')
    return digest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--stock', type=Path, default=REPO / 'iPod_4.3.1.1.ipsw')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(f'{args.output}\nSHA-256 {build_candidate(args.source, args.stock, args.output)}')
