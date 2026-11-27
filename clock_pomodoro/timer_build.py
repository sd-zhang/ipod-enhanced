"""Append Pomodoro UI timer delivery recovery to pinned r34."""
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
from pattern_build import MEMBER, read_archive, repack, word, branch

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT/'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r34.ipsw'
BASE_SHA = '44899cf29ba0dd2e6fe035b33f737c27aec5e8fd04132998934480488af78b3a'


def compile_code(directory, base):
    zig = Path(next(iter(importlib.util.find_spec('ziglang').submodule_search_locations)))/'zig'
    obj, script, elf_path = (directory/name for name in ('timer.o','timer.ld','timer_recovery.elf'))
    subprocess.run(['clang','--target=armv4t-none-eabi','-mcpu=arm7tdmi','-marm',
        '-Os','-ffreestanding','-fno-builtin','-fno-unwind-tables','-fno-asynchronous-unwind-tables',
        '-Wall','-Wextra','-Werror','-c',str(ROOT/'clock_pomodoro/timer_recovery.c'),'-o',str(obj)],check=True)
    owned = json.loads((BASE.with_suffix('.artifacts')/'pomodoro-symbols.json').read_text())
    bindings = {name:owned[name] for name in ('pomo_ui_timer','pomo_ui_visible','pomo_state')}
    script.write_text(f'SECTIONS {{ .text {base} : {{ *(.text*) *(.rodata*) . = ALIGN(4); }} '
        '.bss (NOLOAD) : { *(.bss*) *(COMMON) } .data : { *(.data*) } '
        '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }\n'+
        '\n'.join(f'{name} = {value};' for name,value in bindings.items())+
        '\nASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "no added state")')
    subprocess.run([str(zig),'ld.lld','-m','armelf','-T',str(script),'-e','pomo_timer_deliver',
                    '-o',str(elf_path),str(obj)],check=True)
    raw = elf_path.read_bytes()
    elf = ELFFile(io.BytesIO(raw))
    code = elf.get_section_by_name('.text')
    if code['sh_addr'] != base or not 0 < code['sh_size'] <= 1024:
        raise ValueError('unexpected timer overlay layout')
    symbols = {s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
               if s['st_info']['type']=='STT_FUNC' and s['st_shndx']!='SHN_UNDEF'}
    return code.data(), raw, symbols


def build(output):
    sys.path.insert(0,str(ROOT/'gapless'))
    from verify_release_package import validated_parent
    if hashlib.sha256(BASE.read_bytes()).hexdigest()!=BASE_SHA: raise ValueError('r34 source pin')
    validated_parent(BASE)
    art = output.with_suffix('.artifacts')
    if any(p.exists() or p.is_symlink() for p in (output,art)):
        raise ValueError('output exists; preserve previous evidence')
    infos,members,comment = read_archive(BASE)
    firmware = members[MEMBER]
    start,size = 512+word(firmware,0x420c),word(firmware,0x4210)
    image = firmware[start:start+size]
    with TemporaryDirectory() as temporary:
        code,raw,symbols = compile_code(Path(temporary),len(image)-4)
    candidate = bytearray(image[:-4]+code+image[-4:])
    edits=[]
    def replace(at,before,after):
        if word(image,at)!=before: raise ValueError(f'unexpected source word {at:#x}')
        struct.pack_into('<I',candidate,at,after)
        edits.append(at)
    replace(0xd94b0,0xeb000003,branch(0xd94b0,symbols['pomo_timer_deliver'],0xeb))
    replace(0xd94b4,0xe1a00004,0xe1a00000)
    replace(0xd94b8,0xebff1872,0xe1a00000)
    replace(0xe8,len(image),len(candidate))
    for at in (0x2e8,0x4a8): replace(at,0x04000000+len(image)-4,0x04000000+len(candidate)-4)
    if len(candidate)>0x390000: raise ValueError('timer overlay overlaps RAM')
    carried={p.name:p.read_bytes() for pattern in
        ('*.elf','*-symbols.json','*-layout.json','*-edits.json','diagnostics-cleanup.json')
        for p in BASE.with_suffix('.artifacts').glob(pattern)}
    carried.update({'timer_recovery.elf':raw,
        'timer-recovery-symbols.json':(json.dumps(symbols,indent=2)+'\n').encode(),
        'timer-recovery-edits.json':(json.dumps(edits)+'\n').encode()})
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
        headroom=0x390000-len(candidate),timer_code_bytes=len(code),
        added_state_bytes=0,flashed=False),indent=2)+'\n')
    return digest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(args.output)
    print('SHA-256',build(args.output))
