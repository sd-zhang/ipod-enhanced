import sys
from pathlib import Path
from zipfile import ZipFile
sys.path.insert(0, sys.argv[1])
from elftools.elf.elffile import ELFFile
import verify_image as vi
from image_layout import OSOS_ENTRY, SECTOR, word
build = Path(sys.argv[2])
ipsw = next(build / n for n in ('iPod_4.3.1.1-gapless.ipsw', 'iPod_4.3.1.1-gapless-diag.ipsw',
                                 'iPod_4.3.1.1-gapless-experimental.ipsw') if (build / n).exists())
fw = ZipFile(ipsw).read('Firmware-4.3.1.1')
begin = SECTOR + word(fw, OSOS_ENTRY + 12); length = word(fw, OSOS_ENTRY + 16)
osos = fw[begin:begin + length]
assert osos == (build / 'patched.osos').read_bytes()
assert sum(osos) & 0xffffffff == word(fw, OSOS_ENTRY + 28)
with (build / 'playback.elf').open('rb') as f:
    elf = ELFFile(f)
    syms = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
    size = elf.get_section_by_name('.bss')['sh_size']
import build as bd; vi.OVERLAY_HOOKS = bd.OVERLAY_HOOKS
vi.verify_runtime_layout(osos, word(fw, OSOS_ENTRY + 0x14), size, syms)
print(build.name, 'startup layout, zero-fill, checksum OK; bss', size)
