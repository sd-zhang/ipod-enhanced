"""Independently validate the stateless r36 performance layer and all r35 bytes."""
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
sys.path.insert(0, str(ROOT/'clock_pomodoro'))
from verify_pattern_package import (MEMBER, ZIP_FIELDS, ARTIFACT_PATTERNS,
    read_archive, payload, repacked_stock, artifacts, word, branch_target)

BASE = ROOT/'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r35.ipsw'
BASE_SHA = '9a3ebaddc983993765bc36e66eb1b1fcb9dea347056acc10ec02946c47e63375'
TEXT_SHA = 'ae8a8ffe93cec5958382d6258263cbd3f13c616306879414e55a8f96010349ef'
ALLOWED = {0x53478, 0x94a9c, 0xe8, 0x2e8, 0x4a8}
VM_CODE = struct.pack('<10I', 0xe59f1010, 0xe0401001, 0xe351004b, 0x359ff008,
                      0xe59f2008, 0xe59ff008, 2461, 0x94af4, 0x1041fb1c, 0x94aa0)


def script_profile(image):
    """Decode the actual resource directory; no builder metadata is trusted."""
    root = 0x1a507c
    for index in range(word(image, root+8)):
        tag, count, _, relative = struct.unpack_from('<4sIII', image, root+12+16*index)
        if tag == b'dmCT':
            for index in range(count):
                key, entry_relative, size = struct.unpack_from('<III', image, root+relative+12*index)
                if key == 0x2380:
                    start = root+word(image, root+4)+entry_relative
                    assert start+size <= len(image), 'ScriptVM resource extent'
                    blob = image[start:start+size]
                    break
            else: raise AssertionError('ScriptVM resource missing')
            break
    else: raise AssertionError('ScriptVM resource directory missing')
    assert struct.unpack_from('<H', image, 0x1dfdc2)[0] == 1847, 'ScriptVM function base'
    head = 8+4*struct.unpack_from('<H', blob, 6)[0]
    count, explicit = struct.unpack_from('<HH', blob, head)
    assert count >= explicit and 1847+count-explicit == 2461, 'ScriptVM function endpoint'
    ids = struct.unpack_from(f'<{explicit}H', blob, head+4)
    assert not any(2461 <= atom < 2536 for atom in ids), 'ScriptVM function/global collision'
    table = (head+4+2*explicit+3)&~3
    offsets = struct.unpack_from(f'<{count+1}I', blob, table)
    body = table+4*(count+1)
    assert offsets[0] == 0 and body+offsets[-1]+4 <= len(blob), 'ScriptVM function directory'
    for index, (begin, end) in enumerate(zip(offsets, offsets[1:])):
        assert end-begin >= 12 and body+end <= len(blob), 'ScriptVM function extent'
        atom, _, _, _, length = struct.unpack_from('<5H', blob, body+begin)
        expected = 1847+index if index < count-explicit else ids[index-count+explicit]
        assert atom == expected and not length&1 and length+12 <= end-begin, 'ScriptVM function ordinal/alignment'
    footer = body+offsets[-1]
    assert struct.unpack_from('<HH', blob, footer) == (75, 0), 'ScriptVM global declarations'
    return {'start': start, 'head': head, 'body': body, 'footer': footer, 'functions': count}


