"""Lower the 5.5G native list Quick Scroll entry magnitude from 16 to 13."""
import struct

from image import word


def apply(image):
    # Signed step magnitude after native quantization and acceleration.
    # The existing BLT, cadence predicate and active-mode dispatch stay native.
    address = 0x23a534
    if word(image, address) != 0xe3510010:  # cmp r1, #16
        raise ValueError(f'unexpected native Quick Scroll entry comparison at {address:#x}')
    struct.pack_into('<I', image, address, 0xe351000d)  # cmp r1, #13
    return [address]
