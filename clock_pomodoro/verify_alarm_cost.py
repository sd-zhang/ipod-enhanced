"""Compare installed alarm work and observable state with immutable r29.

Execute native queue/backlight code; count ARM instructions, not CPU cycles or
hardware latency. Kernel scheduling and physical output use the native fixture's
documented boundaries. Unicorn requires repository Python outside the sandbox.
"""
import argparse
import hashlib
import io
import json
import unittest
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from verify_alarm_pattern import PiezoClock, load_image, PATTERN, TAG, PIEZO
from verify_alarm_light import STATE, MASK, TIMER, TIMER_HEAD, UI
from verify_pattern_package import pair_cadence
from verify_native import STACK

BASE = Path(__file__).resolve().parent.parent / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r29.ipsw'
BASE_SHA = 'e55b47b62738b2f4b448505b3e7db41cfe6e531bcc8fb537b7b5cb449ec78a41'


def measure(candidate, scenario):
    art = candidate.with_suffix('.artifacts')
    image = load_image(candidate)
    symbols = json.loads((art / 'pomodoro-symbols.json').read_text())
    m = PiezoClock(image, symbols, enabled=scenario != 'disabled_alarm')
    code = ELFFile(io.BytesIO((art / 'alarm_pattern.elf').read_bytes())).get_section_by_name('.text')
    start, end = code['sh_addr'], code['sh_addr'] + code['sh_size']
    counts = dict(instructions=0, overlay_instructions=0, stack_bytes_written=0)

    def instruction(cpu, pc, size, context):
        counts['instructions'] += 1
        if start <= pc < end:
            counts['overlay_instructions'] += 1

    def stack_write(cpu, access, address, size, value, context):
        counts['stack_bytes_written'] += size

    m.cpu.hook_add(UC_HOOK_CODE, instruction)
    m.cpu.hook_add(UC_HOOK_MEM_WRITE, stack_write, begin=STACK - 1024, end=STACK - 1)
    if scenario == 'idle':
        m.worker()
    elif scenario == 'ordinary_tones':
        for period, duration in ((180, 200000), (144, 400000), (270, 1000)):
            assert m.receive(period, duration) == 0
            m.drain()
    else:
        for period, duration in PATTERN:
            assert m.receive(period, TAG | duration) == 0
        if scenario == 'alarm_to_ordinary':
            assert m.receive(270, 1000) == 0
        elif scenario == 'cancel_lit':
            m.receive(kind=1)
        elif scenario == 'cancel_dark':
            # Both cadences are dark 575 ms into the first 600 ms pair.
            target = m.ticks + 575000
            while m.hardware_due <= target:
                m.advance()
            m.advance(target)
            m.receive(kind=1)
        m.drain()
    m.guards()
    observable = dict(events=m.events, timers=m.timer_writes, tones=m.tones(),
                      queue=bytes(m.cpu.mem_read(PIEZO, 144)).hex(),
                      light_state=bytes(m.cpu.mem_read(STATE, 6)).hex(),
                      ui_timer=bytes(m.cpu.mem_read(TIMER, 0x28)).hex(),
                      timer_head=m.read(TIMER_HEAD), preferences=m.read(MASK),
                      timeout=m.read(UI + 0x1864), flash_state=m.read(0x14534654))
    return counts, observable, code['sh_size']


SCENARIOS = ('idle', 'ordinary_tones', 'alarm', 'disabled_alarm',
             'alarm_to_ordinary', 'cancel_lit', 'cancel_dark')


class AlarmCost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert hashlib.sha256(BASE.read_bytes()).hexdigest() == BASE_SHA, 'r29 reference pin'
        cls.results = {scenario: (measure(BASE, scenario), measure(cls.candidate, scenario))
                       for scenario in SCENARIOS}
        cls.paired = pair_cadence(load_image(cls.candidate), json.loads(
            (cls.candidate.with_suffix('.artifacts') / 'alarm_pattern-symbols.json').read_text()))
        print(json.dumps({name: {'r29': old[0], 'candidate': new[0]}
                          for name, (old, new) in cls.results.items()}, indent=2), flush=True)

    def test_sound_queue_and_cleanup_match_r29(self):
        for scenario, (old, new) in self.results.items():
            with self.subTest(scenario=scenario):
                before, after = dict(old[1]), dict(new[1])
                if self.paired and scenario in ('alarm', 'alarm_to_ordinary', 'cancel_lit', 'cancel_dark'):
                    # Light edges and their extra timer wakes intentionally
                    # change. verify_alarm_pattern checks their exact new
                    # timeline; sound MMIO, queue and final policy stay exact.
                    before.pop('timers')
                    after.pop('timers')
                    if scenario != 'cancel_lit':
                        for observable in (before, after):
                            observable['events'] = [e for e in observable['events'] if e[0] == 'pitch']
                self.assertEqual(after, before)

    def test_inactive_paths_skip_most_alarm_overhead(self):
        for scenario in ('idle', 'ordinary_tones'):
            old, new = self.results[scenario]
            with self.subTest(scenario=scenario):
                self.assertLessEqual(new[0]['overlay_instructions'], old[0]['overlay_instructions'] * 0.6)
                self.assertLess(new[0]['instructions'], old[0]['instructions'])
                self.assertLess(new[0]['stack_bytes_written'], old[0]['stack_bytes_written'])

    def test_active_alarm_work_remains_bounded(self):
        for scenario in SCENARIOS[2:]:
            old, new = self.results[scenario]
            with self.subTest(scenario=scenario):
                self.assertLessEqual(new[0]['instructions'], old[0]['instructions'] * 1.05)

    def test_overlay_stays_smaller_than_original_r29(self):
        old, new = self.results['idle']
        # r31 adds pitch classification, continuous light across the pair and
        # state recovery. It still fits below the original r29 overlay budget.
        self.assertLessEqual(new[2], old[2] * (1 if self.paired else 0.9))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    AlarmCost.candidate = parser.parse_args().candidate
    unittest.main(argv=[__file__], verbosity=2)