def validated_parent(candidate):
    from verify_timer_package import validated_parent as validate_timer
    candidate = Path(candidate)
    if (candidate.with_suffix('.artifacts')/'disc_separators.elf').exists():
        from verify_disc_package import validated_parent as validate_disc
        return validated_parent(validate_disc(candidate))
    assert hashlib.sha256(BASE.read_bytes()).hexdigest() == BASE_SHA, 'r35 source pin'
    validate_timer(BASE)
    old_infos, old, old_comment = read_archive(BASE)
    infos, new, comment = read_archive(candidate)
    assert list(old) == list(new) and comment == old_comment, 'ZIP members/comment'
    for left, right in zip(old_infos, infos):
        assert all(getattr(left, field) == getattr(right, field) for field in ZIP_FIELDS), 'ZIP metadata'
    assert all(old[name] == new[name] for name in old if name != MEMBER), 'unrelated ZIP member'
    before, after = payload(old[MEMBER]), payload(new[MEMBER])
    stock = read_archive(ROOT/'iPod_4.3.1.1.ipsw')[1][MEMBER]
    assert new[MEMBER] == repacked_stock(stock, after), 'stock repack/checksum'
    assert payload(old[MEMBER], 0x4228) == payload(new[MEMBER], 0x4228), 'updater unchanged'
    assert before[-4:] == after[-4:] == bytes.fromhex('55aa55aa'), 'end marker'
    old_art = artifacts(BASE.with_suffix('.artifacts'))
    art = candidate.with_suffix('.artifacts')
    new_art = artifacts(art)
    assert set(new_art) == set(old_art)|{'stock_performance.elf', 'stock-performance-symbols.json',
                                       'stock-performance-edits.json'}, 'artifact set'
    for name, raw in old_art.items(): assert new_art[name] == raw, ('carried artifact changed', name)
    assert (art/'diagnostics-cleanup.json').read_bytes() == (BASE.with_suffix('.artifacts')/'diagnostics-cleanup.json').read_bytes(), 'cleanup metadata'
    elf = ELFFile(io.BytesIO(new_art['stock_performance.elf']))
    code = elf.get_section_by_name('.text')
    base = len(before)-4
    assert elf['e_machine'] == 'EM_ARM' and elf.elfclass == 32 and elf.little_endian, 'ARM ELF'
    assert (code['sh_addr'], code['sh_size'], code['sh_flags']) == (base, 404, 6), 'code layout'
    assert len(after) == len(before)+404 <= 0x390000, 'image bounds'
    assert code.data() == after[base:-4], 'ELF/image correspondence'
    assert hashlib.sha256(code.data()).hexdigest() == TEXT_SHA, 'reviewed performance ARM contract'
    assert code.data()[364:] == VM_CODE, 'ScriptVM trampoline contract'
    assert {s.name for s in elf.iter_sections() if s['sh_flags']&2 and s['sh_size']} == {'.text'}, 'added RAM/section'
    assert not any(s['sh_size'] for s in elf.iter_sections() if s['sh_type'] in ('SHT_REL', 'SHT_RELA')), 'relocations'
    symbols = {s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
               if s['st_info']['type'] in ('STT_FUNC', 'STT_OBJECT') and s['st_shndx'] != 'SHN_UNDEF'}
    assert symbols == {'ipod4g_playlist_invalidate':base, 'ipod4g_script_globals':base+364}, 'symbol layout'
    assert elf['e_entry'] == base, 'ELF entry'
    assert json.loads(new_art['stock-performance-symbols.json']) == symbols, 'symbol metadata'
    edits = json.loads(new_art['stock-performance-edits.json'])
    assert len(edits) == len(ALLOWED) and set(edits) == ALLOWED, 'edit metadata'
    assert branch_target(after, 0x53478, 0xea) == base, 'playlist hook'
    assert branch_target(after, 0x94a9c, 0xea) == base+364, 'ScriptVM hook'
    script_profile(after)
    assert word(after, 0xe8) == len(after), 'image length'
    for at in (0x2e8, 0x4a8): assert word(after, at) == 0x04000000+len(after)-4, 'image end'
    restored = bytearray(after[:base])
    for at in ALLOWED: restored[at:at+4] = before[at:at+4]
    assert restored == before[:-4], 'unrelated image bytes'
    assert json.loads((art/'build.json').read_text()) == dict(source_sha256=BASE_SHA,
        sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(), osos_sha256=hashlib.sha256(after).hexdigest(),
        osos_bytes=len(after), headroom=0x390000-len(after), performance_code_bytes=404,
        added_state_bytes=0, flashed=False), 'build metadata'
    return BASE


