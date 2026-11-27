"""Independently verify the alarm-pattern overlay against hardware-tested r28.

No builder or builder edit list is trusted. The archive, complete image prefix,
append, startup allocation, ARM hook/veneer contracts and every carried ELF are
checked before a legacy verifier may inspect the pinned underlying layer.
This is a static package check; native scheduling/audio behavior is tested by
verify_alarm_pattern.py, and audible/light timing still requires hardware.
"""
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'menu_scroll'))
from verify_resources import directory

BASE = ROOT / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r28.ipsw'
BASE_SHA = '92cca44cef2e0912030fbdfc246fcea8026e024b189f2b146dc81ec697b5750b'
STOCK_SHA = '5b5ce4e560ce03859a2ec30ca7f36ada01dd68e15ce0ca62b3bf847158378d58'
MEMBER = 'Firmware-4.3.1.1'
STATE, POMO_CALLEE = 0x14534654, 0x384338
CLOCK_CALLS = (0x158c74, 0x158cb8, 0x158cc4, 0x158cd0, 0x158cdc,
               0x158ce8, 0x158cf4, 0x158d00, 0x158d0c)
NATIVE_HOOKS = ((0xaf77c, 0xe7915106, 'alarm_duration_hook', 0xeb),
                (0xaf7b0, 0xe5810008, 'alarm_timer_hook', 0xeb),
                (0x1596d8, 0xebfd5813, 'alarm_pattern_advance', 0xeb),
                (0xaf764, 0xebfec3e6, 'alarm_pattern_finish', 0xeb),
                (0x159658, 0x05857008, 'alarm_cancel_hook', 0x0b))
ALLOWED = set(CLOCK_CALLS) | {item[0] for item in NATIVE_HOOKS} | {
    POMO_CALLEE, 0x4b0, 0x20428, 0xe8, 0x2e8, 0x4a8}
CARRIED_ELFS = {'letters', 'actions', 'volume', 'hotpaths', 'otg', 'pomodoro',
                'playback', 'refinements', 'alarm_light', 'alac'}
FUNCTIONS = {'alarm_tone_request', 'pomo_alarm_pattern', 'alarm_tone_begin',
             'alarm_timer_start', 'alarm_pattern_advance', 'alarm_pattern_finish',
             'alarm_pattern_cancel', 'alarm_duration_hook', 'alarm_timer_hook',
             'alarm_cancel_hook'}
FAST_FUNCTIONS = {'alarm_advance_hook', 'alarm_finish_hook'}
ARTIFACT_PATTERNS = ('*.elf', '*-symbols.json', '*-layout.json', '*-edits.json')
ZIP_FIELDS = ('filename', 'date_time', 'compress_type', 'comment', 'extra',
              'create_system', 'create_version', 'extract_version', 'flag_bits',
              'internal_attr', 'external_attr', 'volume', 'reserved')


def word(data, at):
    return struct.unpack_from('<I', data, at)[0]


def packed(*words):
    return struct.pack('<' + 'I' * len(words), *words)


def read_archive(path):
    with ZipFile(path) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)), 'duplicate ZIP member'
        assert archive.testzip() is None, 'ZIP CRC'
        return archive.infolist(), {name: archive.read(name) for name in names}, archive.comment


def payload(firmware, entry=0x4200):
    start, size = 512 + word(firmware, entry + 12), word(firmware, entry + 16)
    assert start + size <= len(firmware), 'payload bounds'
    return firmware[start:start + size]


def repacked_stock(stock, image):
    """Independent container construction, including the opaque stock suffix."""
    assert hashlib.sha256(stock).hexdigest() == STOCK_SHA, 'stock firmware pin'
    start, old_size = 512 + word(stock, 0x420c), word(stock, 0x4210)
    extra = len(image) - old_size
    assert extra >= 0, 'extended image size'
    shift = (extra + 511) // 512 * 512
    expected = bytearray(stock[:start] + image + bytes(shift - extra) + stock[start + old_size:])
    struct.pack_into('<I', expected, 0x4210, len(image))
    struct.pack_into('<I', expected, 0x421c, sum(image) & 0xffffffff)
    struct.pack_into('<I', expected, 0x4234, word(stock, 0x4234) + shift)
    return bytes(expected)


def artifacts(path):
    return {item.name: item.read_bytes() for pattern in ARTIFACT_PATTERNS
            for item in path.glob(pattern)}


def branch_target(image, at, opcode=0xeb):
    instruction = word(image, at)
    assert instruction >> 24 == opcode, ('branch condition/type', hex(at))
    displacement = instruction & 0xffffff
    if displacement & 0x800000:
        displacement -= 0x1000000
    return at + 8 + displacement * 4


