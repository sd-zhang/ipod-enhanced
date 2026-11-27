"""Independently check the disc-separator layer and complete r36 preservation.

The reviewed overlay owns no static RAM. Its 1,032-byte footer belongs to the
existing Songs index allocation and is covered by native lifetime tests.
"""
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

BASE = ROOT/'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r36.ipsw'
BASE_SHA = 'dea731a0fd3e20180db8c375883cbe6338689bc4844080377b9ed5c096358a54'
TEXT_SHA = '30498b8bb85a32286e09289038f5487a8eec9f1cc4073804925fb08cfe07f793'
TEXT_SIZE, INDEX_FOOTER_BYTES = 2760, 1032
FUNCTION_OFFSETS = {
    'discs_index_alloc': 0, 'discs_index_finish': 20, 'discs_visible_count': 228,
    'ui_groups': 312, 'discs_model_property': 472, 'list_groups': 700, 'song_row': 872,
    'discs_select': 1004, 'discs_selected_song': 1216, 'discs_flash_song': 1332,
    'discs_draw_row': 1488, 'discs_header_draw': 1672,
    'discs_publish_hook': 2652, 'discs_count_hook': 2688,
}
HOOKS = ((0x4bca0, 0xeb013f0e, 'discs_index_alloc', 0xeb),
         (0x4bd28, 0xe5a49aac, 'discs_publish_hook', 0xea),
         (0x74c0c, 0xe1a00005, 'discs_count_hook', 0xea),
         (0x763c8, 0xeb0082fd, 'discs_selected_song', 0xeb))
VTABLE = ((0x29160, 0x29030, 0xa80d0, 'discs_select'),
          (0x2919c, 0x29030, 0x30944, 'discs_draw_row'),
          (0x23e9c, 0x23d7c, 0x42bbc, 'discs_flash_song'),
          (0x23024, 0x22fd4, 0x50188, 'discs_model_property'))
PROFILES = {
    TEXT_SIZE: (TEXT_SHA, FUNCTION_OFFSETS, HOOKS),  # Immutable r37 contract.
    2836: ('74b843a4ac790929fea94bd2307d9e7069e1d1b190f71f96ee91262d843652c2',
           {**FUNCTION_OFFSETS, 'discs_previous_min': 1672, 'discs_header_draw': 1720,
            'discs_publish_hook': 2700, 'discs_count_hook': 2736,
            'discs_previous_min_hook': 2756},
           HOOKS+((0x9ca60, 0xe594209c, 'discs_previous_min_hook', 0xea),)),
    5696: ('4eaee8737192b3bc6fc4ecc2417297ab0b2e31a0170bb6dd6bb11102694f5ff0',
           {**FUNCTION_OFFSETS, 'discs_previous_min': 1672,
            'discs_reveal_rect': 1720, 'discs_fast_target': 1848,
            'discs_fast_number': 2072, 'discs_header_draw': 2208,
            'discs_fast_wheel': 3188, 'current_song': 3992,
            'discs_fast_draw': 4112, 'discs_publish_hook': 5436,
            'discs_count_hook': 5472, 'discs_previous_min_hook': 5492,
            'discs_reveal_hook': 5520, 'digits': 5624},
           HOOKS+((0x9ca60, 0xe594209c, 'discs_previous_min_hook', 0xea),
                  (0xbfe64, 0xe28d1004, 'discs_reveal_hook', 0xea),
                  (0x3804b4, 0xebfffe89, 'discs_fast_wheel', 0xeb))),
}


PROFILES[5732] = (
    'c9232fc6a5297fd6d995864557621d8386fbdfed1f3ae373f5b044ea4163f611',
    {**PROFILES[5696][1], 'discs_fast_wheel_hook': 5552, 'digits': 5660},
    PROFILES[5696][2][:-1]+((0x86700, 0xea0be769, 'discs_fast_wheel_hook', 0xea),),
)

PROFILES[6044] = (
    '2981ea1888d3904aa464d3e1a8db71e47a3e42473e0edd293e50d17a182451af',
    {**FUNCTION_OFFSETS, 'discs_previous_min': 1672, 'discs_reveal_rect': 1720,
     'discs_fast_target': 2008, 'discs_fast_number': 2228,
     'discs_header_draw': 2364, 'discs_fast_wheel': 3344, 'current_song': 4340,
     'discs_fast_draw': 4460, 'discs_publish_hook': 5748,
     'discs_count_hook': 5784, 'discs_previous_min_hook': 5804,
     'discs_reveal_hook': 5832, 'discs_fast_wheel_hook': 5864, 'digits': 5972},
    PROFILES[5732][2],
)

