"""Independently verify the LCD overlay and byte-for-byte preservation of r31."""
import argparse
import copy
import hashlib
import io
import json
import shutil
import struct
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile
from verify_pattern_package import (ROOT, MEMBER, ZIP_FIELDS, ARTIFACT_PATTERNS,
    read_archive, payload, repacked_stock, artifacts, word, packed, branch_target)

BASE = ROOT / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r31.ipsw'
BASE_SHA = '5f016dad9fb588de755d6cbfeb94360d4d592a65e34d43f0a6e7073e24f02aa7'
STATE = 0x14534658
HOOKS = ((0x3973c, 0xeb025739, 'lcd_display_start', 0xeb),
         (0xb27e0, 0xebff5547, 'lcd_pool_reset', 0xeb),
         (0xc3f7c, 0xe92d47f0, 'lcd_redraw', 0xea),
         (0x1549b4, 0xe92d40f8, 'lcd_init', 0xea),
         (0x154bd4, 0xe59f0034, 'lcd_display_mode', 0xea),
         (0x154c94, 0xe59f0024, 'lcd_suspend', 0xea),
         (0x154cc4, 0xe59f10d8, 'lcd_power', 0xea),
         (0x154e20, 0xe92d4070, 'lcd_contrast', 0xea))
ALLOWED = {h[0] for h in HOOKS} | {0x4b0, 0x20428, 0xe8, 0x2e8, 0x4a8}
FUNCTIONS = {h[2] for h in HOOKS} | {'lcd_initialize', 'lcd_run'} | {
    'lcd_redraw_native', 'lcd_init_native', 'lcd_display_mode_native',
    'lcd_suspend_native', 'lcd_power_native', 'lcd_contrast_native'}


def validated_parent(candidate):
    """Return pinned r31 only after validating the entire outer layer."""
    from verify_pattern_package import validated_base
    candidate = Path(candidate)
    if (candidate.with_suffix('.artifacts') / 'wheel_recovery.elf').exists():
        sys.path.insert(0, str(ROOT / 'navigation_qol'))
        from verify_wheel_package import validated_parent as validate_wheel
        return validated_parent(validate_wheel(candidate))
    assert hashlib.sha256(BASE.read_bytes()).hexdigest() == BASE_SHA, 'r31 source pin'
    validated_base(BASE)
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
    assert len(before) < len(after) <= 0x390000 and len(after) % 4 == 0, 'image bounds'
    assert word(after, 0xe8) == len(after), 'image length'
    for at in (0x2e8, 0x4a8):
        assert word(after, at) == 0x04000000 + len(after) - 4, 'image end pointer'
    old_art = artifacts(BASE.with_suffix('.artifacts'))
    art_path = candidate.with_suffix('.artifacts')
    new_art = artifacts(art_path)
    assert set(new_art) == set(old_art) | {'lcd.elf', 'lcd-symbols.json', 'lcd-edits.json'}, 'artifact set'
    for name, raw in old_art.items():
        assert new_art[name] == raw, ('carried artifact changed', name)
    elf = ELFFile(io.BytesIO(new_art['lcd.elf']))
    assert elf['e_machine'] == 'EM_ARM' and elf.elfclass == 32 and elf.little_endian, 'ARM ELF'
    code, bss = elf.get_section_by_name('.text'), elf.get_section_by_name('.bss')
    assert (code['sh_addr'], code['sh_size'], code['sh_flags']) == (
        len(before)-4, len(after)-len(before), 6), 'append address/size'
    assert 0 < code['sh_size'] <= 2048, 'LCD code budget'
    assert code.data() == after[len(before)-4:-4], 'appended bytes differ from ELF'
    assert (bss['sh_addr'], bss['sh_size'], bss['sh_type'], bss['sh_flags']) == (
        STATE, 4, 'SHT_NOBITS', 3), 'owned mutex ID'
    assert {s.name for s in elf.iter_sections() if s['sh_flags'] & 2 and s['sh_size']} == {
        '.text', '.bss'}, 'allocated sections'
    assert not any(s['sh_size'] for s in elf.iter_sections()
                   if s.name == '.data' or s['sh_type'] in ('SHT_REL', 'SHT_RELA')), 'data/relocations'
    linked = {s.name: s for s in elf.get_section_by_name('.symtab').iter_symbols() if s.name}
    assert not any(s['st_shndx'] == 'SHN_UNDEF' for s in linked.values()), 'undefined symbol'
    symbols = {n: s['st_value'] for n, s in linked.items()
               if s['st_info']['type'] in ('STT_FUNC', 'STT_OBJECT')}
    assert set(symbols) == FUNCTIONS | {'lcd_mutex'}, 'LCD symbol set'
    for name in FUNCTIONS:
        s = linked[name]
        assert s['st_info']['type'] == 'STT_FUNC' and s['st_size'] > 0, ('function', name)
        assert symbols[name] % 4 == 0 and code['sh_addr'] <= symbols[name], ('ARM entry', name)
        assert symbols[name]+s['st_size'] <= len(after)-4, ('function bounds', name)
    assert elf['e_entry'] == symbols['lcd_display_start'], 'ELF entry'
    s = linked['lcd_mutex']
    assert (s['st_value'], s['st_size'], s['st_info']['type']) == (STATE, 4, 'STT_OBJECT'), 'mutex object'
    assert json.loads(new_art['lcd-symbols.json']) == symbols, 'symbol metadata'
    edits = json.loads(new_art['lcd-edits.json'])
    assert len(edits) == len(ALLOWED) and set(edits) == ALLOWED, 'edit contract'
    for at, expected, name, opcode in HOOKS:
        assert word(before, at) == expected, 'base hook instruction'
        assert branch_target(after, at, opcode) == symbols[name], ('LCD hook target', name)
    for name, instruction, resume in (
            ('lcd_redraw_native', 0xe92d47f0, 0xc3f80),
            ('lcd_init_native', 0xe92d40f8, 0x1549b8),
            ('lcd_contrast_native', 0xe92d4070, 0x154e24)):
        at = symbols[name]
        assert after[at:at+12] == packed(instruction, 0xe51ff004, resume), ('LCD trampoline', name)
    for name, instruction, resume in (
            ('lcd_display_mode_native', 0xe59f0000, 0x154bd8),
            ('lcd_suspend_native', 0xe59f0000, 0x154c98),
            ('lcd_power_native', 0xe59f1000, 0x154cc8)):
        at = symbols[name]
        assert after[at:at+16] == packed(instruction, 0xe59ff000, 0x10419990, resume), \
            ('LCD relocated literal', name)
    assert word(before, 0x4b0) == STATE and word(after, 0x4b0) == STATE+4, 'heap boundary'
    assert word(before, 0x20428) == 0xcd748 and word(after, 0x20428) == 0xcd74c, 'startup zero-fill count'
    assert word(after, 0x2042c) == 0x14466f10, 'zero-fill start'
    assert word(after, 0x2042c)+word(after, 0x20428) == STATE+4, 'zero-fill end'
    restored = bytearray(after[:len(before)-4])
    for at in ALLOWED:
        assert before[at:at+4] != after[at:at+4], ('missing edit', hex(at))
        restored[at:at+4] = before[at:at+4]
    assert restored == before[:-4], 'unrelated image byte changed'
    for name, raw in old_art.items():
        if not name.endswith('.elf'):
            continue
        for section in ELFFile(io.BytesIO(raw)).iter_sections():
            if section['sh_flags'] & 2 and section['sh_size']:
                at, size = section['sh_addr'], section['sh_size']
                if section['sh_type'] == 'SHT_NOBITS':
                    assert at+size <= STATE, 'carried BSS overlap'
                else:
                    assert after[at:at+size] == section.data(), 'carried ELF image correspondence'
    build = json.loads((art_path/'build.json').read_text())
    for name, expected in dict(source_sha256=BASE_SHA,
            sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
            osos_sha256=hashlib.sha256(after).hexdigest(), osos_bytes=len(after),
            headroom=0x390000-len(after), lcd_code_bytes=code['sh_size'],
            added_state_bytes=4, total_alarm_state_bytes=8, flashed=False).items():
        assert build[name] == expected, ('build metadata', name)
    return BASE