def pair_cadence(image, symbols):
    """Select the r31 ABI; the complete veneer is still checked below."""
    return word(image, symbols['alarm_duration_hook'] + 36) == 0xe59f1014


def check_veneers(image, symbols):
    # This exact leaf wrapper tags only the duration argument and tail-calls
    # native AsyncPiezo. In particular it cannot tag the pitch or queue state.
    at = symbols['alarm_tone_request']
    assert image[at:at + 16] == packed(0xe59f2004, 0xe3811102, 0xe12fff12, 0xa6e6c), \
        'alarm-only duration tag wrapper'
    if 'alarm_advance_hook' in symbols:
        at = symbols['alarm_duration_hook']
        paired = pair_cadence(image, symbols)
        assert image[at:at + 36] == packed(
            0xe7915106, 0xe3150102, 0x1a000003,
            0xe59fc028 if paired else 0xe59fc020, 0xe59cc000,
            0xe35c0000, 0x012fff1e, 0xe92d500f, 0xe1a00005), 'inactive duration gate'
        if paired:
            assert image[at + 36:at + 44] == packed(0xe59f1014, 0xe7911106), \
                'native period argument'
            assert word(image, at + 64) == 0x10419a3c, 'native period table'
        extra = 8 if paired else 0
        assert branch_target(image, at + 36 + extra) == symbols['alarm_tone_begin'], 'duration begin call'
        assert image[at + 40 + extra:at + 56 + extra] == packed(
            0xe8bd500f, 0xe3c55102, 0xe12fff1e, STATE), 'duration tag cleanup/state'
        at = symbols['alarm_timer_hook']
        assert image[at:at + 24] == packed(
            0xe59fc01c, 0xe59cc000,
            *( (0xe35c0000, 0x05810008, 0x012fff1e) if paired else
               (0xe35c0001, 0x15810008, 0x112fff1e) ),
            0xe92d500f), 'inactive timer gate'
        assert branch_target(image, at + 24) == symbols['alarm_timer_start'], 'timer veneer call'
        assert image[at + 28:at + 40] == packed(0xe8bd500f, 0xe12fff1e, STATE), 'timer veneer restore'
        for name, compare, transfer, native, slow in (
                ('alarm_advance_hook', 0xe35c0002 if paired else 0xe35c0001,
                 0x959ff004 if paired else 0x159ff004, 0xaf72c, 'alarm_pattern_advance'),
                ('alarm_finish_hook', 0xe35c0000, 0x059ff004, 0x60704, 'alarm_pattern_finish')):
            at = symbols[name]
            assert image[at:at + 16] == packed(0xe59fc00c, 0xe59cc000, compare, transfer), \
                ('inactive call gate', name)
            assert branch_target(image, at + 16, 0xea) == symbols[slow], ('active tail call', name)
            assert image[at + 20:at + 28] == packed(STATE, native), ('inactive call literals', name)
    else:
        at = symbols['alarm_duration_hook']
        assert image[at:at + 12] == packed(0xe7915106, 0xe92d500f, 0xe1a00005), \
            'duration veneer register preservation'
        assert branch_target(image, at + 12) == symbols['alarm_tone_begin'], 'duration begin call'
        assert image[at + 16:at + 28] == packed(0xe8bd500f, 0xe3c55102, 0xe12fff1e), \
            'duration tag removed before native deadline arithmetic'
        at = symbols['alarm_timer_hook']
        assert word(image, at) == 0xe92d500f, 'timer veneer save'
        assert branch_target(image, at + 4) == symbols['alarm_timer_start'], 'timer veneer call'
        assert image[at + 8:at + 16] == packed(0xe8bd500f, 0xe12fff1e), 'timer veneer restore'
    at = symbols['alarm_cancel_hook']
    assert image[at:at + 12] == packed(0xe92d500f, 0xe10f0000, 0xe92d0003), \
        'conditional cancel register/flag save'
    assert branch_target(image, at + 12) == symbols['alarm_pattern_cancel'], 'cancel cleanup call'
    assert image[at + 16:at + 36] == packed(
        0xe8bd0003, 0xe128f000, 0xe8bd500f, 0xe5857008, 0xe12fff1e), \
        'conditional cancel flag restore and displaced store'


