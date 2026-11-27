"""Independently verify the ALAC overlay and return its unchanged base layer."""
import argparse
import hashlib
import io
import struct
import sys
from pathlib import Path
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile
from image_layout import repack

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-label-review-01.ipsw'
BASE_SHA = '363d0d31508f17f1868572da76a8f3de3db4bbf56dad49ea2d39ba8ea618b627'
MEMBER = 'Firmware-4.3.1.1'
ENTRIES = {0x358b20: 'gapless_trim_start', 0x358f4c: 'gapless_trim_finish_begin',
           0x35944c: 'gapless_trim_publish', 0x359920: 'gapless_trim_output',
           0x4f540: 'gapless_alac_decoded_hook'}


def word(data, at):
    return struct.unpack_from('<I', data, at)[0]


def archive(path):
    with ZipFile(path) as z:
        names = z.namelist()
        assert len(names) == len(set(names)), 'duplicate archive members'
        return {name: z.read(name) for name in names}


def payload(firmware):
    start, size = 512 + word(firmware, 0x420c), word(firmware, 0x4210)
    return firmware[start:start + size]


def symbol_table(raw):
    elf = ELFFile(io.BytesIO(raw))
    return {s.name: s for s in elf.get_section_by_name('.symtab').iter_symbols()}


def validated_base(candidate):
    """Validate the entire new layer before legacy verifiers inspect its base."""
    candidate = Path(candidate)
    if (candidate.with_suffix('.artifacts') / 'alarm_pattern.elf').exists():
        sys.path.insert(0, str(ROOT / 'clock_pomodoro'))
        from verify_pattern_package import BASE as alarm_base, validated_base as validate_alarm
        # No candidate byte is exempted from checks: validate the complete
        # outer layer first, then run this unchanged ALAC contract on its pin.
        validate_alarm(candidate)
        return validated_base(alarm_base)
    assert hashlib.sha256(BASE.read_bytes()).hexdigest() == BASE_SHA, 'base pin'
    old, new = archive(BASE), archive(candidate)
    assert old.keys() == new.keys(), 'archive member set'
    assert all(old[k] == new[k] for k in old if k != MEMBER), 'unrelated archive member'
    before, after = payload(old[MEMBER]), payload(new[MEMBER])
    stock = archive(ROOT / 'iPod_4.3.1.1.ipsw')[MEMBER]
    assert new[MEMBER] == repack(stock, after), 'firmware repack'
    assert after[-4:] == before[-4:] == bytes.fromhex('55aa55aa'), 'end marker'
    assert len(before) < len(after) <= 0x390000, 'image bounds'
    assert word(after, 0xe8) == len(after), 'image length'
    for at in (0x2e8, 0x4a8):
        assert word(after, at) == 0x04000000 + len(after) - 4, 'end pointer'
    art, base_art = candidate.with_suffix('.artifacts'), BASE.with_suffix('.artifacts')
    extension_raw = (art / 'alac.elf').read_bytes()
    elf = ELFFile(io.BytesIO(extension_raw))
    assert elf['e_machine'] == 'EM_ARM', 'extension architecture'
    code = elf.get_section_by_name('.text')
    assert code['sh_addr'] == len(before) - 4, 'append address'
    assert code.data() == after[len(before) - 4:-4], 'appended code'
    assert not any(s['sh_size'] for s in elf.iter_sections()
                   if s.name in ('.bss', '.data') or s['sh_type'] in ('SHT_REL', 'SHT_RELA')), 'new state/relocations'
    linked = symbol_table(extension_raw)
    for at, name in ENTRIES.items():
        instruction = word(after, at)
        assert instruction >> 24 == (0xeb if at == 0x4f540 else 0xea), (name, 'branch type')
        delta = (instruction & 0xffffff) << 2
        if delta & 0x2000000:
            delta -= 0x4000000
        target = at + 8 + delta
        assert target == linked[name]['st_value'], (name, 'branch target')
        assert len(before) - 4 <= target < len(after) - 4, (name, 'target range')
    restored = bytearray(after[:len(before) - 4])
    for at in (*ENTRIES, 0xe8, 0x2e8, 0x4a8):
        restored[at:at + 4] = before[at:at + 4]
    assert restored == before[:-4], 'unrelated image changes'
    base_playback = (base_art / 'playback.elf').read_bytes()
    playback = (art / 'playback.elf').read_bytes()
    old_elf = ELFFile(io.BytesIO(base_playback))
    old_code = old_elf.get_section_by_name('.text')
    expected = bytearray(base_playback)
    for at in ENTRIES:
        if at == 0x4f540:
            continue
        offset = old_code['sh_offset'] + at - old_code['sh_addr']
        expected[offset:offset + 4] = after[at:at + 4]
    assert playback == expected, 'playback artifact preservation'
    old_symbols = symbol_table(base_playback)
    shared = ('notes fifo fifo_len output_started empty_ring empty_mask empty_ready empty_queue '
              'synthetic_output finish_engine finish_blocks finish_frames finish_drop finish_ring '
              'finish_capacity gapless_engine_timing copy_frames gapless_output_tx '
              'gapless_start_mp3_clip').split()
    for name in shared:
        assert linked[name]['st_value'] == old_symbols[name]['st_value'], (name, 'state/function ABI')
    producer = linked['producer']['st_value']
    for index in range(14):
        assert producer + index * 4 == old_symbols[f'producer.{index}']['st_value'], 'producer layout'
    assert old_symbols['notes']['st_size'] == 32 * 28, 'note allocation'
    assert old_symbols['fifo']['st_size'] == 4992 * 4, 'FIFO allocation'
    # Every pre-existing artifact remains exact, except four playback entry words.
    for pattern in ('*.elf', '*-symbols.json', '*-layout.json', '*-edits.json'):
        for path in base_art.glob(pattern):
            if path.name != 'playback.elf':
                assert (art / path.name).read_bytes() == path.read_bytes(), path.name
    expected_elfs = {p.name for p in base_art.glob('*.elf')} | {'alac.elf'}
    assert {p.name for p in art.glob('*.elf')} == expected_elfs, 'ELF set'
    for path in art.glob('*.elf'):
        item = ELFFile(io.BytesIO(path.read_bytes()))
        for section in item.iter_sections():
            if section['sh_flags'] & 2 and section['sh_type'] != 'SHT_NOBITS':
                at, count = section['sh_addr'], section['sh_size']
                assert after[at:at + count] == section.data(), (path.name, section.name)
    return before, base_playback


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    validated_base(args.candidate)
    print('ALAC package: pinned base, exact image/ELF edits, branches, shared ABI, no new RAM, repack PASS')
