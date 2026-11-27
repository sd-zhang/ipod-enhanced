"""Build all five accepted navigation features; never flash or overwrite."""
import argparse
import io
import json
from pathlib import Path

from build import REPO, build
from actions_patch import install
from elftools.elf.elffile import ELFFile

SOURCE = REPO / 'build/navigation-qol-letters/iPod_4.3.1.1-gapless-navigation-letters-r10.ipsw'
STOCK = REPO / 'iPod_4.3.1.1.ipsw'


def build_candidate(source, stock, output):
    def installer(image):
        # Carry forward the earlier patch's review symbols, checked against the
        # actual source image, so its behavioral suite can test this final file.
        raw = (source.with_suffix('.artifacts') / 'letters.elf').read_bytes()
        elf = ELFFile(io.BytesIO(raw))
        text = elf.get_section_by_name('.text')
        at, size = text['sh_addr'], text['sh_size']
        if not (0x35b9ec <= at < at + size <= len(image) - 4) or image[at:at + size] != text.data():
            raise ValueError('letter review ELF differs from source firmware')
        symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
                   if s.name.startswith('letters_')}
        artifacts = output.with_suffix('.artifacts')
        result = install(image, artifacts)
        (artifacts / 'letters.elf').write_bytes(raw)
        (artifacts / 'letters-symbols.json').write_text(json.dumps(symbols, indent=2) + '\n')
        return result
    return build(source, stock, output, installer=installer)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--stock', type=Path, default=STOCK)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    digest = build_candidate(args.source, args.stock, args.output)
    print(f'{args.output}\nSHA-256 {digest}')