def validated_base(candidate):
    """Return (pinned r28 image, its Pomodoro ELF) only after full validation."""
    candidate = Path(candidate)
    if (candidate.with_suffix('.artifacts') / 'lcd.elf').exists():
        from verify_lcd_package import validated_parent
        return validated_base(validated_parent(candidate))
    assert hashlib.sha256(BASE.read_bytes()).hexdigest() == BASE_SHA, 'r28 base pin'
    old_infos, old, old_comment = read_archive(BASE)
    new_infos, new, new_comment = read_archive(candidate)
    assert list(old) == list(new), 'archive member order/set'
    assert old_comment == new_comment, 'ZIP comment'
    for before_info, after_info in zip(old_infos, new_infos):
        for field in ZIP_FIELDS:
            assert getattr(before_info, field) == getattr(after_info, field), \
                ('ZIP metadata', before_info.filename, field)
    assert all(old[name] == new[name] for name in old if name != MEMBER), 'unrelated archive member'
    before, after = payload(old[MEMBER]), payload(new[MEMBER])
    stock = read_archive(ROOT / 'iPod_4.3.1.1.ipsw')[1][MEMBER]
    assert old[MEMBER] == repacked_stock(stock, before), 'base firmware repack'
    assert new[MEMBER] == repacked_stock(stock, after), 'candidate firmware repack/checksum'
    assert payload(old[MEMBER], 0x4228) == payload(new[MEMBER], 0x4228), 'updater preservation'
    assert before[-4:] == after[-4:] == bytes.fromhex('55aa55aa'), 'end marker'
    assert len(before) < len(after) <= 0x390000 and len(after) % 4 == 0, 'image bounds'
    assert word(after, 0xe8) == len(after), 'image length header'
    for at in (0x2e8, 0x4a8):
        assert word(after, at) == 0x04000000 + len(after) - 4, 'image end pointer'

    old_art = artifacts(BASE.with_suffix('.artifacts'))
    art_path = candidate.with_suffix('.artifacts')
    new_art = artifacts(art_path)
    assert {Path(name).stem for name in old_art if name.endswith('.elf')} == CARRIED_ELFS, \
        'base ELF set'
    assert set(new_art) == set(old_art) | {
        'alarm_pattern.elf', 'alarm_pattern-symbols.json', 'alarm_pattern-edits.json'}, 'artifact set'
    elf = ELFFile(io.BytesIO(new_art['alarm_pattern.elf']))
    assert elf['e_machine'] == 'EM_ARM' and elf.elfclass == 32 and elf.little_endian, 'ARM ELF'
    code, bss = elf.get_section_by_name('.text'), elf.get_section_by_name('.bss')
    assert (code['sh_addr'], code['sh_size'], code['sh_flags']) == (
        len(before) - 4, len(after) - len(before), 6), 'append address/size/flags'
    assert code.data() == after[len(before) - 4:-4], 'appended bytes differ from ELF'
    assert 0 < code['sh_size'] <= 4096, 'alarm code budget'
    assert (bss['sh_addr'], bss['sh_size'], bss['sh_type'], bss['sh_flags']) == (
        STATE, 4, 'SHT_NOBITS', 3), 'one owned BSS word'
    assert {s.name for s in elf.iter_sections() if s['sh_flags'] & 2 and s['sh_size']} == {
        '.text', '.bss'}, 'unexpected allocated section'
    assert not any(s['sh_size'] for s in elf.iter_sections()
                   if s.name == '.data' or s['sh_type'] in ('SHT_REL', 'SHT_RELA')), 'data/relocations'
    linked = {s.name: s for s in elf.get_section_by_name('.symtab').iter_symbols() if s.name}
    assert not any(s['st_shndx'] == 'SHN_UNDEF' for s in linked.values()), 'undefined symbol'
    symbols = {name: s['st_value'] for name, s in linked.items()
               if s['st_info']['type'] in ('STT_FUNC', 'STT_OBJECT')}
    optimized = 'alarm_advance_hook' in symbols
    functions = FUNCTIONS | (FAST_FUNCTIONS if optimized else set())
    assert set(symbols) == functions | {'alarm_flash_active'}, 'alarm symbol set'
    for name in functions:
        symbol = linked[name]
        assert symbol['st_info']['type'] == 'STT_FUNC' and symbol['st_size'] > 0, ('function', name)
        assert symbols[name] % 4 == 0 and code['sh_addr'] <= symbols[name], ('ARM entry', name)
        assert symbols[name] + symbol['st_size'] <= len(after) - 4, ('function bounds', name)
    assert elf['e_entry'] == symbols['pomo_alarm_pattern'], 'ELF entry'
    state = linked['alarm_flash_active']
    assert (state['st_value'], state['st_size'], state['st_info']['type']) == (
        STATE, 4, 'STT_OBJECT'), 'state object'
    assert json.loads(new_art['alarm_pattern-symbols.json']) == symbols, 'symbol metadata'
    edits = json.loads(new_art['alarm_pattern-edits.json'])
    assert len(edits) == len(ALLOWED) and set(edits) == ALLOWED, 'independent edit-site contract'

    for at in CLOCK_CALLS:
        assert branch_target(before, at) == 0xa6e6c, 'base Clock request'
        assert branch_target(after, at) == symbols['alarm_tone_request'], ('Clock hook target', hex(at))
    for at, original, name, opcode in NATIVE_HOOKS:
        if optimized:
            name = {0x1596d8: 'alarm_advance_hook', 0xaf764: 'alarm_finish_hook'}.get(at, name)
        assert word(before, at) == original, ('base native instruction', hex(at))
        assert branch_target(after, at, opcode) == symbols[name], ('native hook target', name)
    assert word(before, POMO_CALLEE) == 0xa6e6c, 'base Pomodoro callee'
    assert word(after, POMO_CALLEE) == symbols['pomo_alarm_pattern'], 'Pomodoro callee literal'
    check_veneers(after, symbols)

    # The unchanged native zero-fill reads count/start at 20428/2042c and
    # clears [start, start + count). The planner reads the new exclusive end.
    assert word(before, 0x4b0) == STATE and word(after, 0x4b0) == STATE + 4, 'heap boundary'
    assert word(before, 0x20428) == 0xcd744 and word(after, 0x20428) == 0xcd748, 'startup zero-fill count'
    assert word(after, 0x2042c) == 0x14466f10, 'startup zero-fill start'
    assert word(after, 0x2042c) + word(after, 0x20428) == STATE + 4, 'zero-fill exclusive end'
    assert after[0x1fd08:0x1fd1c] == packed(
        0xe59f2718, 0xe59f0718, 0xe1a01000, 0xe0811002, 0xeb000273), 'native startup zero-fill call'
    assert after[0x206ec:0x20700] == packed(
        0xe3a02000, 0xe1500001, 0x34802004, 0x3afffffb, 0xe1a0f00e), 'native zero-fill loop'
    restored = bytearray(after[:len(before) - 4])
    for at in ALLOWED:
        assert before[at:at + 4] != after[at:at + 4], ('missing required edit', hex(at))
        restored[at:at + 4] = before[at:at + 4]
    assert restored == before[:-4], 'unrelated image byte changed'
    assert directory(after) == directory(before), 'UI resource preservation'

    original_pomo = old_art['pomodoro.elf']
    expected_pomo = bytearray(original_pomo)
    text = ELFFile(io.BytesIO(original_pomo)).get_section_by_name('.text')
    assert text['sh_addr'] <= POMO_CALLEE < text['sh_addr'] + text['sh_size'], 'Pomodoro literal location'
    offset = text['sh_offset'] + POMO_CALLEE - text['sh_addr']
    expected_pomo[offset:offset + 4] = after[POMO_CALLEE:POMO_CALLEE + 4]
    for name, raw in old_art.items():
        expected = expected_pomo if name == 'pomodoro.elf' else raw
        assert new_art[name] == expected, ('carried artifact changed', name)
    ranges = []
    for name, raw in new_art.items():
        if not name.endswith('.elf'):
            continue
        item = ELFFile(io.BytesIO(raw))
        for section in item.iter_sections():
            if section['sh_flags'] & 2 and section['sh_size']:
                if section['sh_type'] == 'SHT_NOBITS':
                    if name != 'alarm_pattern.elf':
                        assert section['sh_addr'] + section['sh_size'] <= STATE, ('old BSS overlap', name)
                    continue
                at, count = section['sh_addr'], section['sh_size']
                assert 0 <= at < at + count <= len(after) - 4, ('ELF bounds', name, section.name)
                assert after[at:at + count] == section.data(), ('ELF image correspondence', name, section.name)
                ranges.append((at, at + count, name))
    for left, right in zip(sorted(ranges), sorted(ranges)[1:]):
        assert left[1] <= right[0], ('ELF overlap', left, right)
    build = json.loads((art_path / 'build.json').read_text())
    for name, expected in {
        'source_sha256': BASE_SHA, 'sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
        'osos_sha256': hashlib.sha256(after).hexdigest(), 'osos_bytes': len(after),
        'headroom': 0x390000 - len(after), 'alarm_code_bytes': code['sh_size'],
        'added_state_bytes': 4, 'flashed': False,
    }.items():
        assert build[name] == expected, ('build metadata', name)
    return before, original_pomo


