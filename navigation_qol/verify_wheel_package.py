"""Validate wheel recovery and Albums fast path, preserving all carried r32 bytes."""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'clock_pomodoro'))
from verify_pattern_package import (MEMBER, ZIP_FIELDS, ARTIFACT_PATTERNS,
    read_archive, payload, repacked_stock, artifacts, word, packed, branch_target)

BASE = ROOT / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r32.ipsw'
BASE_SHA = 'cffbdada260b58c842a61eca9c4ff30c57f2718000712ebdc677537512bc9f10'
HOOKS = ((0xb13d8, 'wheel_post', 0xeb), (0x8f668, 'wheel_reject', 0xeb),
         (0x4a450, 'album_index_accept', 0xea))
ALLOWED = {0xb13d8, 0x8f668, 0x4a450, 0xe8, 0x2e8, 0x4a8}
# Reviewed ARMv4T contracts, independent of builder metadata: preserve the
# original post result; drain only on failure / Weel; release the same message.
POST = packed(0xe92d4010, 0xe59f2028, 0xe1a0e00f, 0xe12fff12,
              0xe1a04000, 0xe3500000, 0x1a000002, 0xe59f0014,
              0xe1a0e00f, 0xe12fff10, 0xe1a00004, 0xe8bd4010,
              0xe12fff1e, 0xabbcc, 0x6263c)
REJECT = packed(0xe92d4010, 0xe1a04000, 0xe5900004, 0xe59f1020,
                0xe1500001, 0x1a000002, 0xe59f0018, 0xe1a0e00f,
                0xe12fff10, 0xe59f1010, 0xe1a00004, 0xe8bd4010,
                0xe12fff11, 0x5765656c, 0x6263c, 0xb1774)
ALBUM = packed(0xe35b0006, 0x159ff044, 0xe92d500f, 0xe5960020,
               0xe5d000fd, 0xe3500000, 0x159ff034, 0xe59d1020,
               0xe3510000, 0x0a000007, 0xe59d022c, 0xe1d000b0,
               0xe3801020, 0xe2411061, 0xe3510019, 0x8a000001,
               0xe3c00020, 0xe59ff008, 0xe8bd500f, 0xe51ff004,
               0x35ca48, 0x35ca64)


def validated_parent(candidate):
    from verify_lcd_package import validated_parent as validate_lcd
    candidate = Path(candidate)
    if (candidate.with_suffix('.artifacts') / 'diagnostics-cleanup.json').exists():
        sys.path.insert(0, str(ROOT / 'gapless'))
        from verify_release_package import validated_parent as validate_cleanup
        return validated_parent(validate_cleanup(candidate))
    assert hashlib.sha256(BASE.read_bytes()).hexdigest() == BASE_SHA, 'r32 source pin'
    validate_lcd(BASE)
    old_infos, old, old_comment = read_archive(BASE)
    infos, new, comment = read_archive(candidate)
    assert list(old) == list(new) and comment == old_comment, 'ZIP members/comment'
    for left, right in zip(old_infos, infos):
        assert all(getattr(left, f) == getattr(right, f) for f in ZIP_FIELDS), 'ZIP metadata'
    assert all(old[n] == new[n] for n in old if n != MEMBER), 'unrelated member'
    before, after = payload(old[MEMBER]), payload(new[MEMBER])
    stock = read_archive(ROOT / 'iPod_4.3.1.1.ipsw')[1][MEMBER]
    assert new[MEMBER] == repacked_stock(stock, after), 'candidate repack/checksum'
    assert payload(old[MEMBER], 0x4228) == payload(new[MEMBER], 0x4228), 'updater preservation'
    assert before[-4:] == after[-4:] == bytes.fromhex('55aa55aa'), 'end marker'
    assert len(after) == len(before) + 212 <= 0x390000, 'image bounds/overlay size'
    assert word(after, 0xe8) == len(after), 'image length'
    for at in (0x2e8, 0x4a8):
        assert word(after, at) == 0x04000000 + len(after) - 4, 'image end pointer'
    old_art = artifacts(BASE.with_suffix('.artifacts'))
    art = candidate.with_suffix('.artifacts')
    new_art = artifacts(art)
    assert set(new_art) == set(old_art) | {
        'wheel_recovery.elf', 'wheel_recovery-symbols.json', 'wheel_recovery-edits.json'}, 'artifact set'
    for name, raw in old_art.items():
        assert new_art[name] == raw, ('carried artifact changed', name)
    elf = ELFFile(io.BytesIO(new_art['wheel_recovery.elf']))
    assert elf['e_machine'] == 'EM_ARM' and elf.elfclass == 32 and elf.little_endian, 'ARM ELF'
    code = elf.get_section_by_name('.text')
    base = len(before) - 4
    assert (code['sh_addr'], code['sh_size'], code['sh_flags']) == (base, 212, 6), 'code layout'
    assert code.data() == after[base:-4], 'appended bytes differ from ELF'
    assert code.data() == POST + REJECT + ALBUM, 'wheel/album ARM contract'
    assert {s.name for s in elf.iter_sections() if s['sh_flags'] & 2 and s['sh_size']} == {'.text'}, 'added RAM/section'
    assert not any(s['sh_size'] for s in elf.iter_sections()
                   if s.name in ('.data', '.bss') or s['sh_type'] in ('SHT_REL', 'SHT_RELA')), 'state/relocations'
    symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
               if s['st_info']['type'] in ('STT_FUNC', 'STT_OBJECT') and s['st_shndx'] != 'SHN_UNDEF'}
    assert symbols == {'wheel_post': base, 'wheel_reject': base + 60,
                       'album_index_accept': base + 124}, 'symbol layout'
    assert elf['e_entry'] == base, 'ELF entry'
    assert json.loads(new_art['wheel_recovery-symbols.json']) == symbols, 'symbol metadata'
    edits = json.loads(new_art['wheel_recovery-edits.json'])
    assert len(edits) == len(ALLOWED) and set(edits) == ALLOWED, 'edit metadata'
    for at, name, opcode in HOOKS:
        assert word(after, at) >> 24 == opcode and branch_target(after, at, opcode) == symbols[name], 'wheel/album hook target'
    restored = bytearray(after[:base])
    for at in ALLOWED:
        restored[at:at + 4] = before[at:at + 4]
    assert restored == before[:-4], 'unrelated image byte'
    build = json.loads((art / 'build.json').read_text())
    for name, expected in dict(source_sha256=BASE_SHA,
            sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
            osos_sha256=hashlib.sha256(after).hexdigest(), osos_bytes=len(after),
            headroom=0x390000-len(after), overlay_code_bytes=212,
            wheel_code_bytes=124, album_code_bytes=88,
            added_state_bytes=0, flashed=False).items():
        assert build[name] == expected, ('build metadata', name)
    return BASE


