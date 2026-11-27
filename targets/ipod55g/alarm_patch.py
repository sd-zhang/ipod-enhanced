"""Install independently traced 5.5G alarm/backlight hooks into a linked image."""
import struct

from image import word

SIGNAL_BASE = 0x108b777c
SIGNAL_POINTERS = (0xa641c, 0x27e948, 0x2804dc, 0x286084)
SOURCES = ('alarm_flash.c', 'alarm_hooks.S')


def branch(at, destination, *, link=False, condition=14):
    displacement = destination - at - 8
    if displacement & 3 or not -(1 << 25) <= displacement < (1 << 25):
        raise ValueError('ARM branch out of range or unaligned')
    return (condition << 28) | (0x0b000000 if link else 0x0a000000) | ((displacement >> 2) & 0xffffff)


def install(image: bytearray, symbols: dict[str, int]) -> dict:
    patches = []
    def replace(at, expected, new, reason):
        if word(image, at) != expected:
            raise ValueError(f'Unexpected alarm patch site {at:#x}')
        struct.pack_into('<I', image, at, new)
        patches.append({'at': hex(at), 'expected': hex(expected), 'value': hex(new), 'reason': reason})
    # Every aligned native absolute pointer into the original signal array
    # must be accounted for before moving the array. This is a target contract,
    # not a claim that arbitrary firmware can be relocated by pattern matching.
    actual = {at: word(image, at) for at in range(0, 0x7358c0, 4)
              if SIGNAL_BASE <= word(image, at) < SIGNAL_BASE + 392 * 4
              and word(image, at) % 4 == 0}
    expected = {at: SIGNAL_BASE for at in SIGNAL_POINTERS}
    expected[0x281334] = SIGNAL_BASE + 4
    if actual != expected:
        raise ValueError(f'Unexpected native signal-table references: {actual!r}')
    new_table = symbols['ipod55g_alarm_signal_table']
    for at in SIGNAL_POINTERS:
        replace(at, SIGNAL_BASE, new_table, 'relocate complete native signal table')
    replace(0x281334, SIGNAL_BASE + 4, new_table + 4, 'native signal initialization first entry')
    replace(0x4f8904, 0x187, 0x188, 'include owned signal 392 in native initialization')
    hooks = (
        (0x238070, 0x0af964f0, 'ipod55g_clock_alarm_signal', False, 0),
        (0x2381d0, 0x0bf96498, 'ipod55g_clock_alarm_signal', True, 0),
        (0x285174, 0xebf90808, 'ipod55g_alarm_worker', True, 14),
        (0xc71f0, 0xe0800005, 'ipod55g_alarm_begin_hook', True, 14),
        (0xc7220, 0xe5810008, 'ipod55g_alarm_timer_hook', True, 14),
        (0xc71d4, 0xeb01555d, 'ipod55g_alarm_drain', True, 14),
        (0x285100, 0xea000019, 'ipod55g_alarm_cancel_hook', False, 14),
        (0x284588, 0xebf888f2, 'ipod55g_alarm_light_wait', True, 14),
        (0x284918, 0xebf8880e, 'ipod55g_alarm_light_wait', True, 14),
        (0x266288, 0xe92d41f0, 'ipod55g_alarm_power', False, 14),
        (0x26617c, 0xe92d4010, 'ipod55g_alarm_brightness', False, 14),
    )
    for at, expected, symbol, link, condition in hooks:
        replace(at, expected, branch(at, symbols[symbol], link=link, condition=condition), symbol)
    return {'patches': patches, 'signal': 0x188, 'signal_table': hex(new_table),
            'native_signal_count': 392, 'extended_signal_count': 393}
