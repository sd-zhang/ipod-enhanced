"""Append native-style album disc separators to pinned 4G r36."""
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
from about_version import REVISION, install as install_about_version
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'clock_pomodoro'))
from pattern_build import MEMBER, read_archive, repack, word, branch

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT/'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r36.ipsw'
BASE_SHA = 'dea731a0fd3e20180db8c375883cbe6338689bc4844080377b9ed5c096358a54'
HOOK_WORDS = {0x3cff4:0xeb015797,0x35caa0:0xebfffec5,0x4bca0:0xeb013f0e,0x4bd28:0xe5a49aac,
              0x74c0c:0xe1a00005,0x763c8:0xeb0082fd,0x9ca60:0xe594209c,
              0xbfe64:0xe28d1004,0x86700:0xea0be769}


def compile_code(directory, base):
    zig = Path(next(iter(importlib.util.find_spec('ziglang').submodule_search_locations)))/'zig'
    objects=[]
    for name in ('disc_separators.c','disc_render.c','disc_fast_scroll.c','song_scroll.c','disc_hooks.S'):
        obj=directory/(name+'.o')
        subprocess.run(['clang','--target=armv4t-none-eabi','-mcpu=arm7tdmi',
            '-mthumb' if name=='song_scroll.c' else '-marm',
            '-Oz' if name in ('song_scroll.c','disc_separators.c','disc_fast_scroll.c') else '-Os',
            '-ffreestanding','-fno-builtin','-fno-unwind-tables','-fno-asynchronous-unwind-tables',
            '-Wall','-Wextra','-Werror','-c',str(ROOT/'navigation_qol'/name),'-o',str(obj)],check=True)
        objects.append(obj)
    script=directory/'disc.ld'
    # Resolve ARM tail branches at link time. Assembly-local absolute .set
    # operands are encoded as displacements by Clang's integrated assembler.
    script.write_text('songs_reveal_top = 0x384db0; native_list_invalidate = 0x9107c;\n'
        f'SECTIONS {{ .text {base} : {{ *(.text*) *(.rodata*) . = ALIGN(4); }} '
        '.bss (NOLOAD) : { *(.bss*) *(COMMON) } .data : { *(.data*) } '
        '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }\n'
        'ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "no retained state")')
    elf_path=directory/'disc_separators.elf'
    subprocess.run([str(zig),'ld.lld','-m','armelf','-T',str(script),'-e','discs_draw_row',
                    '-o',str(elf_path),*map(str,objects)],check=True)
    raw=elf_path.read_bytes(); elf=ELFFile(io.BytesIO(raw)); code=elf.get_section_by_name('.text')
    if code['sh_addr']!=base or not 0<code['sh_size']<=6976:
        raise ValueError(f'unexpected disc overlay layout: {code["sh_size"]} bytes')
    symbols={s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
             if s['st_info']['type'] in ('STT_FUNC','STT_OBJECT') and s['st_shndx']!='SHN_UNDEF'}
    return code.data(),raw,symbols


def build(output):
    sys.path.insert(0,str(ROOT/'gapless'))
    from verify_performance_package import validated_parent
    if hashlib.sha256(BASE.read_bytes()).hexdigest()!=BASE_SHA: raise ValueError('r36 source pin')
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
    for at,target in ((0x3cff4,'songs_title_hook'),(0x35caa0,'songs_import_sort_hook'),
                      (0x4bca0,'discs_index_alloc'),(0x4bd28,'discs_publish_hook'),
                      (0x74c0c,'discs_count_hook'),
                      (0x763c8,'discs_selected_song'),
                      (0x9ca60,'discs_previous_min_hook'),
                      (0xbfe64,'discs_reveal_hook'),
                      (0x86700,'discs_fast_wheel_hook')):
        before=HOOK_WORDS[at]
        replace(at,before,branch(at,symbols[target],0xea if at in (0x4bd28,0x74c0c,0x9ca60,0xbfe64,0x86700) else 0xeb))
    for at,before,target in ((0x29160,0xd1100-0x29030,'discs_select'),
                             (0x2919c,0x59974-0x29030,'discs_draw_row'),
                             (0x29090,0x35c304-0x29030,'discs_fast_draw')):
        replace(at,before,symbols[target]-0x29030)
    replace(0x23e9c,0x66938-0x23d7c,symbols['discs_flash_song']-0x23d7c)
    replace(0x23024,0x7315c-0x22fd4,symbols['discs_model_property']-0x22fd4)
    replace(0xe8,len(image),len(candidate))
    for at in (0x2e8,0x4a8): replace(at,0x04000000+len(image)-4,0x04000000+len(candidate)-4)
    about = install_about_version(candidate)
    if len(candidate)>0x390000: raise ValueError('disc overlay overlaps RAM')
    carried={p.name:p.read_bytes() for pattern in
        ('*.elf','*-symbols.json','*-layout.json','*-edits.json','diagnostics-cleanup.json')
        for p in BASE.with_suffix('.artifacts').glob(pattern)}
    carried.update({'disc_separators.elf':raw,
        'disc-separators-symbols.json':(json.dumps(symbols,indent=2)+'\n').encode(),
        'disc-separators-edits.json':(json.dumps(edits)+'\n').encode(),
        'about-version.json':(json.dumps(about,indent=2)+'\n').encode()})
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
        headroom=0x390000-len(candidate),disc_code_bytes=len(code),
        added_state_bytes=0,index_footer_bytes=1032,about_revision=REVISION,
        flashed=False),indent=2)+'\n')
    return digest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(args.output)
    print('SHA-256',build(args.output))
