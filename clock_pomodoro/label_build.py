"""Rename pinned r27's Backlight Alerts caption in place, without rebuilding code."""
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

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / 'gapless'), str(REPO / 'menu_scroll')]
from image_layout import repack, word
from verify_resources import directory, ROOT

SOURCE = REPO / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r27.ipsw'
SOURCE_SHA = 'd398d8be76cc3386319d6898d796b7491b67b10c03b03ef67ae55b9f742d9452'
MEMBER = 'Firmware-4.3.1.1'
CAPTION_AT, ENTRY_AT = 0x38ad10, 0x38bdd4
OLD_CAPTION, NEW_CAPTION = b'Backlight Alerts\0', b'Alarm Backlight\0'


def read_archive(path):
    with ZipFile(path) as archive:
        infos = archive.infolist()
        if len({i.filename for i in infos}) != len(infos):
            raise ValueError('duplicate archive member')
        return infos, {i.filename: archive.read(i) for i in infos}, archive.comment


def rename_caption(image):
    header, resources = directory(image)['Str ']
    table = ROOT + word(image, header + 12)
    entries = [(table + index * 12, offset, size)
               for index, (key, offset, size) in enumerate(struct.iter_unpack(
                   '<III', image[table:table + len(resources) * 12])) if key == 0x6150]
    if entries != [(ENTRY_AT, CAPTION_AT - ROOT - word(image, ROOT + 4), len(OLD_CAPTION))]:
        raise ValueError('caption directory entry is not at the pinned r27 address')
    if resources[0x6150] != OLD_CAPTION or image[CAPTION_AT:CAPTION_AT + 17] != OLD_CAPTION:
        raise ValueError('expected exact Backlight Alerts caption')
    result = bytearray(image)
    result[CAPTION_AT:CAPTION_AT + len(OLD_CAPTION)] = NEW_CAPTION.ljust(len(OLD_CAPTION), b'\0')
    struct.pack_into('<I', result, ENTRY_AT + 8, len(NEW_CAPTION))
    return result


def build_candidate(source, stock, output):
    artifacts = output.with_suffix('.artifacts')
    for path in (output, artifacts):
        if path.exists() or path.is_symlink():
            raise ValueError(f'output already exists; preserve previous evidence: {path}')
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError('expected pinned r27 archive')
    infos, members, comment = read_archive(source)
    _, original, _ = read_archive(stock)
    if members.keys() != original.keys() or any(members[k] != original[k] for k in members if k != MEMBER):
        raise ValueError('unrelated archive members differ from stock')
    firmware = members[MEMBER]
    start, size = 512 + word(firmware, 0x420c), word(firmware, 0x4210)
    image = firmware[start:start + size]
    if firmware != repack(original[MEMBER], image) or image[-4:] != bytes.fromhex('55aa55aa'):
        raise ValueError('invalid source repack')
    candidate = rename_caption(image)
    carried = {p.name: p.read_bytes()
               for pattern in ('*.elf', '*-symbols.json', '*-layout.json', '*-edits.json')
               for p in source.with_suffix('.artifacts').glob(pattern)}
    expected_elfs = {'letters', 'actions', 'volume', 'hotpaths', 'otg', 'pomodoro',
                     'playback', 'refinements', 'alarm_light'}
    if {Path(name).stem for name in carried if name.endswith('.elf')} != expected_elfs:
        raise ValueError('missing or unexpected source ELF artifacts')
    for name, raw in carried.items():
        if not name.endswith('.elf'):
            continue
        elf = ELFFile(io.BytesIO(raw))
        for section in elf.iter_sections():
            if section['sh_flags'] & 2 and section['sh_type'] != 'SHT_NOBITS':
                at, size = section['sh_addr'], section['sh_size']
                if image[at:at + size] != section.data() or candidate[at:at + size] != section.data():
                    raise ValueError(f'{name} ELF differs from source or candidate code')
    members[MEMBER] = bytes(repack(original[MEMBER], candidate))
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temporary:
        staging = Path(temporary) / 'candidate.ipsw'
        with ZipFile(staging, 'w') as archive:
            archive.comment = comment
            for info in infos:
                archive.writestr(info, members[info.filename])
        if read_archive(staging)[1:] != (members, comment):
            raise ValueError('archive readback differs')
        artifacts.mkdir()
        for name, raw in carried.items():
            (artifacts / name).write_bytes(raw)
        os.link(staging, output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (artifacts / 'build.json').write_text(json.dumps(dict(
        source_sha256=SOURCE_SHA, sha256=digest,
        osos_sha256=hashlib.sha256(candidate).hexdigest(), osos_bytes=len(candidate),
        headroom=0x390000 - len(candidate), caption_resource='Str 0x6150',
        caption='Alarm Backlight', caption_offset=CAPTION_AT,
        caption_allocation_bytes=len(OLD_CAPTION), caption_bytes=len(NEW_CAPTION),
        length_offset=ENTRY_AT + 8,
        osos_changed_offsets=[i for i, (old, new) in enumerate(zip(image, candidate)) if old != new],
        code_rebuilt=False, flashed=False), indent=2) + '\n')
    return digest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--stock', type=Path, default=REPO / 'iPod_4.3.1.1.ipsw')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(args.output)
    print('SHA-256', build_candidate(args.source, args.stock, args.output))
