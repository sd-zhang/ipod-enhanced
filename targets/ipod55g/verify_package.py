"""Independent preservation/layout audit of a built 5.5G candidate."""
import argparse
import hashlib
import json
import struct
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from elftools.elf.elffile import ELFFile
from image import load_stock, resources, script_functions, word, MEMBER, ROOT
from verify_disc_contract import audit as audit_disc_contract

TEXT = 0x7358c0


def audit_about_version(stock, image, *, required=False):
    """Independently constrain the sole display binding; retain older candidates."""
    old, new = resources(stock), resources(image)
    at, size = old['View'][0x5fdc]
    original = stock[at:at+size]
    at, size = new['View'][0x5fdc]
    actual = image[at:at+size]
    if actual == original and 0x7141 not in new['Str ']:
        assert not required, 'missing About revision'
        return None
    assert hashlib.sha256(original).hexdigest() == '33834af6571b4f38bbf718541b72b87f55f79fd19b5ea6f4e25d521a92450fcc', 'stock About View contract'
    expected = bytearray(original)
    struct.pack_into('<II', expected, 1072, 0, 0xffffffff)
    struct.pack_into('<H', expected, 1144, 0x7141)
    assert actual == expected, 'About Version binding/layout contract'
    assert 0x7141 in new['Str '], 'missing About revision string'
    at, size = new['Str '][0x7141]
    assert image[at:at+size] == b'1.3 / r19\0', 'About revision string contract'
    assert image[0x9ee70:0x9eed8] == stock[0x9ee70:0x9eed8], 'About stock version getter preservation'
    return {'revision': 19, 'text': '1.3 / r19', 'native_view': '0x5fdc',
            'stock_version_getter_preserved': True}


def audit_playlist_cache(image, symbols):
    start = symbols['ipod55g_playlist_invalidate']
    assert TEXT <= start < symbols['__extension_end'] and start % 4 == 0, 'playlist helper placement'
    # Independently pin the entire native ownership contract, restoring only
    # the one branch word in the original outer loop for the digest check.
    original = struct.pack('<I', 0xe92d4070) + image[0x1137c8:0x1137f4]
    assert hashlib.sha256(original).hexdigest() == '9e71ead7f9464acbc5f5690098110a3b96748ab58c68190dc937989dfc42603e', 'playlist loop contract'
    assert hashlib.sha256(image[0x110208:0x110308]).hexdigest() == 'b238312f68a5631595f03deaac1a09736dc399c658f5b0332e9e1f6b9704c8c8', 'playlist leaf contract'
    return {'native_indexes': 48, 'always_native_indexes': [6, 7, 9, 24, 48], 'stateless': True}


def audit_script_globals(image, symbols):
    """Independent instruction/resource contract; never call the installer."""
    start = symbols['ipod55g_script_globals']
    assert start >= TEXT and start % 4 == 0, 'ScriptVM trampoline alignment'
    instruction = word(image, 0xd62f0)
    displacement = instruction & 0xffffff
    if displacement & 0x800000: displacement -= 0x1000000
    assert instruction >> 24 == 0xea and 0xd62f8+displacement*4 == start, 'ScriptVM branch'
    expected = (0xe59f1010, 0xe0401001, 0xe3510053, 0x359ff008,
                0xe59f2008, 0xe59ff008, 5010, 0xd6348, 0x108317b0, 0xd62f4)
    assert image[start:start+40] == struct.pack('<10I', *expected), 'ScriptVM trampoline contract'
    for at, value in ((0xd62f4, 0xe59f310c), (0xd6348, 0xe59c1000),
                      (0xd634c, 0xe59fc0bc), (0xd6350, 0xe1500001),
                      (0xd6400, 0x10822abc), (0xd6404, 0x108317b0),
                      (0xd6410, 0x10822ac0)):
        assert word(image, at) == value, 'ScriptVM native continuation/literal'
    assert struct.unpack_from('<H', image, 0x4fd746)[0] == 3961, 'ScriptVM function base'
    at, size = resources(image)['TCmd'][0x2380]
    blob = image[at:at+size]
    head = 8+4*struct.unpack_from('<H', blob, 6)[0]
    count, explicit = struct.unpack_from('<HH', blob, head)
    assert count >= explicit and 3961+count-explicit == 5010, 'ScriptVM implicit endpoint'
    ids = struct.unpack_from(f'<{explicit}H', blob, head+4)
    assert all(not 5010 <= atom < 5093 for atom in ids), 'ScriptVM function/global collision'
    directory = (head+4+2*explicit+3)&~3
    offsets = struct.unpack_from(f'<{count+1}I', blob, directory)
    body = directory+4*(count+1)
    assert offsets[0] == 0 and body+offsets[-1]+4 <= len(blob), 'ScriptVM function bounds'
    for i, (begin, end) in enumerate(zip(offsets, offsets[1:])):
        assert end-begin >= 12 and body+end <= len(blob), 'ScriptVM function bounds'
        atom = struct.unpack_from('<H', blob, body+begin)[0]
        assert atom == (3961+i if i < count-explicit else ids[i-count+explicit]), 'ScriptVM ordinal'
    assert struct.unpack_from('<HH', blob, body+offsets[-1]) == (83, 0), 'ScriptVM global interval'
    return {'implicit_globals': 83, 'explicit_globals': 0, 'stateless': True}


