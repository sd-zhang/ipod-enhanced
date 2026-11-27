"""Apply verified 5.5G RTC runtime edits to an owned extension image."""
import struct
from research_clock import EPOCH, TABLE, EPOCH_LITERALS, TABLE_LITERALS


def branch(at, target, link=False):
    offset = target - at - 8
    if offset % 4 or not -(1 << 25) <= offset < (1 << 25):
        raise ValueError('unreachable ARM branch')
    return (0xeb000000 if link else 0xea000000) | ((offset >> 2) & 0xffffff)


def apply(image, symbols):
    edits = []
    def replace(at, expected, value):
        if struct.unpack_from('<I', image, at)[0] != expected:
            raise ValueError(f'changed stock RTC word at {at:#x}')
        struct.pack_into('<I', image, at, value)
        edits.append(at)
    for site in EPOCH_LITERALS:
        replace(site, EPOCH, symbols['pomo_calendar'])
    for site in TABLE_LITERALS:
        replace(site, TABLE, symbols['pomo_calendar'] + 12)
    replace(0xfff28, 0xe3540005, 0xe3540006)
    replace(0xa229c, 0xe3500005, 0xe3500006)
    for at, expected, name, link in (
        (0xa22a8, 0xe3a00f49, 'pomo_boot_hook', True),
        (0x28557c, 0xeaffffbb, 'pomo_background_hook', False),
        (0xb3764, 0xe92d4030, 'pomo_set_clock', False),
        (0xbb118, 0xe92d41fc, 'pomo_set_zone', False),
    ):
        replace(at, expected, branch(at, symbols[name], link))
    return edits