class CorruptionChecks(unittest.TestCase):
    def test_independent_contract_rejects_corruptions(self):
        infos, members, comment = read_archive(self.candidate)
        original = payload(members[MEMBER])
        art = self.candidate.with_suffix('.artifacts')
        elf = ELFFile(io.BytesIO((art/'lcd.elf').read_bytes()))
        code = elf.get_section_by_name('.text')
        symbols = json.loads((art/'lcd-symbols.json').read_text())
        stock = read_archive(ROOT/'iPod_4.3.1.1.ipsw')[1][MEMBER]
        cases = [(f'hook {at:x}', at, 'LCD hook target') for at, *_ in HOOKS]
        cases += [('unrelated byte', 0x1234, 'unrelated image byte'),
                  ('draw skip', 0xc3f74, 'unrelated image byte'),
                  ('heap', 0x4b0, 'heap boundary'), ('startup', 0x20428, 'startup zero-fill count'),
                  ('append', code['sh_addr']+16, 'appended bytes'),
                  ('trampoline plus ELF', symbols['lcd_redraw_native'], 'LCD trampoline'),
                  ('literal plus ELF', symbols['lcd_power_native']+8, 'LCD relocated literal'),
                  ('carried alarm', None, 'carried artifact changed')]
        with TemporaryDirectory(prefix='lcd-package-negative-') as temporary:
            for index, (label, at, failure) in enumerate(cases):
                with self.subTest(corruption=label):
                    candidate = Path(temporary)/f'corrupt-{index}.ipsw'
                    target = candidate.with_suffix('.artifacts')
                    target.mkdir()
                    for pattern in (*ARTIFACT_PATTERNS, 'build.json'):
                        for source in art.glob(pattern):
                            shutil.copyfile(source, target/source.name)
                    image = bytearray(original)
                    if at is not None:
                        image[at] ^= 1
                    if label.endswith('plus ELF'):
                        raw = bytearray((target/'lcd.elf').read_bytes())
                        raw[code['sh_offset']+at-code['sh_addr']] ^= 1
                        (target/'lcd.elf').write_bytes(raw)
                    if label == 'carried alarm':
                        raw = bytearray((target/'alarm_pattern.elf').read_bytes())
                        raw[-1] ^= 1
                        (target/'alarm_pattern.elf').write_bytes(raw)
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
    print('LCD package: pinned r31, eight hooks, four-byte state, all carried feature bytes preserved: PASS')
    if args.self_test:
        target = args.candidate
        if (target.with_suffix('.artifacts') / 'wheel_recovery.elf').exists():
            from verify_wheel_package import validated_parent as validate_wheel
            target = validate_wheel(target)
        CorruptionChecks.candidate = target
        unittest.main(argv=[__file__], verbosity=2)
