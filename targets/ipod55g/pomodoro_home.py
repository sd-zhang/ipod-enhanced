"""Checked native Main Menu pinning for the pinned 5.5G stock 1.3 image."""
import hashlib
import struct

from image import resources, script_functions, word
from pomodoro_patch import branch
from pomodoro_ui import VIEW, OPEN, records, sequence, call, integer

KEY, LABEL, PIN = VIEW, 0x7126, 0x40000000


def apply(image, symbols):
    """Install after alarm_settings.apply; non-Pomodoro settings chain to it."""
    edits = []
    patches = (
        (0x193488, 0xebfdc95d, branch(0x193488, symbols['pomo_home_mask'], link=True)),
        (0x19353c, 0xebfdc930, branch(0x19353c, symbols['pomo_home_mask'], link=True)),
        (0x66c94c, symbols['alarm_setting_get'], symbols['pomo_home_setting_get']),
        (0x66c950, symbols['alarm_setting_set'], symbols['pomo_home_setting_set']),
    )
    for at, expected, replacement in patches:
        if word(image, at) != expected:
            raise ValueError(f'changed native Main Menu site {at:#x}')
    for at, expected, replacement in patches:
        struct.pack_into('<I', image, at, replacement)
        edits.append(at)
    return edits


def resource_updates(image):
    groups = resources(image)
    if LABEL in groups['Str ']:
        raise ValueError('Pomodoro customization caption collision')
    updates = {'Menu': {}, 'Str ': {LABEL: '\u2003\u2060Pomodoro\0'.encode()}}
    for menu, offset, match in ((0x5e1e, 24, 0x5f85), (0x6018, 28, 0x60005f85)):
        at, size = groups['Menu'][menu]
        rows = records(image[at:at + size])
        matches = [i for i, row in enumerate(rows) if word(row, offset) == match]
        if len(matches) != 1:
            raise ValueError('missing or duplicate native Stopwatch anchor')
        index = matches[0]
        row = bytearray(rows[index])
        if menu == 0x5e1e:
            struct.pack_into('<I', row, 12, OPEN * 4 + 2)
            struct.pack_into('<H', row, 24, KEY)
            struct.pack_into('<H', row, 32, KEY)
        else:
            struct.pack_into('<H', row, 24, LABEL)
            struct.pack_into('<H', row, 28, KEY)
            struct.pack_into('<H', row, 32, LABEL)
        rows.insert(index + 1, bytes(row))
        updates['Menu'][menu] = sequence(rows)
    return updates


def script_updates(image):
    """Register the extra key in the stock Main Menu visibility refresh."""
    original = script_functions(image)[3996]
    if hashlib.sha256(original).hexdigest() != 'b901c43fd9ec78fb95f50ea52c87e19f3a3cb52db0b7ff4e1c2b7cec027d0f7e':
        raise ValueError('changed native Main Menu visibility script')
    code = bytearray(original[10:-2])
    anchor = call(3995, integer(0x5f85))
    addition = call(3995, integer(KEY))
    at = code.index(anchor) + len(anchor)
    code[at:at] = addition
    # The one outer conditional skips the visibility updates outside Main
    # Menu. Preserve its target (the native return), moved by the insertion.
    struct.pack_into('>H', code, 15, 0x146 + len(addition))
    code.extend(bytes(len(code) % 2))
    header = bytearray(original[:10])
    struct.pack_into('<H', header, 8, len(code))
    return {3996: bytes(header + code + original[-2:])}
