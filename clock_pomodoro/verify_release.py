"""Verify exact candidate bytes, with per-suite logs and an immutable receipt."""
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
from verify_runtime import load_image


def verify(candidate, alac_library=None, alac_outgoing=None, alac_incoming=None):
    art = candidate.with_suffix('.artifacts')
    receipt = art / 'verification.json'
    if receipt.exists():
        raise ValueError('verification receipt already exists; preserve previous evidence')
    checks = [('pomodoro-runtime', 'clock_pomodoro/verify_runtime.py', ['--candidate', candidate]),
              ('pomodoro-ui', 'clock_pomodoro/verify_ui.py', [candidate, '--native']),
              ('pomodoro-countdown-cost', 'clock_pomodoro/verify_countdown_cost.py', [candidate]),
              ('pomodoro-package', 'clock_pomodoro/verify_package.py', [candidate]),
              ('pomodoro-build', 'clock_pomodoro/test_build.py', []),
              ('pomodoro-home', 'clock_pomodoro/verify_home.py', [candidate]),
              ('pomodoro-home-flow', 'clock_pomodoro/verify_home_flow.py', [candidate]),
              ('pomodoro-home-clock', 'clock_pomodoro/verify_home_indent.py', [candidate]),
              ('fast-scroll-top', 'navigation_qol/verify_fast_scroll_top.py', [candidate])]
    checks += [(name, 'navigation_qol/verify_' + name + '.py', [candidate]) for name in (
        'otg', 'actions', 'actions_playback', 'actions_downstream', 'actions_shuffle',
        'repeat_toggle', 'sleep', 'hotpaths', 'volume', 'volume_output', 'sort_letters',
        'wheel_input', 'native', 'letters', 'letter_viewport', 'letter_performance')]
    checks += [('volume-ui', 'navigation_qol/verify_volume_ui.py', [candidate, '--native-layout']),
               ('widgets', 'navigation_qol/verify_actions_widgets.py',
                [candidate, '--with-sleep', '--stock-repeat', '--with-otg']),
               ('marquee', 'menu_scroll/verify_native.py', [candidate]),
               ('dwell', 'menu_scroll/verify_dwell.py', [candidate])]
    if (art / 'refinements.elf').exists():
        checks += [('refinement-package', 'navigation_qol/verify_optimizations.py', [candidate]),
                   ('refinement-build', 'navigation_qol/test_optimizations_build.py', []),
                   ('marker-recovery', 'navigation_qol/verify_marker_recovery.py', [candidate]),
                   ('gapless-copy', 'gapless/tests/copy_frames.py', [candidate])]
    if (art / 'alarm_light.elf').exists():
        checks += [('alarm-light', 'clock_pomodoro/verify_alarm_light.py', [candidate]),
                   ('alert-preferences', 'clock_pomodoro/verify_alert_preferences.py', [candidate]),
                   ('alert-ui', 'clock_pomodoro/verify_alert_ui.py', [candidate]),
                   ('alert-package', 'clock_pomodoro/verify_alert_package.py', [candidate]),
                   ('alert-build', 'clock_pomodoro/test_alert_build.py', [])]
    if (art / 'alarm_pattern.elf').exists():
        checks += [('alarm-pattern', 'clock_pomodoro/verify_alarm_pattern.py', [candidate]),
                   ('alarm-pattern-package', 'clock_pomodoro/verify_pattern_package.py', [candidate])]
        if 'alarm_advance_hook' in json.loads((art / 'alarm_pattern-symbols.json').read_text()):
            checks += [('alarm-cost', 'clock_pomodoro/verify_alarm_cost.py', [candidate])]
    if (art / 'lcd.elf').exists():
        checks += [('lcd-serialization', 'clock_pomodoro/verify_lcd_serialization.py', [candidate]),
                   ('lcd-package', 'clock_pomodoro/verify_lcd_package.py', [candidate, '--self-test'])]
    if (art / 'wheel_recovery.elf').exists():
        checks += [('wheel-recovery', 'navigation_qol/verify_wheel_recovery.py', [candidate]),
                   ('album-index', 'navigation_qol/verify_album_index.py', [candidate]),
                   ('wheel-package', 'navigation_qol/verify_wheel_package.py', [candidate, '--self-test'])]
    if (art / 'diagnostics-cleanup.json').exists():
        checks += [('release-diagnostics', 'gapless/tests/release_diagnostics.py', [candidate]),
                   ('release-package', 'gapless/verify_release_package.py', [candidate, '--self-test'])]
    if (art / 'timer_recovery.elf').exists():
        checks += [('timer-recovery', 'clock_pomodoro/verify_timer_recovery.py', [candidate]),
                   ('timer-package', 'clock_pomodoro/verify_timer_package.py', [candidate, '--self-test'])]
    if (art / 'stock_performance.elf').exists():
        checks += [('playlist-cache', 'navigation_qol/verify_playlist_cache.py', [candidate]),
                   ('script-globals', 'navigation_qol/verify_script_globals.py', [candidate]),
                   ('performance-package', 'navigation_qol/verify_performance_package.py', [candidate, '--self-test'])]
    if (art / 'disc_separators.elf').exists():
        checks += [('disc-separators', 'navigation_qol/verify_disc_separators.py', [candidate]),
                   ('disc-render', 'navigation_qol/verify_disc_render.py', [candidate]),
                   ('disc-package', 'navigation_qol/verify_disc_package.py', [candidate, '--self-test'])]
        if 'discs_fast_wheel' in json.loads((art / 'disc-separators-symbols.json').read_text()):
            checks += [('disc-fast-scroll', 'navigation_qol/verify_disc_fast_scroll.py', [candidate])]
        if 'songs_fast_target' in json.loads((art / 'disc-separators-symbols.json').read_text()):
            checks += [('song-scroll', 'navigation_qol/verify_song_scroll.py', [candidate])]
    if (art / 'about-version.json').exists():
        checks += [('about-version', 'navigation_qol/verify_about_version.py', [candidate])]
    if (art / 'alac.elf').exists():
        native_args = ['--candidate', candidate]
        output_args = ['--candidate', candidate]
        if alac_library is not None:
            native_args += ['--library', alac_library]
        if alac_outgoing is not None:
            boundary = art / 'alac-real-boundary'
            native_args += ['--export-boundary', boundary,
                            '--outgoing', alac_outgoing, '--incoming', alac_incoming]
            output_args += ['--replay', boundary]
        checks += [('alac-package', 'gapless/verify_alac_package.py', [candidate]),
                   ('alac-build', 'gapless/test_alac_build.py', []),
                   ('alac-native-boundaries', 'gapless/verify_alac_boundaries.py', native_args),
                   ('alac-output', 'gapless/tests/alac_output.py', output_args)]
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
        if r.returncode: print(r.stdout[-5000:], flush=True)

    for name, script, args in checks:
        run(name, script, args)
    image = load_image(candidate)
    playback = art / 'playback.elf'
    if not playback.exists(): playback = Path('build/gapless-complete-drain/playback.elf')
    section = ELFFile(io.BytesIO(playback.read_bytes())).get_section_by_name('.text')
    at, size = section['sh_addr'], section['sh_size']
    if image[at:at + size] != section.data():
        raise ValueError('gapless review ELF differs from candidate')
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
        if (art / 'refinements.elf').exists():
            run('gapless-long-position', 'gapless/tests/long_position.py',
                [root / 'stock.osos', root / 'candidate.osos', playback])
            for name in ('decoder_positions', 'empty_retirement', 'track_end'):
                extra = ['--apple'] if name == 'track_end' else []
                run('gapless-' + name, 'gapless/tests/' + name + '.py', args + extra)
            run('gapless-short-real-aac', 'gapless/repro_aac_edges.py', args + ['--apple'])
            run('gapless-dispose', 'gapless/tests/dispose.py', [root / 'candidate.osos', playback])
            run('gapless-lame', 'gapless/verify_lame_timing.py', [playback])
            run('gapless-alac', 'gapless/verify_alac.py',
                [root / 'stock.osos', root / 'candidate.osos', playback])
    receipt.write_text(json.dumps(dict(
        candidate=str(candidate), sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        osos_sha256=hashlib.sha256(image).hexdigest(), osos_bytes=len(image),
        verified_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), suites=results,
        flashed=False, hardware_tested=False,
        limitations=['Kernel scheduling, IRQ generation, UI timer-event delivery and physical wake are supplied.',
                     'LCD rasterization and audible output require hardware validation.',
                     'UI return tests supply the previous Clock/Home display tree; native history/leave dispatch executes.',
                     'Fast-scroll native end-of-list clamp keeps the last page full.',
                     'ALAC output tests supply compressed decode results at the native successful-decode tail; '
                     'separate native decoder tests compare true PCM/counts against source audio.'],
    ), indent=2) + '\n')
    return int(any(r['returncode'] for r in results))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--alac-library', type=Path)
    parser.add_argument('--alac-outgoing', type=Path)
    parser.add_argument('--alac-incoming', type=Path)
    args = parser.parse_args()
    if bool(args.alac_outgoing) != bool(args.alac_incoming):
        parser.error('--alac-outgoing and --alac-incoming must be supplied together')
    sys.exit(verify(args.candidate, args.alac_library, args.alac_outgoing, args.alac_incoming))
