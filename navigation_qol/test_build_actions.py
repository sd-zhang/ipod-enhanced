"""Check rebuild reproducibility and preservation of reviewable artifacts."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from build_actions import build_candidate, SOURCE, STOCK
from actions_patch import install, extend_script, functions
from native_fixture import load_image
from verify_resources import directory


class PreserveActionBuilds(unittest.TestCase):
    def test_script_metadata_after_bytecode_is_halfword_aligned(self):
        original = directory(load_image(SOURCE))['TCmd'][1][0x2380]
        for label, blob in (('source', original), ('rebuilt', extend_script(original))):
            for atom, _, _, _, code in functions(blob)[-1]:
                # Native loader 68efc/68f00 adds the declared bytecode length
                # then LDRH-loads the extension count. ARM7 requires alignment.
                with self.subTest(image=label, command=atom):
                    self.assertEqual(len(code) % 2, 0, 'unaligned native LDRH metadata read')

    def test_reproducible_and_existing_output_is_preserved(self):
        with TemporaryDirectory() as temporary:
            first, second = (Path(temporary) / name for name in ('a.ipsw', 'b.ipsw'))
            digest = build_candidate(SOURCE, STOCK, first)
            original = first.read_bytes()
            self.assertEqual(build_candidate(SOURCE, STOCK, second), digest)
            with self.assertRaisesRegex(ValueError, 'output already exists'):
                build_candidate(SOURCE, STOCK, first)
            self.assertEqual(first.read_bytes(), original)
            for path in first.with_suffix('.artifacts').iterdir():
                self.assertEqual(path.read_bytes(), (second.with_suffix('.artifacts') / path.name).read_bytes())

    def test_existing_sidecar_is_preserved(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / 'candidate.ipsw'
            directory = output.with_suffix('.artifacts')
            directory.mkdir()
            marker = directory / 'actions.elf'
            marker.write_bytes(b'previous review')
            with self.assertRaisesRegex(ValueError, 'artifacts already exist'):
                build_candidate(SOURCE, STOCK, output)
            self.assertEqual(marker.read_bytes(), b'previous review')
            self.assertFalse(output.exists())

    def test_unrecognized_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'expected pinned'):
            install(b'unknown image')


if __name__ == '__main__':
    unittest.main(verbosity=2)
