"""Validate r34's exact in-place cleanup before unwrapping pinned r33."""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'clock_pomodoro'))
sys.path.insert(0, str(ROOT / 'navigation_qol'))
from verify_pattern_package import (MEMBER, ZIP_FIELDS, ARTIFACT_PATTERNS,
    read_archive, payload, repacked_stock, artifacts)

BASE = ROOT / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r33.ipsw'
BASE_SHA = '43cdae25c131d2acddc1cb6db4746ea1e4ddc7192510225c435c96a8d9ab2f9f'
# Independent expected ARM words: two NOPs at initialization, NOTE_TRIM plus
# B to the existing position update, and three unreachable NOPs.
WORDS = {0x38ca6c:0xe1a00000, 0x38ca78:0xe1a00000,
         0x38d480:0xe38cc004, 0x38d484:0xea000002,
         0x38d488:0xe1a00000, 0x38d48c:0xe1a00000, 0x38d490:0xe1a00000}


def validated_parent(candidate):
    from verify_wheel_package import validated_parent as validate_wheel
    candidate = Path(candidate)
    if (candidate.with_suffix('.artifacts')/'timer_recovery.elf').exists():
        from verify_timer_package import validated_parent as validate_timer
        return validated_parent(validate_timer(candidate))
    assert hashlib.sha256(BASE.read_bytes()).hexdigest() == BASE_SHA, 'r33 source pin'
    validate_wheel(BASE)
    old_infos, old, old_comment = read_archive(BASE)
    infos, new, comment = read_archive(candidate)
    assert list(old) == list(new) and comment == old_comment, 'ZIP members/comment'
    for left, right in zip(old_infos, infos):
        assert all(getattr(left, f) == getattr(right, f) for f in ZIP_FIELDS), 'ZIP metadata'
    assert all(old[n] == new[n] for n in old if n != MEMBER), 'unrelated ZIP member'
    before, after = payload(old[MEMBER]), payload(new[MEMBER])
    expected = bytearray(before)
    for at, value in WORDS.items():
        struct.pack_into('<I', expected, at, value)
    assert after == expected, 'exact cleanup instruction changes'
    stock = read_archive(ROOT / 'iPod_4.3.1.1.ipsw')[1][MEMBER]
    assert new[MEMBER] == repacked_stock(stock, after), 'stock repack/checksum'
    assert payload(old[MEMBER], 0x4228) == payload(new[MEMBER], 0x4228), 'updater unchanged'
    old_art = artifacts(BASE.with_suffix('.artifacts'))
    art = candidate.with_suffix('.artifacts')
    new_art = artifacts(art)
    assert set(new_art) == set(old_art), 'artifact set'
    elf = ELFFile(io.BytesIO(old_art['alac.elf']))
    code = elf.get_section_by_name('.text')
    base, count, offset = code['sh_addr'], code['sh_size'], code['sh_offset']
    assert before[base:base + count] == code.data(), 'baseline ALAC ELF'
    expected_elf = bytearray(old_art['alac.elf'])
    for at, value in WORDS.items():
        assert base <= at < base + count, 'cleanup within existing code'
        struct.pack_into('<I', expected_elf, offset + at - base, value)
    for name, raw in old_art.items():
        assert new_art[name] == (expected_elf if name == 'alac.elf' else raw), ('carried ELF/metadata', name)
    assert ELFFile(io.BytesIO(new_art['alac.elf'])).get_section_by_name('.text').data() == after[base:base+count], 'ALAC image correspondence'
    assert json.loads((art/'diagnostics-cleanup.json').read_text()) == dict(
        source_sha256=BASE_SHA, edits=list(WORDS), added_code_bytes=0, added_state_bytes=0), 'cleanup metadata'
    assert json.loads((art/'build.json').read_text()) == dict(source_sha256=BASE_SHA,
        sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        osos_sha256=hashlib.sha256(after).hexdigest(), osos_bytes=len(after),
        headroom=0x390000-len(after), added_code_bytes=0, added_state_bytes=0,
        flashed=False), 'build metadata'
    return BASE


class CorruptionChecks(unittest.TestCase):
    def test_rejects_instruction_and_preservation_corruption(self):
        infos, members, comment = read_archive(self.candidate)
        original = payload(members[MEMBER])
        art = self.candidate.with_suffix('.artifacts')
        code = ELFFile(io.BytesIO((art/'alac.elf').read_bytes())).get_section_by_name('.text')
        stock = read_archive(ROOT/'iPod_4.3.1.1.ipsw')[1][MEMBER]
        cases = [(hex(at), at, 'exact cleanup instruction changes') for at in WORDS]
        cases += [('state/layout', 0x4b0, 'exact cleanup instruction changes'),
                  ('LCD', 0xc3f7c, 'exact cleanup instruction changes'),
                  ('wheel', 0xb13d8, 'exact cleanup instruction changes'),
                  ('matching ELF', 0x38d484, 'exact cleanup instruction changes'),
                  ('carried ELF', None, 'carried ELF/metadata')]
        with TemporaryDirectory(prefix='release-cleanup-negative-') as temporary:
            for i, (name, at, failure) in enumerate(cases):
                with self.subTest(name=name):
                    path = Path(temporary)/f'bad-{i}.ipsw'
                    target = path.with_suffix('.artifacts')
                    target.mkdir()
                    for pattern in (*ARTIFACT_PATTERNS, 'build.json', 'diagnostics-cleanup.json'):
                        for source in art.glob(pattern): shutil.copyfile(source, target/source.name)
                    image = bytearray(original)
                    if at is not None: image[at] ^= 1
                    if name == 'matching ELF':
                        raw = bytearray((target/'alac.elf').read_bytes())
                        raw[code['sh_offset'] + at - code['sh_addr']] ^= 1
                        (target/'alac.elf').write_bytes(raw)
                    if name == 'carried ELF':
                        raw = bytearray((target/'lcd.elf').read_bytes())
                        raw[-1] ^= 1
                        (target/'lcd.elf').write_bytes(raw)
                    changed = dict(members)
                    changed[MEMBER] = repacked_stock(stock, image)
                    with ZipFile(path, 'w') as archive:
                        archive.comment = comment
                        for info in infos: archive.writestr(copy.copy(info), changed[info.filename])
                    build = json.loads((target/'build.json').read_text())
                    build.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                 osos_sha256=hashlib.sha256(image).hexdigest())
                    (target/'build.json').write_text(json.dumps(build))
                    with self.assertRaisesRegex(AssertionError, failure): validated_parent(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    validated_parent(args.candidate)
    print('Release cleanup: seven instruction words, unchanged addresses/state and all unrelated bytes: PASS')
    if args.self_test:
        target = args.candidate
        if (target.with_suffix('.artifacts')/'timer_recovery.elf').exists():
            from verify_timer_package import validated_parent as validate_timer
            target = validate_timer(target)
        CorruptionChecks.candidate = target
        unittest.main(argv=[__file__], verbosity=2)