class CorruptionChecks(unittest.TestCase):
    """Exercise rejection paths without changing the reviewed archive/artifacts."""

    def test_corruptions_are_rejected_by_the_independent_contract(self):
        infos, members, comment = read_archive(self.candidate)
        original = payload(members[MEMBER])
        art = self.candidate.with_suffix('.artifacts')
        elf = ELFFile(io.BytesIO((art / 'alarm_pattern.elf').read_bytes()))
        code = elf.get_section_by_name('.text')
        tag_at = code['sh_addr'] + 4
        stock = read_archive(ROOT / 'iPod_4.3.1.1.ipsw')[1][MEMBER]
        cases = (
            ('undeclared byte', 0x1234, 'unrelated image byte'),
            ('wrong hook', 0x1596d8, 'native hook target'),
            ('heap word', 0x4b0, 'heap boundary'),
            ('startup count', 0x20428, 'startup zero-fill count'),
            ('Pomodoro callee', POMO_CALLEE, 'Pomodoro callee literal'),
            ('append byte', code['sh_addr'] + 128, 'appended bytes'),
            ('tag plus matching ELF', tag_at, 'alarm-only duration tag'),
            ('UI caption', 0x38ad10, 'unrelated image byte'),
            ('carried playback', None, 'carried artifact changed'),
            ('ZIP comment', None, 'ZIP comment'),
            ('updater byte', None, 'candidate firmware repack'),
            ('OSOS checksum', None, 'candidate firmware repack'),
        )
        symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
        if 'alarm_advance_hook' in symbols:
            cases += (('fast timer plus matching ELF', symbols['alarm_timer_hook'] + 8,
                       'inactive timer gate'),
                      ('fast advance plus matching ELF', symbols['alarm_advance_hook'] + 8,
                       'inactive call gate'))
        if pair_cadence(original, symbols):
            cases += (('period load plus matching ELF', symbols['alarm_duration_hook'] + 40,
                       'native period argument'),
                      ('period table plus matching ELF', symbols['alarm_duration_hook'] + 64,
                       'native period table'))
        with TemporaryDirectory(prefix='alarm-package-negative-') as temporary:
            root = Path(temporary)
            for index, (label, at, failure) in enumerate(cases):
                with self.subTest(corruption=label):
                    candidate = root / f'corrupt-{index}.ipsw'
                    target_art = candidate.with_suffix('.artifacts')
                    target_art.mkdir()
                    for pattern in (*ARTIFACT_PATTERNS, 'build.json'):
                        for path in art.glob(pattern):
                            shutil.copyfile(path, target_art / path.name)
                    changed, image = dict(members), bytearray(original)
                    if at is not None:
                        image[at] ^= 1
                    if label.endswith('plus matching ELF'):
                        raw = bytearray((target_art / 'alarm_pattern.elf').read_bytes())
                        raw[code['sh_offset'] + at - code['sh_addr']] ^= 1
                        (target_art / 'alarm_pattern.elf').write_bytes(raw)
                    if label == 'carried playback':
                        raw = bytearray((target_art / 'playback.elf').read_bytes())
                        raw[-1] ^= 1
                        (target_art / 'playback.elf').write_bytes(raw)
                    firmware = bytearray(repacked_stock(stock, image))
                    if label == 'updater byte':
                        firmware[512 + word(firmware, 0x4234)] ^= 1
                    if label == 'OSOS checksum':
                        firmware[0x421c] ^= 1
                    changed[MEMBER] = bytes(firmware)
                    with ZipFile(candidate, 'w') as archive:
                        archive.comment = comment + (b'changed' if label == 'ZIP comment' else b'')
                        for info in infos:
                            archive.writestr(copy.copy(info), changed[info.filename])
                    # Keep recorded hashes truthful, so rejection proves the
                    # structural/byte contract rather than a stale manifest.
                    build_path = target_art / 'build.json'
                    build = json.loads(build_path.read_text())
                    build['sha256'] = hashlib.sha256(candidate.read_bytes()).hexdigest()
                    build['osos_sha256'] = hashlib.sha256(image).hexdigest()
                    build_path.write_text(json.dumps(build))
                    with self.assertRaisesRegex(AssertionError, failure):
                        validated_base(candidate)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--self-test', action='store_true', help='also test deliberate corruptions in temporary copies')
    args = parser.parse_args()
    validated_base(args.candidate)
    print('Alarm package: pinned r28, exact hooks/append, 4-byte startup state, ELF/UI preservation, ZIP/repack PASS', flush=True)
    if args.self_test:
        CorruptionChecks.candidate = args.candidate
        unittest.main(argv=[__file__], verbosity=2)