class CorruptionChecks(unittest.TestCase):
    def check_corruptions(self, cases):
        infos, members, comment = read_archive(self.candidate)
        original = payload(members[MEMBER]); art = self.candidate.with_suffix('.artifacts')
        raw = (art/'stock_performance.elf').read_bytes()
        code = ELFFile(io.BytesIO(raw)).get_section_by_name('.text')
        stock = read_archive(ROOT/'iPod_4.3.1.1.ipsw')[1][MEMBER]
        with TemporaryDirectory(prefix='performance-package-negative-') as temporary:
            for index, (name, at, value, failure) in enumerate(cases):
                with self.subTest(name=name):
                    path = Path(temporary)/f'bad-{index}.ipsw'; target = path.with_suffix('.artifacts'); target.mkdir()
                    for pattern in (*ARTIFACT_PATTERNS, 'build.json', 'diagnostics-cleanup.json'):
                        for source in art.glob(pattern): shutil.copyfile(source, target/source.name)
                    image = bytearray(original)
                    if at is not None:
                        if value is None: image[at] ^= 1
                        else: struct.pack_into('<H', image, at, value)
                    if name.startswith('matching ELF'):
                        changed = bytearray(raw); changed[code['sh_offset']+at-code['sh_addr']] ^= 1
                        (target/'stock_performance.elf').write_bytes(changed)
                    if name == 'carried ELF':
                        changed = bytearray((target/'lcd.elf').read_bytes()); changed[-1] ^= 1
                        (target/'lcd.elf').write_bytes(changed)
                    if name == 'symbol metadata': (target/'stock-performance-symbols.json').write_text('{}')
                    if name == 'edit metadata': (target/'stock-performance-edits.json').write_text('[]')
                    if name == 'added RAM section':
                        changed = bytearray(raw)
                        elf = ELFFile(io.BytesIO(raw))
                        at = elf['e_shoff']+next(i for i,s in enumerate(elf.iter_sections()) if s.name == '.symtab')*elf['e_shentsize']+8
                        struct.pack_into('<I', changed, at, 2)
                        (target/'stock_performance.elf').write_bytes(changed)
                    new = dict(members); new[MEMBER] = repacked_stock(stock, image)
                    with ZipFile(path, 'w') as archive:
                        archive.comment = comment
                        for info in infos: archive.writestr(copy.copy(info), new[info.filename])
                    build = json.loads((target/'build.json').read_text())
                    build.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                 osos_sha256=hashlib.sha256(image).hexdigest())
                    if name == 'build metadata': build['added_state_bytes'] = 4
                    (target/'build.json').write_text(json.dumps(build))
                    with self.assertRaisesRegex(AssertionError, failure): validated_parent(path)

    def test_rejects_hook_code_and_preservation_corruption(self):
        base = len(payload(read_archive(BASE)[1][MEMBER]))-4
        self.check_corruptions([
            ('playlist hook',0x53478,None,'playlist hook'), ('VM hook',0x94a9c,None,'ScriptVM hook'),
            ('heap',0x4b0,None,'unrelated image bytes'), ('playback',0x38d480,None,'unrelated image bytes'),
            ('LCD',0xc3f7c,None,'unrelated image bytes'), ('native continuation',0x94af4,None,'unrelated image bytes'),
            ('code',base,None,'ELF/image'), ('matching ELF queue',base,None,'reviewed performance ARM contract'),
            ('matching ELF VM',base+364+28,None,'reviewed performance ARM contract'),
            ('carried ELF',None,None,'carried artifact'), ('added RAM section',None,None,'added RAM'),
            ('symbol metadata',None,None,'symbol metadata'), ('edit metadata',None,None,'edit metadata'),
            ('build metadata',None,None,'build metadata')])

    def test_rejects_unsafe_vm_profile(self):
        original = payload(read_archive(self.candidate)[1][MEMBER]); profile = script_profile(original)
        start, head, body, footer = (profile[k] for k in ('start','head','body','footer'))
        self.check_corruptions([
            ('base',0x1dfdc2,1846,'ScriptVM function base'),
            ('count',start+head,637,'ScriptVM function endpoint'),
            ('collision first',start+head+4,2461,'ScriptVM function/global collision'),
            ('collision last',start+head+4,2535,'ScriptVM function/global collision'),
            ('ordinal',start+body,1846,'ScriptVM function ordinal'),
            ('odd bytecode',start+body+8,1,'ScriptVM function ordinal/alignment'),
            ('global count',start+footer,74,'ScriptVM global declarations'),
            ('explicit globals',start+footer+2,1,'ScriptVM global declarations')])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path); parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args(); validated_parent(args.candidate)
    print('Performance package: exact ARM contracts, zero added state, all r35 bytes/artifacts preserved: PASS')
    if args.self_test:
        target = args.candidate
        if (target.with_suffix('.artifacts')/'disc_separators.elf').exists():
            from verify_disc_package import validated_parent as validate_disc
            target = validate_disc(target)
        CorruptionChecks.candidate = target
        unittest.main(argv=[__file__], verbosity=2)