def audit(stock, firmware, elf_path, symbols):
    size = word(firmware, 0x4210)
    assert size >= 0x736000 and size % 0x800 == 0, 'OSOS disk length'
    image = firmware[0x5000:0x5000+size]
    assert len(image) == size and word(firmware, 0x421c) == sum(image) & 0xffffffff, 'OSOS checksum'
    prefix = bytearray(stock.firmware[:0x5000])
    struct.pack_into('<I', prefix, 0x4210, size)
    struct.pack_into('<I', prefix, 0x421c, sum(image) & 0xffffffff)
    for at in (0x4234, 0x425c):
        struct.pack_into('<I', prefix, at, word(stock.firmware, at)+size-0x736000)
    assert firmware[:0x5000] == prefix, 'container preservation'
    assert firmware[0x5000+size:] == stock.firmware[0x73b000:], 'RSRC/AUPD/suffix preservation'
    end = word(image, 0xe8)
    assert TEXT < end <= 0x800000 and end % 4 == 0, 'loaded endpoint'
    assert image[end-4:end] == bytes.fromhex('55aa55aa') and not any(image[end:]), 'marker/padding'
    assert word(image, 0x308) == word(image, 0xdec) == end-4+0x4000000, 'startup marker references'
    expected = {0xe8: end, 0x308: end-4+0x4000000, 0xdec: end-4+0x4000000}
    # Fixed, independently enumerated hook contracts; do not import installers.
    pointers = {
        0x4fcd68: ('actions_artist', 0), 0x4fcd78: ('actions_album', 0),
        0x66fb34: ('pomo_ui_command', 0), 0x66fac8: ('pomo_ui_property', 0),
        0x66fa9c: ('pomo_ui_event', 0), 0x66c94c: ('pomo_home_setting_get', 0),
        0x66c950: ('pomo_home_setting_set', 0),
    }
    for at in (0xa22ec, 0xa7a28, 0xb01d8, 0xb3864, 0xbb21c, 0x1135d8):
        pointers[at] = ('pomo_calendar', 0)
    for at in (0x90730, 0x9e890, 0xa22f4, 0xb38e0, 0xfff38, 0x1020d0, 0x285580):
        pointers[at] = ('pomo_calendar', 12)
    for at in (0xa641c, 0x27e948, 0x2804dc, 0x286084, 0x281334):
        pointers[at] = ('ipod55g_alarm_signal_table', 4 if at == 0x281334 else 0)
    for at, (name, offset) in pointers.items(): expected[at] = symbols[name]+offset
    expected.update({0xfff28: 0xe3540006, 0xa229c: 0xe3500006, 0x4f8904: 0x188,
                     0x151a04: 0xe1a00000})
    # Enumerate independently of the installer. Audio, preference layout and
    # localized captions remain covered by the full stock preservation check.
    for at, value in ((0x192368, 0xe3510003), (0x192394, 0x03c00002),
                      (0x192398, 0x03800001), (0x1923a4, 0x03c00001),
                      (0x1923a8, 0x03800002), (0x193804, 0xe3a00002),
                      (0x19381c, 0xe3a00003), (0x193e3c, 0x03a00003),
                      (0x193e4c, 0x03a00002)):
        assert word(image, at) == value, f'Clicker settings contract {at:#x}'
        expected[at] = value
    branches = (
        (0xd62f0, 'ipod55g_script_globals', 0xea),
        (0x1137c4, 'ipod55g_playlist_invalidate', 0xea),
        (0xa22a8, 'pomo_boot_hook', 0xeb), (0x28557c, 'pomo_background_hook', 0xea),
        (0xb3764, 'pomo_set_clock', 0xea), (0xbb118, 'pomo_set_zone', 0xea),
        (0x190a4c, 'alarm_reset_main_menu', 0xea), (0xf3dc0, 'alarm_defaults', 0xea),
        (0xaf88c, 'alarm_preferences_loaded_hook', 0xea),
        (0x193488, 'pomo_home_mask', 0xeb), (0x19353c, 'pomo_home_mask', 0xeb),
        (0x1519fc, 'pomo_timer_deliver', 0xeb),
        (0x238070, 'ipod55g_clock_alarm_signal', 0x0a), (0x2381d0, 'ipod55g_clock_alarm_signal', 0x0b),
        (0x285174, 'ipod55g_alarm_worker', 0xeb), (0xc71f0, 'ipod55g_alarm_begin_hook', 0xeb),
        (0xc7220, 'ipod55g_alarm_timer_hook', 0xeb), (0xc71d4, 'ipod55g_alarm_drain', 0xeb),
        (0x285100, 'ipod55g_alarm_cancel_hook', 0xea),
        (0x284588, 'ipod55g_alarm_light_wait', 0xeb), (0x284918, 'ipod55g_alarm_light_wait', 0xeb),
        (0x266288, 'ipod55g_alarm_power', 0xea), (0x26617c, 'ipod55g_alarm_brightness', 0xea),
    )
    for at, name, opcode in branches:
        value = word(image, at)
        delta = value & 0xffffff
        if delta & 0x800000: delta -= 0x1000000
        assert value >> 24 == opcode and at+8+delta*4 == symbols[name], f'branch {at:#x}'
        expected[at] = value
    discs = audit_disc_contract(image, stock.osos, symbols, expected)
    for i in range(8):
        at = ROOT+12+16*i
        tag = stock.osos[at:at+4][::-1].decode()
        if tag in ('View', 'Menu', 'Str ', 'TCmd'):
            count = word(image, at+4)
            directory = ROOT+word(image, at+12)
            assert count > 0 and directory % 4 == 0 and \
                ROOT+140 <= directory < directory+12*count <= end-4, 'resource directory bounds'
            # Native lookup 0x17ff94 binary-searches unsigned IDs in the
            # sparse Menu/Str/View tables. Check the raw records before the
            # dictionary parser can hide their order. TCmd has one record;
            # untouched BMap keeps its pinned stock duplicate-ID behavior.
            keys = [word(image, directory+12*j) for j in range(count)]
            assert all(a < b for a, b in zip(keys, keys[1:])), 'resource directory key order'
            expected[at+4] = count
            expected[at+12] = word(image, at+12)
    old, new = resources(stock.osos), resources(image)
    about = audit_about_version(stock.osos, image)
    compare = bytearray(stock.osos[:TEXT])
    for at, value in expected.items(): struct.pack_into('<I', compare, at, value)
    assert image[:TEXT] == compare, 'unexpected original code/data/resource edit'
    with elf_path.open('rb') as stream:
        elf = ELFFile(stream)
        for section in elf.iter_sections():
            if not section['sh_flags'] & 2 or not section['sh_size']: continue
            offset = section['sh_addr'] - (0x10000000 if section['sh_flags'] & 1 else 0)
            length = section['sh_size']
            assert TEXT <= offset < offset+length <= symbols['__extension_end'] < end, 'ELF bounds'
            data = bytes(length) if section['sh_type'] == 'SHT_NOBITS' else section.data()
            assert image[offset:offset+length] == data, 'ELF bytes/zero state'
    changed = {'View': {0x5e15}, 'Menu': {0x6016, 0x5f75, 0x5e1e, 0x6018}, 'TCmd': {0x2380}}
    if about is not None: changed['View'].add(0x5fdc)
    for tag, rows in old.items():
        assert rows.keys() <= new[tag].keys(), 'deleted resource'
        for key, (at, length) in rows.items():
            if key in changed.get(tag, set()): continue
            new_at, new_length = new[tag][key]
            assert stock.osos[at:at+length] == image[new_at:new_at+new_length], f'resource {tag}/{key:#x}'
        for key in new[tag].keys()-rows.keys():
            assert (tag in ('View', 'Menu', 'Str ') and 0x7100 <= key <= 0x7140 or
                    tag == 'Str ' and key == 0x7141 and about is not None), 'unexpected resource addition'
    before, after = script_functions(stock.osos), script_functions(image)
    assert before.keys() <= after.keys(), 'deleted script'
    for atom, data in before.items():
        if atom not in (3996, 4239, 4244): assert after[atom] == data, f'changed stock script {atom}'
    assert all(5093 <= atom <= 5120 for atom in after.keys()-before.keys()), 'new script IDs'
    # The interpreter's original global declarations and constants must not move
    # ordinal. They follow the indexed function records as an opaque footer.
    def footer(data):
        at, length = resources(data)['TCmd'][0x2380]; b = data[at:at+length]
        h = 8+4*struct.unpack_from('<H', b, 6)[0]
        count, explicit = struct.unpack_from('<HH', b, h)
        directory = (h+4+2*explicit+3)&~3
        return b[directory+4*(count+1)+word(b, directory+4*count):]
    assert footer(stock.osos) == footer(image), 'VM global footer'
    resolver = audit_script_globals(image, symbols)
    playlist = audit_playlist_cache(image, symbols)
    return {'original_words_allowed': len(expected), 'loaded_bytes': end,
            'native_heap_base_unchanged': word(image, 0xdf0) == 0x10800000,
            'stock_scripts_preserved': len(before)-3, 'new_scripts': len(after)-len(before),
            'script_global_resolver': resolver, 'playlist_cache_invalidation': playlist,
            'clicker': {'modes': ['On', 'Off', 'Speaker', 'Headphones'],
                        'stored_flags': [3, 0, 1, 2], 'stock_audio_preserved': True},
            'album_disc_headings': discs, 'about_version': about}


