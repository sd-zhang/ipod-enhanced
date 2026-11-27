"""Guarded native playlist sort-index invalidation optimization for 5.5G 1.3."""
import hashlib
import struct
from pomodoro_patch import branch


def apply(image, symbols):
    # Check the complete original loop and leaf, including all four special
    # cache fields. A firmware/layout change requires independent retracing.
    for start, end, digest in (
        (0x1137c4, 0x1137f4, '9e71ead7f9464acbc5f5690098110a3b96748ab58c68190dc937989dfc42603e'),
        (0x110208, 0x110308, 'b238312f68a5631595f03deaac1a09736dc399c658f5b0332e9e1f6b9704c8c8'),
    ):
        if hashlib.sha256(image[start:end]).hexdigest() != digest:
            raise ValueError('changed native playlist cache contract')
    target = symbols['ipod55g_playlist_invalidate']
    if target < 0x7358c0 or target & 3 or target+4 > len(image):
        raise ValueError('invalid playlist cache helper placement')
    struct.pack_into('<I', image, 0x1137c4, branch(0x1137c4, target))
    return [0x1137c4]