class CorruptionChecks(unittest.TestCase):
    def test_rejects_hooks_image_and_matching_elf_corruption(self):
        infos, members, comment = read_archive(self.candidate)
        original = payload(members[MEMBER])
        art = self.candidate.with_suffix('.artifacts')
        raw_elf = (art / 'wheel_recovery.elf').read_bytes()
        code = ELFFile(io.BytesIO(raw_elf)).get_section_by_name('.text')
        base = code['sh_addr']
        stock = read_archive(ROOT / 'iPod_4.3.1.1.ipsw')[1][MEMBER]
        cases = [('post hook', 0xb13d8, False, 'wheel/album hook target'),
                 ('reject hook', 0x8f668, False, 'wheel/album hook target'),
                 ('album hook', 0x4a450, False, 'wheel/album hook target'),
                 ('unrelated byte', 0x1234, False, 'unrelated image byte'),
                 ('heap', 0x4b0, False, 'unrelated image byte'),
                 ('LCD hook', 0xc3f7c, False, 'unrelated image byte'),
                 ('playback', 0x1165bc, False, 'unrelated image byte'),
                 ('appended byte', base, False, 'appended bytes'),
                 ('failure condition', base+24, True, 'wheel/album ARM contract'),
                 ('event type', base+60+52, True, 'wheel/album ARM contract'),
                 ('drain callee', base+56, True, 'wheel/album ARM contract'),
                 ('album selector', base+124, True, 'wheel/album ARM contract'),
                 ('album frame', base+124+40, True, 'wheel/album ARM contract'),
                 ('album continuation', base+124+84, True, 'wheel/album ARM contract'),
                 ('carried artifact', None, False, 'carried artifact changed')]
        with TemporaryDirectory(prefix='wheel-package-negative-') as temporary:
            for index, (label, at, match_elf, failure) in enumerate(cases):
                with self.subTest(corruption=label):
                    candidate = Path(temporary) / f'corrupt-{index}.ipsw'
                    target = candidate.with_suffix('.artifacts')
                    target.mkdir()
                    for pattern in (*ARTIFACT_PATTERNS, 'build.json'):
                        for source in art.glob(pattern):
                            shutil.copyfile(source, target/source.name)
                    image = bytearray(original)
                    if at is not None:
                        image[at] ^= 1
                    if match_elf:
                        raw = bytearray(raw_elf)
                        raw[code['sh_offset'] + at - base] ^= 1
                        (target/'wheel_recovery.elf').write_bytes(raw)
                    if at is None:
                        raw = bytearray((target/'lcd.elf').read_bytes())
                        raw[-1] ^= 1
                        (target/'lcd.elf').write_bytes(raw)
                    changed = dict(members)
                    changed[MEMBER] = repacked_stock(stock, image)
                    with ZipFile(candidate, 'w') as archive:
                        archive.comment = comment
                        for info in infos:
                            archive.writestr(copy.copy(info), changed[info.filename])
                    build = json.loads((target/'build.json').read_text())
                    build['sha256'] = hashlib.sha256(candidate.read_bytes()).hexdigest()
                    build['osos_sha256'] = hashlib.sha256(image).hexdigest()
                    (target/'build.json').write_text(json.dumps(build))
                    with self.assertRaisesRegex(AssertionError, failure):
                        validated_parent(candidate)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    validated_parent(args.candidate)
    print('Wheel/Albums package: three hooks, 212 bytes, no added state, carried r32 feature bytes preserved: PASS')
    if args.self_test:
        target = args.candidate
        if (target.with_suffix('.artifacts') / 'diagnostics-cleanup.json').exists():
            from verify_release_package import validated_parent as validate_cleanup
            target = validate_cleanup(target)
        CorruptionChecks.candidate = target
        unittest.main(argv=[__file__], verbosity=2)
