"""Checked 5.5G preference hooks and native Settings row."""
import struct
from image import word, resources
from pomodoro_patch import branch

KEY = 0x7140


def apply(image, symbols):
    edits = []
    for at, expected, name, code in (
        (0x66c94c, 0x190b00, 'alarm_setting_get', False),
        (0x66c950, 0x1919d8, 'alarm_setting_set', False),
        (0x190a4c, 0xe5900098, 'alarm_reset_main_menu', True),
        (0xf3dc0, 0xe92d41f0, 'alarm_defaults', True),
        (0xaf88c, 0xe5c47099, 'alarm_preferences_loaded_hook', True),
    ):
        if word(image, at) != expected:
            raise ValueError(f'changed native preferences site {at:#x}')
        replacement = branch(at, symbols[name]) if code else symbols[name]
        struct.pack_into('<I', image, at, replacement); edits.append(at)
    return edits


def resource_updates(image):
    from actions import records
    groups = resources(image)
    if KEY in groups['Str ']: raise ValueError('alarm setting ID collision')
    at, size = groups['Menu'][0x6016]
    rows = records(image[at:at+size])
    row = bytearray(rows[2])  # stock in-place Shuffle cycling row
    struct.pack_into('<I', row, 4, 0x22)
    struct.pack_into('<6H', row, 24, KEY, 0, KEY, 0x6000, KEY, 0)
    rows.insert(8, bytes(row))  # alongside native backlight/brightness controls
    return {'Menu': {0x6016: struct.pack('<I', len(rows)) + b''.join(rows)},
            'Str ': {KEY: b'Alarm Backlight\0'}}
