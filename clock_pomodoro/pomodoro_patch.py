"""Install the owned native Pomodoro runtime over pinned r19.

The final package builder adds the Clock resources and preserves the source
archive. This module never accesses a device or chooses a flash target.
"""
import hashlib
import importlib.util
import json
import struct
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from elftools.elf.elffile import ELFFile
from rtc_spike import R19_SHA256, TABLE_LITERAL_SITES
from resources import install_resources

SOURCE = Path(__file__).resolve().parent
BSS_BASE = 0x14534294
ZERO_SIZE = 0xcd384


def compile_code(path, base):
    spec = importlib.util.find_spec('ziglang')
    if not spec or not spec.submodule_search_locations:
        raise ValueError('ziglang is required in the repository environment')
    zig = Path(next(iter(spec.submodule_search_locations))) / 'zig'
    objects = []
    for name in ('runtime.c', 'ui.c', 'fast_scroll.c', 'hooks.S', 'home_shortcuts.S'):
        obj = path / (Path(name).stem + '.o')
        subprocess.run(['clang', '--target=armv4t-none-eabi', '-mcpu=arm7tdmi', '-marm',
                        '-Os', '-ffreestanding', '-fno-builtin', '-fno-unwind-tables',
                        '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra', '-Werror',
                        '-c', str(SOURCE / name), '-o', str(obj)], check=True)
        objects.append(str(obj))
    elf, script = path / 'pomodoro.elf', path / 'pomodoro.ld'
    script.write_text(f'SECTIONS {{ .text {base} : {{ *(.text*) *(.rodata*) . = ALIGN(4); }} '
                      f'.bss {BSS_BASE} (NOLOAD) : {{ *(.bss*) *(COMMON) . = ALIGN(4); }} '
                      '.data : { *(.data*) } '
                      '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } } '
                      'ASSERT(SIZEOF(.data) == 0, "initialized data needs startup copy")')
    subprocess.run([str(zig), 'ld.lld', '-m', 'armelf', '-T', str(script), '-e',
                    'pomo_boot', '-o', str(elf), *objects], check=True)
    with elf.open('rb') as stream:
        file = ELFFile(stream)
        code, state = file.get_section_by_name('.text'), file.get_section_by_name('.bss')
        assert code['sh_addr'] == base and state['sh_addr'] == BSS_BASE
        assert state['sh_type'] == 'SHT_NOBITS' and state['sh_size'] % 4 == 0
        symbols = {s.name: s['st_value'] for s in file.get_section_by_name('.symtab').iter_symbols()
                   if s.name.startswith('pomo_')}
        return code.data(), state['sh_size'], symbols, elf.read_bytes()


def install(image, artifacts=None):
    if hashlib.sha256(image).hexdigest() != R19_SHA256:
        raise ValueError('expected pinned r19 navigation OSOS')
    if artifacts is not None and (artifacts.exists() or artifacts.is_symlink()):
        raise ValueError('review artifacts already exist; choose a new path')
    if image[-4:] != bytes.fromhex('55aa55aa') or len(image) % 4:
        raise ValueError('unexpected image terminator')
    output = bytearray(image[:-4])
    with TemporaryDirectory() as temporary:
        code, bss_size, symbols, elf = compile_code(Path(temporary), len(output))
    output.extend(code)
    edits = []

    def replace(at, expected, value):
        if struct.unpack_from('<I', image, at)[0] != expected:
            raise ValueError(f'unexpected word at {at:#x}')
        struct.pack_into('<I', output, at, value)
        edits.append(at)

    for site in TABLE_LITERAL_SITES:
        replace(site, 0x103c98e4, symbols['pomo_alarms'])
    replace(0x2e894, 0xe3570005, 0xe3570006)
    replace(0x8cf28, 0xe3500005, 0xe3500006)
    for at, expected, symbol, link in (
        (0x8cf34, 0xe3a00f49, 'pomo_boot_hook', True),
        (0x159ad0, 0xeaffffba, 'pomo_background_hook', False),
        (0xcd118, 0xe92d4030, 'pomo_set_clock', False),
        (0xe21b8, 0xe92d40fe, 'pomo_set_zone', False),
        (0x91418, 0xeb002eb9, 'pomo_dispatch', True),
        (0x3802b4, 0xe12fff11, 'pomo_fast_scroll_top', True),
        (0x6b1d8, 0xea00003f, 'pomo_home_mask', False),
        (0x6b224, 0xea00002c, 'pomo_home_alarm_mask', False),
        (0x6b228, 0xea00002b, 'pomo_home_sleep_mask', False),
        (0x6b22c, 0xea00002a, 'pomo_home_date_mask', False),
    ):
        displacement = symbols[symbol] - at - 8
        if displacement % 4 or not -(1 << 25) <= displacement < (1 << 25):
            raise ValueError('unreachable ARM hook')
        replace(at, expected, (0xeb000000 if link else 0xea000000) |
                ((displacement >> 2) & 0xffffff))
    # Vacant 25f4 cases now use the existing native boolean preference paths.
    for at, expected, target in ((0x73348, 0xea0002b4, 0x73d8c),
                                 (0xcc1c4, 0xea000304, 0xccd74),
                                 (0xcc228, 0xea0002eb, 0xccd74),
                                 (0xcc22c, 0xea0002ea, 0xccd74),
                                 (0xcc230, 0xea0002e9, 0xccd74)):
        replace(at, expected, 0xea000000 | ((target - at - 8) >> 2))
    # Preserve the native EQ path for property 2784; only the NE default
    # branch inspects the three newly assigned preference keys.
    displacement = symbols['pomo_home_getter'] - 0x733d0 - 8
    if displacement % 4 or not -(1 << 25) <= displacement < (1 << 25):
        raise ValueError('unreachable Clock shortcut getter')
    replace(0x733d0, 0x1a000292, 0x1a000000 | ((displacement >> 2) & 0xffffff))
    # The factory at 0xf2c04 replaces the base Renderer table with this
    # iPod subclass. Hook the table hardware actually uses; its property
    # fallback must retain the subclass's title and font-style properties.
    replace(0x256c0, 0x4f144, symbols['pomo_property'] - 0x25670)
    replace(0x25694, 0x5b200, symbols['pomo_ui_event'] - 0x25670)
    replace(0x4b0, BSS_BASE, BSS_BASE + bss_size)
    replace(0x20428, ZERO_SIZE, ZERO_SIZE + bss_size)
    resource_edits, script_range = install_resources(image, output)
    edits += resource_edits
    output.extend(bytes.fromhex('55aa55aa'))
    replace(0xe8, len(image), len(output))
    for at in (0x2e8, 0x4a8):
        replace(at, 0x04000000 + len(image) - 4, 0x04000000 + len(output) - 4)
    if len(output) > 0x390000:
        raise ValueError('Pomodoro overlaps initialized RAM')
    if artifacts is not None:
        artifacts.mkdir(parents=True, exist_ok=False)
        (artifacts / 'pomodoro.elf').write_bytes(elf)
        (artifacts / 'pomodoro-symbols.json').write_text(json.dumps(symbols, indent=2) + '\n')
        (artifacts / 'pomodoro-edits.json').write_text(json.dumps(edits) + '\n')
        (artifacts / 'pomodoro-layout.json').write_text(json.dumps({
            'script_range': script_range, 'bss_base': BSS_BASE,
            'bss_bytes': bss_size, 'code_bytes': len(code)}, indent=2) + '\n')
    return bytes(output)