PROFILES[6064] = (
    'e7f686ae2c3b0a8cd3d322bf5fdbce6cdd8b34b10bf7cc472f27292dbe13ac1c',
    {**PROFILES[6044][1], 'discs_fast_number': 2252, 'discs_header_draw': 2388,
     'discs_fast_wheel': 3368, 'current_song': 4360, 'discs_fast_draw': 4480,
     'discs_publish_hook': 5768, 'discs_count_hook': 5804,
     'discs_previous_min_hook': 5824, 'discs_reveal_hook': 5852,
     'discs_fast_wheel_hook': 5884, 'digits': 5992},
    PROFILES[6044][2],
)

PROFILES[6116] = (
    'dcba794038146e581436a3e6c58664589e449a13713c652ae9ac1d3cb148a6bf',
    {**PROFILES[6064][1], 'current_song': 4412, 'discs_fast_draw': 4532,
     'discs_publish_hook': 5820, 'discs_count_hook': 5856,
     'discs_previous_min_hook': 5876, 'discs_reveal_hook': 5904,
     'discs_fast_wheel_hook': 5936, 'digits': 6044},
    PROFILES[6064][2],
)

# r44: cached title initials and Songs letter jumps. Thumb entry symbols retain
# bit 0; the two linker veneers use ARMv4 BX interworking, not ARMv5 BLX.
PROFILES[6904] = (
    '39c9702ab0b93ef8cc19a95122456c276f4eef4e642fe9569272c3df78939ba9',
    {**PROFILES[6116][1], 'current_song': 4628, 'discs_fast_draw': 4748,
     'songs_cache_title': 6101, 'songs_fast_target': 6169, 'songs_title_hook': 6576,
     'discs_publish_hook': 6584, 'discs_count_hook': 6620,
     'discs_previous_min_hook': 6640, 'discs_reveal_hook': 6668,
     'discs_fast_wheel_hook': 6700, 'digits': 6832,
     '__ARMv4ABSLongBXThunk_songs_fast_target': 6736,
     '__ARMv4ABSLongBXThunk_songs_cache_title': 6748},
    PROFILES[6116][2]+((0x3cff4, 0xeb015797, 'songs_title_hook', 0xeb),),
)

# r45: refresh a live Songs caption and scan cached runs in ARM. The new
# Thumb-to-ARM veneer also retains its low-bit entry marker in the ELF.
PROFILES[6960] = (
    '70a5b33ab1e6bddd772bdadc4165e96aa414b1f6db6da8fdc4181086aea88f1a',
    {**PROFILES[6904][1], 'songs_cache_title': 6157, 'songs_fast_target': 6221,
     'scan': 6372, 'songs_title_hook': 6620, 'discs_publish_hook': 6628,
     'discs_count_hook': 6664, 'discs_previous_min_hook': 6684,
     'discs_reveal_hook': 6712, 'discs_fast_wheel_hook': 6744, 'digits': 6888,
     '__ARMv4ABSLongBXThunk_songs_fast_target': 6780,
     '__Thumbv4ABSLongBXThunk_scan': 6793,
     '__ARMv4ABSLongBXThunk_songs_cache_title': 6804},
    PROFILES[6904][2],
)

# r46: the Thumb cache helper no longer calls legacy Apple ARM code. The ARM
# title trampoline restores the original arguments/LR and tails to Apple.
PROFILES[6952] = (
    'adf6e007d6956bd3feb0318c97542aa42fbd4860b2c993fc835fa0f652e34fca',
    {**PROFILES[6960][1], 'songs_fast_target': 6197, 'scan': 6348,
     'songs_title_hook': 6596, 'discs_publish_hook': 6620,
     'discs_count_hook': 6656, 'discs_previous_min_hook': 6676,
     'discs_reveal_hook': 6704, 'discs_fast_wheel_hook': 6736, 'digits': 6880,
     '__ARMv4ABSLongBXThunk_songs_fast_target': 6772,
     '__Thumbv4ABSLongBXThunk_scan': 6785,
     '__ARMv4ABSLongBXThunk_songs_cache_title': 6796},
    PROFILES[6960][2],
)