def verify(candidate, stock_path):
    artifacts = candidate.with_suffix('.artifacts')
    manifest = json.loads((artifacts/'build.json').read_text())
    symbols = json.loads((artifacts/'extension-symbols.json').read_text())
    stock = load_stock(stock_path)
    data = candidate.read_bytes()
    assert hashlib.sha256(data).hexdigest() == manifest['archive_sha256'], 'archive digest'
    with ZipFile(BytesIO(data)) as archive:
        assert archive.namelist() == [MEMBER, 'manifest.plist'] and archive.testzip() is None
        assert archive.read('manifest.plist') == stock.manifest, 'updater manifest'
        firmware = archive.read(MEMBER)
    assert hashlib.sha256(firmware).hexdigest() == manifest['firmware_sha256'], 'firmware digest'
    osos = firmware[0x5000:0x5000+word(firmware, 0x4210)]
    assert hashlib.sha256(osos).hexdigest() == manifest['osos_sha256'], 'OSOS digest'
    for name, digest in manifest['source_sha256'].items():
        assert hashlib.sha256((artifacts/'source'/name).read_bytes()).hexdigest() == digest, f'frozen source {name}'
    result = audit(stock, firmware, artifacts/'extension.elf', symbols)
    about = audit_about_version(stock.osos, osos, required='enhanced_revision' in manifest)
    if about is not None:
        assert type(manifest.get('enhanced_revision')) is int and manifest['enhanced_revision'] == about['revision'], 'About revision manifest'
        assert manifest.get('about_version') == about['text'], 'About text manifest'
    else:
        assert 'about_version' not in manifest, 'About text without revision'
    result['about_version'] = about
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--stock', type=Path, default=Path('iPod_25.1.3.ipsw'))
    args = parser.parse_args()
    print(json.dumps(verify(args.candidate, args.stock), indent=2))
