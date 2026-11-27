"""Host-only experiment with the 4G's native RTC scheduler.

This is NOT an IPSW patcher. The relocated table lives in emulator-owned RAM;
boot-time allocation, ownership, event routing and UI integration are separate
work. Never install the modified image on a device.
"""
import hashlib
import struct

R19_SHA256 = '1dd7b429d41335c7f7ecf9ba9928e3cfe11552f2180dd7b8b17b7e1998b6d15b'
TABLE = 0x10440000  # supplied by the host fixture, NOT reserved firmware memory
TABLE_LITERAL_SITES = (0x2e9d4, 0x3cb00, 0x8cf80, 0xbe650, 0xcd298, 0x159adc)
RECORD_BYTES = 20
STOCK_RECORDS = 5
POMODORO_SLOT = 5


def experimental_image(image, *, sixth_slot=False):
    """Relocate the table, optionally enable the sixth record in native loops."""
    if hashlib.sha256(image).hexdigest() != R19_SHA256:
        raise ValueError('experiment requires the exact reviewed r19 OSOS')
    result = bytearray(image)
    for site in TABLE_LITERAL_SITES:
        if struct.unpack_from('<I', image, site)[0] != 0x103c98e4:
            raise ValueError(f'RTC table reference changed at {site:#x}')
        struct.pack_into('<I', result, site, TABLE)
    if sixth_slot:
        # Native setter/canceller already accept 0..5, but stock scheduler
        # and reset only visit 0..4. Accepting index 5 does NOT imply it works.
        for site, expected in ((0x2e894, 0xe3570005), (0x8cf28, 0xe3500005)):
            if struct.unpack_from('<I', image, site)[0] != expected:
                raise ValueError(f'RTC record count changed at {site:#x}')
            struct.pack_into('<I', result, site, (expected & ~0xff) | 6)
    return bytes(result)
