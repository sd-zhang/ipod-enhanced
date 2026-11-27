"""Normal playback must not maintain the disabled trace counter.

Run the actual packaged start/publish/output paths with compressed decode and
scheduling supplied, and require exact PCM through a retained track boundary.
The memory observer does not replace a production instruction or its outcome.
"""
import argparse
import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_PC
import alac_output
import build as playback_build
from verify_tx import Machine, ENGINE

COUNTER = 0x1452f0e8


class ReleaseDiagnostics(unittest.TestCase):
    def test_explicit_diagnostic_build_still_records_start_and_trace_budget(self):
        zig = Path(next(iter(importlib.util.find_spec('ziglang').submodule_search_locations))) / 'zig'
        with TemporaryDirectory(prefix='diagnostic-source-check-') as temporary:
            elf = playback_build.link_code(Path(temporary), 'clang', [str(zig), 'ld.lld'], diag=True)
            with elf.open('rb') as binary:
                m = Machine(self.stock, binary, 0x21eb4)
            m.call('gapless_trim_start', ENGINE, 0)
            self.assertEqual(m.read(m.symbols['gapless_diag']), 1)
            self.assertEqual(m.read(m.symbols['producer.12']), 12)

    def test_playback_does_not_access_disabled_trace_counter(self):
        for frontend in (0x21eb4, 0x21f3c):
            with self.subTest(frontend=hex(frontend)):
                accesses = []

                class ObservedMachine(alac_output.OutputMachine):
                    def __init__(self, *args):
                        super().__init__(*args)
                        self.cpu.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE,
                            lambda cpu, kind, at, size, value, unused:
                                accesses.append((hex(cpu.reg_read(UC_ARM_REG_PC)), kind)),
                            begin=COUNTER, end=COUNTER + 3)

                tracks = [alac_output.numbered(n, 0x100001 + i * 0x100000)
                          for i, n in enumerate((17 * 4096 + 137, 4096 + 220))]
                with patch.object(alac_output, 'OutputMachine', ObservedMachine):
                    alac_output.run_case(self.stock, self.installed, self.elf,
                        tracks, frontend, True, retention_limit=4)
                self.assertEqual(accesses, [],
                    'release playback still maintains a diagnostic-only counter')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    ReleaseDiagnostics.stock = alac_output.image(Path('iPod_4.3.1.1.ipsw'))
    ReleaseDiagnostics.installed = alac_output.image(args.candidate)
    ReleaseDiagnostics.elf = args.candidate.with_suffix('.artifacts') / 'playback.elf'
    unittest.main(argv=[__file__], verbosity=2)
