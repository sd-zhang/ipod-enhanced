"""Run native suites against the exact packaged image; immutable result/logs.

Kernel, disk and hardware boundaries remain supplied. A pass is host evidence,
not a device boot, rendering or timing report. Requires Unicorn JIT permission.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from zipfile import ZipFile

from image import load_stock, resources, word, MEMBER
from verify_package import verify


def about_test_cases(package):
    """Only patched images promise the Enhanced About resource contract."""
    from test_about_version import AboutVersion
    return (AboutVersion,) if package['about_version'] is not None else ()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--stock', type=Path, default=Path('iPod_25.1.3.ipsw'))
    args = parser.parse_args()
    candidate = args.candidate.resolve(); artifacts = candidate.with_suffix('.artifacts')
    receipt = artifacts/'verification.json'
    # Claim evidence destination before work; retain failed/running receipt.
    with receipt.open('x') as stream: json.dump({'status': 'running'}, stream)
    results = []
    try:
        package = verify(candidate, args.stock)
        symbols = json.loads((artifacts/'extension-symbols.json').read_text())
        stock = load_stock(args.stock).osos
        with ZipFile(candidate) as archive: firmware = archive.read(MEMBER)
        image = firmware[0x5000:0x5000+word(firmware, 0x4210)]
        import actions
        import pomodoro_home
        from test_actions_native import ActionsNative
        from test_actions_query_native import ActionsQueryNative
        from test_alarm_settings_native import AlarmSettings
        from test_pomodoro_native import PomodoroRuntime
        from test_pomodoro_ui import PomodoroUI
        from test_pomodoro_home import PomodoroHome
        from test_script_globals import ScriptGlobals
        from test_playlist_cache_native import PlaylistCache
        from test_disc_native import DiscSeparatorsNative
        from test_disc_render_native import DiscRenderNative
        from test_disc_fast_scroll_native import DiscFastScrollNative
        from test_disc_popup_native import DiscPopupNative
        from test_quick_scroll_native import QuickScrollNative
        from test_clicker_native import ClickerNative
        from test_clicker_audio_native import ClickerAudioNative
        from test_about_version import AboutManifest
        suite = unittest.TestSuite()
        for base in (ActionsNative, ActionsQueryNative, AlarmSettings, PomodoroRuntime, PomodoroUI, PomodoroHome,
                     ScriptGlobals, PlaylistCache, DiscSeparatorsNative, DiscRenderNative,
                     DiscFastScrollNative, DiscPopupNative, QuickScrollNative,
                     ClickerNative, ClickerAudioNative, *about_test_cases(package)):
            def setup(cls):
                cls.image, cls.stock, cls.symbols = image, stock, symbols
                updates, cls.scripts = actions.changes(stock)
                cls.scripts.update(pomodoro_home.script_updates(stock))
                groups = resources(image)
                cls.updates = {tag: {key: image[groups[tag][key][0]:sum(groups[tag][key])]
                                     for key in rows} for tag, rows in updates.items()}
            packaged = type('Packaged'+base.__name__, (base,),
                            {'setUpClass': classmethod(setup), 'tearDownClass': classmethod(lambda cls: None)})
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(packaged))
        with (artifacts/'native-features.txt').open('x') as log:
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        results.append({'suite': 'packaged-native-features', 'tests': result.testsRun,
                        'failures': len(result.failures), 'errors': len(result.errors), 'passed': result.wasSuccessful()})
        # Alarm has a separate MMIO/task fixture, but loads the same package.
        with (artifacts/'native-alarm.txt').open('x') as log:
            run = subprocess.run([sys.executable, str(Path(__file__).with_name('test_alarm_native.py')),
                                  '--candidate', str(candidate), '--output', str(artifacts/'alarm-evidence')],
                                 stdout=log, stderr=subprocess.STDOUT)
        alarm_result = artifacts/'alarm-evidence/receipt.json'
        alarm_counts = json.loads(alarm_result.read_text()) if alarm_result.exists() else {}
        results.append({'suite': 'packaged-native-alarm', 'tests': alarm_counts.get('tests', 0),
                        'passed': run.returncode == 0})
        from verify_layout_native import Machine
        old, new = Machine(stock), Machine(image)
        assert old.arena() == new.arena(), 'native heap changed'
        assert new.probes == [(0x4000000, word(image, 0xe8)-4)], 'native loaded extent'
        results.append({'suite': 'packaged-native-memory-arena', 'tests': 1, 'passed': True})
        from test_image import StockPackageTests
        from test_layout import ExtensionTests
        from test_scripts import ScriptMergeTests
        from test_package import PackageAudit
        tooling = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case)
                                     for case in (StockPackageTests, ExtensionTests, ScriptMergeTests, PackageAudit, AboutManifest))
        with (artifacts/'package-tooling.txt').open('x') as log:
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(tooling)
        results.append({'suite': 'package-layout-script-tooling', 'tests': result.testsRun,
                        'failures': len(result.failures), 'errors': len(result.errors), 'passed': result.wasSuccessful()})
        status = {'passed': all(r['passed'] for r in results), 'package': package, 'suites': results,
                  'archive_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
                  'verification_source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                 for p in sorted(Path(__file__).parent.glob('*.py'))},
                  'evidence': 'native ARM and supplied OS/MMIO boundaries; no device observation'}
    except BaseException as error:
        status = {'passed': False, 'suites': results, 'error': repr(error)}
    # Finalize this run once. A new process always refuses this existing path.
    receipt.write_text(json.dumps(status, indent=2)+'\n')
    print(json.dumps(status, indent=2))
    if not status['passed']: raise SystemExit(1)


if __name__ == '__main__': main()
