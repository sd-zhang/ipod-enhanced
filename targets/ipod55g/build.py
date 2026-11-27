"""Build the 5.5G OEM+ candidate from pinned stock; never access a device."""
import argparse
import hashlib
import json
import struct
from pathlib import Path

import actions
import alarm_patch
import alarm_settings
import pomodoro_patch
import pomodoro_ui
import pomodoro_home
import script_globals
import playlist_cache
import disc_patch
import disc_fast_scroll
import quick_scroll
import clicker
import about_version
from image import load_stock, repack, write_archive
from layout import link, install_elf, extend_resources, finish, TEXT, SOURCE
from scripts import merge_scripts

SOURCES = ('actions.c', 'pomodoro_runtime.c', 'pomodoro_hooks.S', 'pomodoro_ui.c',
           'alarm_flash.c', 'alarm_hooks.S', 'alarm_settings.c', 'alarm_settings_hooks.S',
           'pomodoro_home.c', 'script_globals.S', 'playlist_cache.c',
           'disc_separators.c', 'disc_hooks.S', 'disc_render.c',
           'disc_fast_scroll.c', 'disc_fast_scroll.S')


def sha(data): return hashlib.sha256(data).hexdigest()


def compose(stock, directory):
    elf = link([SOURCE / name for name in SOURCES], directory, 'pomo_boot')
    image, symbols = install_elf(stock.osos, elf)
    actions.patch(image, symbols)
    pomodoro_patch.apply(image, symbols)
    pomodoro_ui.apply(image, symbols)
    alarm_patch.install(image, symbols)
    alarm_settings.apply(image, symbols)
    pomodoro_home.apply(image, symbols)
    updates, commands = actions.changes(image)
    pomo_updates, roots = pomodoro_ui.resource_updates(image)
    rows = actions.records(updates['View'][0x5e15]) + roots
    updates['View'][0x5e15] = struct.pack('<I', len(rows)) + b''.join(rows)
    # Gather independent edits before emission: each native directory and the
    # shared root View are appended only once, with no obsolete intermediate copy.
    for additions in (pomo_updates, alarm_settings.resource_updates(image),
                      pomodoro_home.resource_updates(image), about_version.resource_updates(image)):
        for tag, entries in additions.items():
            target = updates.setdefault(tag, {})
            if target.keys() & entries.keys(): raise ValueError('resource collision')
            target.update(entries)
    for additions in (pomodoro_ui.script_updates(), pomodoro_home.script_updates(image)):
        if commands.keys() & additions.keys(): raise ValueError('script collision')
        commands.update(additions)
    updates['TCmd'] = {0x2380: merge_scripts(image, commands)}
    extend_resources(image, updates)
    script_globals.apply(image, symbols)
    playlist_cache.apply(image, symbols)
    disc_patch.apply(image, symbols)
    disc_fast_scroll.apply(image, symbols)
    quick_scroll.apply(image)
    clicker.apply(image)
    return finish(image), symbols, elf


def build(stock_path, output):
    stock = load_stock(stock_path)
    output = Path(output)
    artifacts = output.with_suffix('.artifacts')
    if output.exists() or artifacts.exists(): raise FileExistsError('candidate/evidence path already exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir()
    # Freeze everything needed for an independent rebuild, before compiling.
    frozen = artifacts / 'source' / 'targets' / 'ipod55g'
    frozen.mkdir(parents=True)
    files = {}
    for path in sorted(SOURCE.iterdir()):
        if path.is_file() and path.suffix in ('.py', '.c', '.h', '.S', '.ld', '.md'):
            data = path.read_bytes(); (frozen/path.name).write_bytes(data)
            files[f'targets/ipod55g/{path.name}'] = sha(data)
    image, symbols, elf = compose(stock, artifacts)
    firmware = repack(stock, image)
    write_archive(stock, firmware, output)
    # Record all changed original words, including the four directory headers.
    edits = [{'offset': hex(at), 'before': stock.osos[at:at+4].hex(),
              'after': image[at:at+4].hex()} for at in range(0, TEXT, 4)
             if stock.osos[at:at+4] != image[at:at+4]]
    manifest = {'target': 'iPod 5.5G stock 1.3', 'status': 'candidate, no device evidence',
                'enhanced_revision': about_version.REVISION, 'about_version': about_version.VERSION_TEXT,
                'stock_archive_sha256': sha(stock.archive), 'archive_sha256': sha(output.read_bytes()),
                'firmware_sha256': sha(firmware), 'osos_sha256': sha(image),
                'osos_size': len(image), 'logical_end': struct.unpack_from('<I', image, 0xe8)[0],
                'source_sha256': files}
    for name, data in (('build.json', manifest), ('extension-symbols.json', symbols), ('extension-edits.json', edits)):
        with (artifacts/name).open('x') as f: json.dump(data, f, indent=2, sort_keys=True); f.write('\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, default=Path('iPod_25.1.3.ipsw'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.stock, args.output), indent=2))
