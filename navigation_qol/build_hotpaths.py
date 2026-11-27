"""Build the two reviewed optimizations over r15; never flash or overwrite."""
import argparse
import io
import json
from pathlib import Path

from elftools.elf.elffile import ELFFile
from build import REPO, build
from hotpaths_patch import install

SOURCE = REPO / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r15.ipsw'


def build_candidate(source, stock, output):
    def installer(image):
        carried = {}
        for name in ('letters', 'actions', 'volume'):
            raw = (source.with_suffix('.artifacts') / (name + '.elf')).read_bytes()
            elf = ELFFile(io.BytesIO(raw))
            section = elf.get_section_by_name('.text')
            at, size = section['sh_addr'], section['sh_size']
            if not (0x35b9ec <= at < at + size <= len(image) - 4) or image[at:at + size] != section.data():
                raise ValueError(f'{name} review ELF differs from source firmware')
            symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
                       if s.name.startswith(name + '_')}
            carried[name] = raw, symbols
        artifacts = output.with_suffix('.artifacts')
        result = install(image, artifacts)
        for name, (raw, symbols) in carried.items():
            (artifacts / (name + '.elf')).write_bytes(raw)
            (artifacts / (name + '-symbols.json')).write_text(json.dumps(symbols, indent=2) + '\n')
        return result
    return build(source, stock, output, installer=installer)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--stock', type=Path, default=REPO / 'iPod_4.3.1.1.ipsw')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(f'{args.output}\nSHA-256 {build_candidate(args.source, args.stock, args.output)}')
