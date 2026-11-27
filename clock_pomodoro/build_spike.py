"""Compile only the experimental ARM interval helper, never an IPSW."""
import argparse
import importlib.util
import json
import subprocess
from pathlib import Path
from elftools.elf.elffile import ELFFile

SOURCE = Path(__file__).resolve().parent
CODE = 0x10500000  # emulator-owned mapping; no firmware allocation claim


def build(out):
    out.mkdir(parents=True, exist_ok=True)
    obj, elf, link = (out / name for name in ('interval.o', 'interval.elf', 'interval.ld'))
    subprocess.run(['clang', '--target=armv4t-none-eabi', '-mcpu=arm7tdmi', '-marm',
                    '-Os', '-ffreestanding', '-fno-builtin', '-fno-unwind-tables',
                    '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra', '-Werror',
                    '-c', str(SOURCE / 'interval.c'), '-o', str(obj)], check=True)
    spec = importlib.util.find_spec('ziglang')
    if spec is None:
        raise RuntimeError('repository .venv must contain ziglang')
    zig = Path(next(iter(spec.submodule_search_locations))) / 'zig'
    link.write_text(f'SECTIONS {{ . = {CODE}; .text : {{ *(.text*) *(.rodata*) }} '
                    '.data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } '
                    '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }')
    subprocess.run([str(zig), 'ld.lld', '-m', 'armelf', '-T', str(link), '-e',
                    'pomo_reset', '-o', str(elf), str(obj)], check=True)
    with elf.open('rb') as stream:
        f = ELFFile(stream)
        assert not any(s['sh_size'] for s in f.iter_sections() if s.name in ('.data', '.bss'))
        symbols = {s.name: s['st_value'] for s in f.get_section_by_name('.symtab').iter_symbols()
                   if s.name.startswith('pomo_')}
        binary = f.get_section_by_name('.text').data()
    (out / 'interval.bin').write_bytes(binary)
    (out / 'symbols.json').write_text(json.dumps(symbols, indent=2) + '\n')
    print(f'Host-only prototype: {len(binary)} ARM bytes; 32-byte caller-owned state')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=SOURCE.parent / 'build/research/pomodoro-spike')
    build(p.parse_args().output)
