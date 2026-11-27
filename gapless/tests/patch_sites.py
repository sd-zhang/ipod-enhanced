"""List the stock OSOS words a patched image changes (outside the appended
extension) and the symbol each branch or literal targets."""
import struct, sys
from elftools.elf.elffile import ELFFile
stock = open(sys.argv[1], 'rb').read(); patched = open(sys.argv[2], 'rb').read()
with open(sys.argv[3], 'rb') as f:
    syms = {s['st_value']: s.name for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols()
            if s.name and s['st_info']['type'] in ('STT_FUNC', 'STT_NOTYPE', 'STT_OBJECT')}
end = 0x356f10
out = []
for a in range(0, end, 4):
    if stock[a:a+4] != patched[a:a+4]:
        w = struct.unpack_from('<I', patched, a)[0]
        target = None
        if (w >> 25) & 7 == 5:                      # B/BL
            off = ((w & 0xffffff) ^ 0x800000) - 0x800000
            target = (a + 8 + 4 * off) & 0xffffffff
        elif w in syms or (w | 0x10000000) in syms:
            target = w
        name = syms.get(target) or syms.get((target or 0) | 0x10000000) or syms.get((target or 0) & 0x0fffffff)
        out.append(f'{a:06x} {name or hex(w)}')
print('\n'.join(out))
