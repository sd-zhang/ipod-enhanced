"""Append stateless native performance shortcuts to pinned 4G r35."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'clock_pomodoro'))
from pattern_build import MEMBER, read_archive, repack, word, branch

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT/'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r35.ipsw'
BASE_SHA = '9a3ebaddc983993765bc36e66eb1b1fcb9dea347056acc10ec02946c47e63375'
CODE_SHA = 'ae8a8ffe93cec5958382d6258263cbd3f13c616306879414e55a8f96010349ef'


def validate_script_profile(image):
    """Reject unsafe function/global collisions before installing the shortcut."""
    sys.path.insert(0, str(ROOT/'menu_scroll'))
    from verify_resources import directory
    blob = directory(image)['TCmd'][1][0x2380]
    head = 8+4*struct.unpack_from('<H', blob, 6)[0]
    count, explicit = struct.unpack_from('<HH', blob, head)
    if struct.unpack_from('<H', image, 0x1dfdc2)[0] != 1847 or count < explicit or 1847+count-explicit != 2461:
        raise ValueError('changed implicit ScriptVM functions')
    ids = struct.unpack_from(f'<{explicit}H', blob, head+4)
    if any(2461 <= atom < 2536 for atom in ids):
        raise ValueError('explicit ScriptVM function shadows a global')
    table = (head+4+2*explicit+3)&~3
    offsets = struct.unpack_from(f'<{count+1}I', blob, table)
    body = table+4*(count+1)
    if offsets[0] or body+offsets[-1]+4 > len(blob):
        raise ValueError('invalid ScriptVM directory')
    for index, (begin, end) in enumerate(zip(offsets, offsets[1:])):
        if end-begin < 12 or body+end > len(blob):
            raise ValueError('invalid ScriptVM function extent')
        atom, _, _, _, length = struct.unpack_from('<5H', blob, body+begin)
        expected = 1847+index if index < count-explicit else ids[index-count+explicit]
        if atom != expected or length&1 or length+12 > end-begin:
            raise ValueError('invalid ScriptVM ordinal or bytecode alignment')
    if struct.unpack_from('<HH', blob, body+offsets[-1]) != (75, 0):
        raise ValueError('changed ScriptVM global declarations')


def compile_code(directory, base):
    zig = Path(next(iter(importlib.util.find_spec('ziglang').submodule_search_locations)))/'zig'
    obj, script, elf_path = (directory/name for name in ('playlist.o','performance.ld','stock_performance.elf'))
    subprocess.run(['clang','--target=armv4t-none-eabi','-mcpu=arm7tdmi','-marm',
        '-Os','-ffreestanding','-fno-builtin','-fno-unwind-tables','-fno-asynchronous-unwind-tables',
        '-Wall','-Wextra','-Werror','-c',str(ROOT/'navigation_qol/playlist_cache.c'),'-o',str(obj)],check=True)
    asm = directory/'script.o'
    subprocess.run(['clang','--target=armv4t-none-eabi','-mcpu=arm7tdmi','-marm',
        '-c',str(ROOT/'navigation_qol/script_globals.S'),'-o',str(asm)],check=True)
    script.write_text(f'SECTIONS {{ .text {base} : {{ *(.text*) *(.rodata*) . = ALIGN(4); }} '
        '.bss (NOLOAD) : { *(.bss*) *(COMMON) } .data : { *(.data*) } '
        '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }\n'+
        '\nASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "no added state")')
    subprocess.run([str(zig),'ld.lld','-m','armelf','-T',str(script),'-e','ipod4g_playlist_invalidate',
                    '-o',str(elf_path),str(obj),str(asm)],check=True)
    raw = elf_path.read_bytes()
    elf = ELFFile(io.BytesIO(raw))
    code = elf.get_section_by_name('.text')
    if code['sh_addr'] != base or code['sh_size'] != 404 or hashlib.sha256(code.data()).hexdigest() != CODE_SHA:
        raise ValueError('unexpected performance overlay layout')
    if {s.name for s in elf.iter_sections() if s['sh_flags']&2 and s['sh_size']} != {'.text'}:
        raise ValueError('performance overlay must not add state')
    if any(s['sh_size'] for s in elf.iter_sections() if s['sh_type'] in ('SHT_REL', 'SHT_RELA')):
        raise ValueError('unresolved performance relocation')
    symbols = {s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
               if s['st_info']['type']=='STT_FUNC' and s['st_shndx']!='SHN_UNDEF'}
    if symbols != {'ipod4g_playlist_invalidate':base, 'ipod4g_script_globals':base+364}:
        raise ValueError('unexpected performance symbols')
    return code.data(), raw, symbols


def build(output):
    sys.path.insert(0,str(ROOT/'gapless'))
    from verify_timer_package import validated_parent
    if hashlib.sha256(BASE.read_bytes()).hexdigest()!=BASE_SHA: raise ValueError('r35 source pin')
    validated_parent(BASE)
    art = output.with_suffix('.artifacts')
    if any(p.exists() or p.is_symlink() for p in (output,art)):
        raise ValueError('output exists; preserve previous evidence')
    infos,members,comment = read_archive(BASE)
    firmware = members[MEMBER]
    start,size = 512+word(firmware,0x420c),word(firmware,0x4210)
    image = firmware[start:start+size]
    validate_script_profile(image)
    with TemporaryDirectory() as temporary:
        code,raw,symbols = compile_code(Path(temporary),len(image)-4)
    candidate = bytearray(image[:-4]+code+image[-4:])
    edits=[]
    def replace(at,before,after):
        if word(image,at)!=before: raise ValueError(f'unexpected source word {at:#x}')
        struct.pack_into('<I',candidate,at,after)
        edits.append(at)
    replace(0x53478,0xe92d4038,branch(0x53478,symbols['ipod4g_playlist_invalidate'],0xea))
    replace(0x94a9c,0xe59f210c,branch(0x94a9c,symbols['ipod4g_script_globals'],0xea))
    replace(0xe8,len(image),len(candidate))
    for at in (0x2e8,0x4a8): replace(at,0x04000000+len(image)-4,0x04000000+len(candidate)-4)
    if len(candidate)>0x390000: raise ValueError('performance overlay overlaps RAM')
    carried={p.name:p.read_bytes() for pattern in
        ('*.elf','*-symbols.json','*-layout.json','*-edits.json','diagnostics-cleanup.json')
        for p in BASE.with_suffix('.artifacts').glob(pattern)}
    carried.update({'stock_performance.elf':raw,
        'stock-performance-symbols.json':(json.dumps(symbols,indent=2)+'\n').encode(),
        'stock-performance-edits.json':(json.dumps(edits)+'\n').encode()})
    stock=read_archive(ROOT/'iPod_4.3.1.1.ipsw')[1][MEMBER]
    members[MEMBER]=bytes(repack(stock,candidate))
    output.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temporary:
        staging=Path(temporary)/'candidate.ipsw'
        with ZipFile(staging,'w') as archive:
            archive.comment=comment
            for info in infos: archive.writestr(info,members[info.filename])
        if read_archive(staging)[1:]!=(members,comment): raise ValueError('archive readback differs')
        art.mkdir()
        for name,data in carried.items(): (art/name).write_bytes(data)
        os.link(staging,output)
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    (art/'build.json').write_text(json.dumps(dict(source_sha256=BASE_SHA,sha256=digest,
        osos_sha256=hashlib.sha256(candidate).hexdigest(),osos_bytes=len(candidate),
        headroom=0x390000-len(candidate),performance_code_bytes=len(code),
        added_state_bytes=0,flashed=False),indent=2)+'\n')
    from verify_performance_package import validated_parent as validate_performance
    validate_performance(output)
    return digest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(args.output)
    print('SHA-256',build(args.output))
