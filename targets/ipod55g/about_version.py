"""Display the Enhanced revision in the native About Version value only."""
import hashlib
import struct

from image import resources

REVISION = 19
VERSION_TEXT = f'1.3 / r{REVISION}'
VIEW, STRING = 0x5fdc, 0x7141


def resource_updates(image):
    groups = resources(image)
    at, size = groups['View'][VIEW]
    original = image[at:at+size]
    if hashlib.sha256(original).hexdigest() != '33834af6571b4f38bbf718541b72b87f55f79fd19b5ea6f4e25d521a92450fcc':
        raise ValueError('unexpected native About View')
    # Stock constructs 1.3 from fixed version constants. Its getter and all
    # host/update identifiers remain untouched by this display-only resource.
    if hashlib.sha256(image[0x9ee70:0x9eed8]).hexdigest() != '7424c70928022a91262e06cb025301712b96c50cc511dbe2667f153041130481':
        raise ValueError('unexpected native About version getter')
    if STRING in groups['Str ']:
        raise ValueError('About version string resource collision')
    result = bytearray(original)
    row = 4+11*96
    struct.pack_into('<II', result, row+12, 0, 0xffffffff)
    struct.pack_into('<H', result, row+84, STRING)
    return {'View': {VIEW: bytes(result)},
            'Str ': {STRING: VERSION_TEXT.encode('ascii')+b'\0'}}