# r47: host title-sort import, provenance-aware grouping, and no inactive
# forward disc mode at the final run. Native import dispatch stays in ARM.
PROFILES[6940] = (
    '0506ddfbace49fa1e8304addf7a8ca8d3a89e47f1505dc6846c4180e4a23e041',
    {'discs_index_alloc': 0, 'discs_index_finish': 20, 'discs_visible_count': 232,
     'ui_groups': 316, 'discs_model_property': 476, 'list_groups': 696,
     'song_row': 868, 'discs_select': 996, 'discs_selected_song': 1204,
     'discs_flash_song': 1312, 'discs_draw_row': 1452, 'discs_previous_min': 1632,
     'discs_reveal_rect': 1672, 'discs_fast_target': 1952, 'discs_fast_number': 2192,
     'discs_header_draw': 2320, 'discs_fast_wheel': 3300, 'current_song': 4580,
     'discs_fast_draw': 4700, 'songs_cache_title': 6089, 'songs_import_sort': 6137,
     'songs_fast_target': 6193, 'scan': 6344, 'songs_title_hook': 6556,
     'songs_import_sort_hook': 6580, 'discs_publish_hook': 6596,
     'discs_count_hook': 6632, 'discs_previous_min_hook': 6652,
     'discs_reveal_hook': 6680, 'discs_fast_wheel_hook': 6712,
     '__ARMv4ABSLongBXThunk_songs_fast_target': 6748,
     '__Thumbv4ABSLongBXThunk_scan': 6761,
     '__ARMv4ABSLongBXThunk_songs_cache_title': 6772,
     '__ARMv4ABSLongBXThunk_songs_import_sort': 6784, 'digits': 6868},
    PROFILES[6952][2]+((0x35caa0, 0xebfffec5, 'songs_import_sort_hook', 0xeb),),
)

# r48: immediate ASCII title initial and zero-row reverse-stroke preservation.
PROFILES[6968] = (
    '829fe8f65d7b11dcd58ccdbd38cdc5e0b2257a24d242e5ee9ceeb24fc97da64e',
    {**PROFILES[6940][1], 'songs_import_sort': 6165, 'songs_fast_target': 6221,
     'scan': 6372, 'songs_title_hook': 6584, 'songs_import_sort_hook': 6608,
     'discs_publish_hook': 6624, 'discs_count_hook': 6660,
     'discs_previous_min_hook': 6680, 'discs_reveal_hook': 6708,
     'discs_fast_wheel_hook': 6740, 'digits': 6896,
     '__ARMv4ABSLongBXThunk_songs_fast_target': 6776,
     '__Thumbv4ABSLongBXThunk_scan': 6789,
     '__ARMv4ABSLongBXThunk_songs_cache_title': 6800,
     '__ARMv4ABSLongBXThunk_songs_import_sort': 6812},
    PROFILES[6940][2],
)

# r49: Songs reuses the pinned named-list top reveal; direct ARM dispatch.
PROFILES[6976] = (
    '3d1baf3cccc5f78eba714a0f8f990130800c7f62934bb5c3527d1e2aec83873d',
    {**PROFILES[6968][1], 'current_song': 4576, 'discs_fast_draw': 4696,
     'songs_cache_title': 6085, 'songs_import_sort': 6161,
     'songs_fast_target': 6217, 'scan': 6368, 'discs_jump_redraw': 6580,
     'songs_title_hook': 6592, 'songs_import_sort_hook': 6616,
     'discs_publish_hook': 6632, 'discs_count_hook': 6668,
     'discs_previous_min_hook': 6688, 'discs_reveal_hook': 6716,
     'discs_fast_wheel_hook': 6748, 'digits': 6904,
     '__ARMv4ABSLongBXThunk_songs_fast_target': 6784,
     '__Thumbv4ABSLongBXThunk_scan': 6797,
     '__ARMv4ABSLongBXThunk_songs_cache_title': 6808,
     '__ARMv4ABSLongBXThunk_songs_import_sort': 6820},
    PROFILES[6968][2],
)

# r40 has the same size as an archived r39 review candidate. Select using
# both immutable size and ARM digest; never infer a contract from size alone.
EXACT_PROFILES = {(size, item[0]): item for size, item in PROFILES.items()}
R40_SHA = '3724a693989587c9bb3b95e203259376b8f399ecc167f3f5da1418cb1ef3bb57'
EXACT_PROFILES[5696, R40_SHA] = (
    R40_SHA,
    {**PROFILES[5732][1], 'discs_publish_hook': 5400,
     'discs_count_hook': 5436, 'discs_previous_min_hook': 5456,
     'discs_reveal_hook': 5484, 'discs_fast_wheel_hook': 5516, 'digits': 5624},
    PROFILES[5732][2],
)


