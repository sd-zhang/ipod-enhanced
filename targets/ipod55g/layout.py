"""Model-specific extension layout and resource relocation, never a flasher."""
from pathlib import Path
import struct
import subprocess

from elftools.elf.elffile import ELFFile
import ziglang

from image import ROOT, SECTOR, resources, word

TEXT = 0x7358c0
MARKER = bytes.fromhex('55aa55aa')
SOURCE = Path(__file__).resolve().parent


def link(sources: list[Path], directory: Path, entry='next_value') -> Path:
    objects=[]
    for index,source in enumerate(sources):
        obj=directory/f'{index}-{source.stem}.o'
        subprocess.run(['clang','--target=armv4t-none-eabi','-mcpu=arm7tdmi',
                        '-marm','-O2','-ffreestanding','-fno-builtin',
                        '-fno-unwind-tables','-fno-asynchronous-unwind-tables',
                        '-Wall','-Wextra','-Werror','-I',str(SOURCE),
                        '-c',str(source),'-o',str(obj)],check=True,capture_output=True)
        objects.append(obj)
    elf=directory/'extension.elf'
    linker=Path(ziglang.__file__).parent/'zig'
    subprocess.run([str(linker),'ld.lld','-m','armelf','-T',str(SOURCE/'extension.ld'),
                    '-e',entry,'-o',str(elf),*[str(o) for o in objects]],
                   check=True,capture_output=True)
    return elf


def install_elf(stock: bytes, path: Path) -> tuple[bytearray,dict[str,int]]:
    if word(stock,0xe8)!=TEXT+4 or stock[TEXT:TEXT+4]!=MARKER:
        raise ValueError('unexpected stock logical endpoint')
    with path.open('rb') as stream:
        elf=ELFFile(stream)
        if elf['e_machine']!='EM_ARM':raise ValueError('expected ARM ELF')
        symbols={s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
        end=symbols['__extension_end']
        if not TEXT<end<0x800000:raise ValueError('extension overlaps native data')
        result=bytearray(end)
        result[:TEXT]=stock[:TEXT]
        spans=[]
        for section in elf.iter_sections():
            if not section['sh_flags']&2 or not section['sh_size']:continue
            address=section['sh_addr'];size=section['sh_size']
            writable=bool(section['sh_flags']&1)
            offset=address-0x10000000 if writable else address
            if not TEXT<=offset<offset+size<=end:raise ValueError('bad ELF placement')
            if any(offset<stop and offset+size>start for start,stop in spans):
                raise ValueError('overlapping ELF sections')
            spans.append((offset,offset+size))
            if section['sh_type']!='SHT_NOBITS':result[offset:offset+size]=section.data()
    return result,symbols


def extend_resources(image: bytearray, changes: dict[str,dict[int,bytes]]):
    """Append changed payloads and complete replacement directories.

    Originals remain byte-for-byte in place. Native lookup uses the root-relative
    directory offsets; no assumed resource arena or deleted historical payload.
    Duplicate BMap lookup is unresolved and that section cannot be edited.
    """
    groups=resources(image)
    base=ROOT+word(image,ROOT+4)
    for tag,updates in changes.items():
        if tag=='BMap':raise ValueError('BMap duplicate lookup must be resolved before edits')
        if tag not in groups:raise ValueError('unknown resource section')
        rows=dict(groups[tag])
        for key,payload in updates.items():
            image.extend(bytes(-len(image)%4))
            rows[key]=(len(image),len(payload));image.extend(payload)
        image.extend(bytes(-len(image)%4))
        directory=len(image)
        for key,(at,size) in sorted(rows.items()):image.extend(struct.pack('<III',key,at-base,size))
        for i in range(8):
            header=ROOT+12+16*i
            if image[header:header+4]==tag[::-1].encode('ascii'):
                struct.pack_into('<I',image,header+4,len(rows))
                struct.pack_into('<I',image,header+12,directory-ROOT)
                break


def finish(image: bytearray) -> bytes:
    """Keep the loaded extension below the native arena/data at 0x10800000."""
    image=bytearray(image)
    image.extend(bytes(-len(image)%4))
    end=len(image)+4
    if not TEXT+4<=end<=0x800000:
        raise ValueError('extension overlaps native data')
    if word(image,0xe8)!=TEXT+4 or any(word(image,p)!=TEXT+0x04000000 for p in (0x308,0xdec)):
        raise ValueError('startup endpoint changed before finalization')
    image.extend(MARKER)
    struct.pack_into('<I',image,0xe8,end)
    for offset in (0x308,0xdec):struct.pack_into('<I',image,offset,0x04000000+end-4)
    image.extend(bytes(-len(image)%SECTOR))
    return bytes(image)
