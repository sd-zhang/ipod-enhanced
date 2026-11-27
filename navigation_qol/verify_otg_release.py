"""Verify an r19 archive; preserve exact-image logs and a machine-readable receipt.

Run with repository Python outside the macOS sandbox for Unicorn JIT.
"""
import argparse
import datetime
import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile
from native_fixture import load_image


def verify(candidate):
    art = candidate.with_suffix('.artifacts')
    receipt = art / 'verification.json'
    if receipt.exists():
        raise ValueError('verification receipt already exists; preserve previous evidence')
    checks = [('otg', 'navigation_qol/verify_otg.py', []),
              ('otg-package', 'navigation_qol/verify_otg_package.py', []),
              ('otg-build', 'navigation_qol/test_build_otg.py', None)]
    checks += [(name, 'navigation_qol/verify_' + name + '.py', []) for name in (
        'actions', 'actions_playback', 'actions_downstream', 'actions_shuffle',
        'repeat_toggle', 'sleep', 'hotpaths', 'volume', 'volume_output',
        'sort_letters', 'wheel_input', 'native', 'letters', 'letter_viewport', 'letter_performance')]
    checks += [('volume-ui', 'navigation_qol/verify_volume_ui.py', ['--native-layout']),
               ('widgets', 'navigation_qol/verify_actions_widgets.py',
                ['--with-sleep', '--stock-repeat', '--with-otg']),
               ('marquee', 'menu_scroll/verify_native.py', []),
               ('dwell', 'menu_scroll/verify_dwell.py', [])]
    results = []
    def run(name, script, args):
        command = [sys.executable, script, *map(str, args)]
        r = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        log = art / (name + '.log')
        log.write_text(r.stdout)
        match = re.search(r'Ran (\d+) tests?', r.stdout)
        results.append(dict(suite=name, command=command, returncode=r.returncode,
                            tests=int(match[1]) if match else None, log=log.name))
        print(name, 'PASS' if r.returncode == 0 else 'FAIL', flush=True)
        if r.returncode:
            print(r.stdout, flush=True)
    for name, script, args in checks:
        run(name, script, [] if args is None else [candidate, *args])
    image = load_image(candidate)
    playback = Path('build/gapless-complete-drain/playback.elf')
    raw_elf = playback.read_bytes()
    text = ELFFile(io.BytesIO(raw_elf)).get_section_by_name('.text')
    at, length = text['sh_addr'], text['sh_size']
    if image[at:at + length] != text.data():
        raise ValueError('gapless test ELF differs from candidate')
    stock = load_image(Path('iPod_4.3.1.1.ipsw'))
    with ZipFile('iPod_25.1.3.ipsw') as z:
        reference = z.read('Firmware-25.6.3')[0x5000:0x73b000]
    if hashlib.sha256(reference).hexdigest() != '784ae3d5540fd2f89e8c947b93629e97f62a06dc311d9745aab143e4db6bb251':
        raise ValueError('unexpected reference decoder')
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, data in (('stock', stock), ('candidate', image), ('reference', reference)):
            (root / (name + '.osos')).write_bytes(data)
        args = ['gapless', root / 'stock.osos', root / 'candidate.osos', playback]
        for name in ('cold_start', 'aac_transitions', 'mp3_transitions'):
            extra = [root / 'reference.osos'] if name == 'mp3_transitions' else []
            run('gapless-' + name, 'gapless/tests/' + name + '.py', args + extra)
    data = dict(candidate=str(candidate), sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
                osos_sha256=hashlib.sha256(image).hexdigest(), osos_bytes=len(image),
                image_region_headroom=0x390000 - len(image),
                verified_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                suites=results, flashed=False, hardware_tested=False,
                playback_elf_sha256=hashlib.sha256(raw_elf).hexdigest(),
                limitations=['Native ARM/VM fixtures supply storage, allocation, final UI refresh, codec teardown, LCD and OS services.',
                             'Actual device input delivery, disk persistence and listening remain hardware validation.'])
    receipt.write_text(json.dumps(data, indent=2) + '\n')
    return int(any(r['returncode'] for r in results))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    sys.exit(verify(parser.parse_args().candidate))