def vtables(offsets):
    return VTABLE+(((0x29090, 0x29030, 0x3332d4, 'discs_fast_draw'),)
                   if 'discs_fast_draw' in offsets else ())


def profile(size, actual_digest):
    assert size in PROFILES, 'unsupported disc overlay profile'
    assert (size, actual_digest) in EXACT_PROFILES, 'reviewed disc ARM contract'
    digest, offsets, hooks = EXACT_PROFILES[size, actual_digest]
    allowed = {at for at, *_ in hooks}|{at for at, *_ in vtables(offsets)}|{0xe8, 0x2e8, 0x4a8}
    return digest, offsets, hooks, allowed


def about_contract(image, metadata):
    """Independent r50 display contract; builder metadata is not trusted."""
    edits = (*range(0x766a4, 0x766c4, 4), 0x766cc)
    assert metadata == dict(revision=50, apple_version='3.1.1',
        display_version='3.1.1 / r50', edits=list(edits),
        added_code_bytes=0, added_state_bytes=0), 'About metadata'
    assert image[0x766a4:0x766c4] == (
        bytes.fromhex('0040a0e100008fe2040000ea')
        + b'3.1.1 / r50\0' + bytes(8)), 'About display bytes'
    assert word(image, 0x766cc) == 0xe1a00000, 'About display bytes'
    return edits


def validated_parent(candidate):
    from verify_performance_package import validated_parent as validate_performance
    candidate = Path(candidate)
    assert hashlib.sha256(BASE.read_bytes()).hexdigest() == BASE_SHA, 'r36 source pin'
    validate_performance(BASE)
    old_infos, old, old_comment = read_archive(BASE)
    infos, new, comment = read_archive(candidate)
    assert list(old) == list(new) and comment == old_comment, 'ZIP members/comment'
    for left, right in zip(old_infos, infos):
        assert all(getattr(left, field) == getattr(right, field) for field in ZIP_FIELDS), 'ZIP metadata'
    assert all(old[name] == new[name] for name in old if name != MEMBER), 'unrelated ZIP member'
    before, after = payload(old[MEMBER]), payload(new[MEMBER])
    size = len(after)-len(before)
    digest, offsets, hooks, allowed = profile(size, hashlib.sha256(after[len(before)-4:-4]).hexdigest())
    stock = read_archive(ROOT/'iPod_4.3.1.1.ipsw')[1][MEMBER]
    assert new[MEMBER] == repacked_stock(stock, after), 'stock repack/checksum'
    assert payload(old[MEMBER], 0x4228) == payload(new[MEMBER], 0x4228), 'updater unchanged'
    assert before[-4:] == after[-4:] == bytes.fromhex('55aa55aa'), 'end marker'
    old_art = artifacts(BASE.with_suffix('.artifacts'))
    art = candidate.with_suffix('.artifacts')
    about = art/'about-version.json'
    about_edits = about_contract(after, json.loads(about.read_text())) if about.exists() else ()
    if about_edits:
        assert (size, digest) == (6976, PROFILES[6976][0]), 'About release profile'
    new_art = artifacts(art)
    assert set(new_art) == set(old_art)|{'disc_separators.elf', 'disc-separators-symbols.json',
                                       'disc-separators-edits.json'}, 'artifact set'
    for name, raw in old_art.items(): assert new_art[name] == raw, ('carried artifact changed', name)
    assert (art/'diagnostics-cleanup.json').read_bytes() == (BASE.with_suffix('.artifacts')/'diagnostics-cleanup.json').read_bytes(), 'cleanup metadata'
    elf = ELFFile(io.BytesIO(new_art['disc_separators.elf']))
    code = elf.get_section_by_name('.text')
    base = len(before)-4
    assert elf['e_machine'] == 'EM_ARM' and elf.elfclass == 32 and elf.little_endian, 'ARM ELF'
    # Literal strings merge into executable/read-only .text: AXMS, never W.
    assert (code['sh_addr'], code['sh_size'], code['sh_flags']) == (base, size, 0x36), 'code layout'
    assert len(after) <= 0x390000, 'image bounds'
    assert code.data() == after[base:-4], 'ELF/image correspondence'
    assert hashlib.sha256(code.data()).hexdigest() == digest, 'reviewed disc ARM contract'
    assert {s.name for s in elf.iter_sections() if s['sh_flags']&2 and s['sh_size']} == {'.text'}, 'added static RAM/section'
    assert not any(s['sh_size'] for s in elf.iter_sections() if s['sh_type'] in ('SHT_REL', 'SHT_RELA')), 'relocations'
    symbols = {s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
               if s['st_info']['type'] in ('STT_FUNC', 'STT_OBJECT') and s['st_shndx'] != 'SHN_UNDEF'}
    expected = {name:base+offset for name, offset in offsets.items()}
    assert symbols == expected, 'symbol layout'
    assert elf['e_entry'] == symbols['discs_draw_row'], 'ELF entry'
    assert json.loads(new_art['disc-separators-symbols.json']) == symbols, 'symbol metadata'
    edits = json.loads(new_art['disc-separators-edits.json'])
    assert len(edits) == len(allowed) and set(edits) == allowed, 'edit metadata'
    for at, original, name, opcode in hooks:
        assert word(before, at) == original, ('source hook', hex(at))
        assert branch_target(after, at, opcode) == symbols[name], ('disc branch hook', hex(at))
    for at, table, original, name in vtables(offsets):
        assert word(before, at) == original, ('source vtable', hex(at))
        assert word(after, at) == symbols[name]-table, ('disc vtable hook', hex(at))
    assert word(after, 0xe8) == len(after), 'image length'
    for at in (0x2e8, 0x4a8): assert word(after, at) == 0x04000000+len(after)-4, 'image end'
    restored = bytearray(after[:base])
    for at in allowed|set(about_edits): restored[at:at+4] = before[at:at+4]
    # This includes every resource/script, state initializer, framebuffer and
    # retained playback/VM/queue instruction outside the reviewed profile hooks.
    assert restored == before[:-4], 'unrelated image bytes'
    expected_build = dict(source_sha256=BASE_SHA,
        sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(), osos_sha256=hashlib.sha256(after).hexdigest(),
        osos_bytes=len(after), headroom=0x390000-len(after), disc_code_bytes=size,
        added_state_bytes=0, index_footer_bytes=INDEX_FOOTER_BYTES, flashed=False)
    if about_edits: expected_build['about_revision'] = 50
    assert json.loads((art/'build.json').read_text()) == expected_build, 'build metadata'
    return BASE


class CorruptionChecks(unittest.TestCase):
    def contract(self):
        after = payload(read_archive(self.candidate)[1][MEMBER])
        parent_size = len(payload(read_archive(BASE)[1][MEMBER]))
        return profile(len(after)-parent_size,
                       hashlib.sha256(after[parent_size-4:-4]).hexdigest())

    def check_corruptions(self, cases):
        infos, members, comment = read_archive(self.candidate)
        original = payload(members[MEMBER]); art = self.candidate.with_suffix('.artifacts')
        raw = (art/'disc_separators.elf').read_bytes()
        code = ELFFile(io.BytesIO(raw)).get_section_by_name('.text')
        stock = read_archive(ROOT/'iPod_4.3.1.1.ipsw')[1][MEMBER]
        with TemporaryDirectory(prefix='disc-package-negative-') as temporary:
            for index, (name, at, failure) in enumerate(cases):
                with self.subTest(name=name):
                    path = Path(temporary)/f'bad-{index}.ipsw'; target = path.with_suffix('.artifacts'); target.mkdir()
                    for pattern in (*ARTIFACT_PATTERNS, 'build.json', 'diagnostics-cleanup.json',
                                    'about-version.json'):
                        for source in art.glob(pattern): shutil.copyfile(source, target/source.name)
                    image = bytearray(original)
                    if at is not None: image[at] ^= 1
                    if name.startswith('matching ELF'):
                        changed = bytearray(raw); changed[code['sh_offset']+at-code['sh_addr']] ^= 1
                        (target/'disc_separators.elf').write_bytes(changed)
                    if name == 'carried ELF':
                        changed = bytearray((target/'stock_performance.elf').read_bytes()); changed[-1] ^= 1
                        (target/'stock_performance.elf').write_bytes(changed)
                    if name == 'symbol metadata': (target/'disc-separators-symbols.json').write_text('{}')
                    if name == 'edit metadata': (target/'disc-separators-edits.json').write_text('[]')
                    if name == 'about metadata': (target/'about-version.json').write_text('{}')
                    if name == 'about missing metadata': (target/'about-version.json').unlink()
                    if name == 'added static RAM':
                        changed = bytearray(raw); elf = ELFFile(io.BytesIO(raw))
                        field = elf['e_shoff']+next(i for i,s in enumerate(elf.iter_sections()) if s.name == '.symtab')*elf['e_shentsize']+8
                        struct.pack_into('<I', changed, field, 2)
                        (target/'disc_separators.elf').write_bytes(changed)
                    new = dict(members); new[MEMBER] = repacked_stock(stock, image)
                    if name == 'manifest member': new['manifest.plist'] += b'corrupt'
                    if name == 'updater':
                        changed = bytearray(new[MEMBER]); changed[512+word(changed,0x4234)] ^= 1
                        new[MEMBER] = bytes(changed)
                    with ZipFile(path, 'w') as archive:
                        archive.comment = comment+b'corrupt' if name == 'ZIP comment' else comment
                        for info in infos:
                            changed_info = copy.copy(info)
                            if name == 'ZIP metadata': changed_info.external_attr ^= 1
                            archive.writestr(changed_info, new[info.filename])
                    build = json.loads((target/'build.json').read_text())
                    build.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                 osos_sha256=hashlib.sha256(image).hexdigest())
                    if name == 'build static state': build['added_state_bytes'] = 4
                    if name == 'build footer size': build['index_footer_bytes'] = 1036
                    if name == 'about build revision': build['about_revision'] = 49
                    (target/'build.json').write_text(json.dumps(build))
                    with self.assertRaisesRegex(AssertionError, failure): validated_parent(path)

    def test_rejects_all_profile_hooks_and_three_headers(self):
        _, offsets, hooks, _ = self.contract()
        cases = [(f'branch {at:x}', at, 'disc branch hook') for at, *_ in hooks]
        cases += [(f'vtable {at:x}', at, 'disc vtable hook') for at, *_ in vtables(offsets)]
        cases += [('length',0xe8,'image length'),('end1',0x2e8,'image end'),('end2',0x4a8,'image end')]
        self.check_corruptions(cases)

    def test_rejects_code_state_resources_and_carried_artifacts(self):
        base = len(payload(read_archive(BASE)[1][MEMBER]))-4
        _, offsets, _, _ = self.contract()
        self.check_corruptions([
            ('code',base,'reviewed disc ARM contract'), ('matching ELF allocation',base,'reviewed disc ARM contract'),
            ('matching ELF rendering',base+offsets['discs_header_draw'],'reviewed disc ARM contract'),
            ('framebuffer LCD path',0xc3f7c,'unrelated image bytes'),('state allocation',0x4b0,'unrelated image bytes'),
            ('resource header',0x1a5080,'unrelated image bytes'),('native VM',0x94af4,'unrelated image bytes'),
            ('original shared count call',0x74c10,'unrelated image bytes'),
            ('original shared property call',0x7317c,'unrelated image bytes'),
            ('carried sort importer body',0x35c5bc,'unrelated image bytes'),
            ('playback',0x38d480,'unrelated image bytes'),('carried ELF',None,'carried artifact'),
            ('added static RAM',None,'added static RAM')])

    def test_rejects_archive_and_review_metadata_corruption(self):
        self.check_corruptions([
            ('manifest member',None,'unrelated ZIP member'),('updater',None,'stock repack/checksum'),
            ('ZIP comment',None,'ZIP members/comment'),('ZIP metadata',None,'ZIP metadata'),
            ('symbol metadata',None,'symbol metadata'),('edit metadata',None,'edit metadata'),
            ('build static state',None,'build metadata'),('build footer size',None,'build metadata')])

    def test_rejects_about_revision_and_metadata_corruption(self):
        cases = [('about metadata', None, 'About metadata')]
        if (self.candidate.with_suffix('.artifacts')/'about-version.json').exists():
            cases += [(f'About word {at:x}', at, 'About display bytes')
                      for at in (*range(0x766a4, 0x766c4, 4), 0x766cc)]
            cases += [('about missing metadata', None, 'unrelated image bytes'),
                      ('about build revision', None, 'build metadata'),
                      ('About native converter', 0x766d0, 'unrelated image bytes')]
        self.check_corruptions(cases)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path); parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args(); validated_parent(args.candidate)
    print('Disc package: pinned hooks, exact read-only overlay, owned index footer, r36 preservation: PASS')
    if args.self_test:
        CorruptionChecks.candidate = args.candidate
        unittest.main(argv=[__file__], verbosity=2)
