"""Checked stateless fast path for reserved native 5.5G ScriptVM globals."""
import struct

from image import resources, word
from pomodoro_patch import branch


def validate_profile(image):
    """Reject function/global overlap after all resource additions are merged."""
    try:
        if struct.unpack_from('<H', image, 0x4fd746)[0] != 3961:
            raise ValueError('changed ScriptVM implicit function base')
        at, size = resources(image)['TCmd'][0x2380]
        blob = image[at:at+size]
        if len(blob) != size or size < 8:
            raise ValueError('invalid ScriptVM resource extent')
        head = 8+4*struct.unpack_from('<H', blob, 6)[0]
        count, explicit = struct.unpack_from('<HH', blob, head)
        if count < explicit or 3961+count-explicit != 5010:
            raise ValueError('changed ScriptVM implicit function endpoint')
        ids = struct.unpack_from(f'<{explicit}H', blob, head+4)
        if any(5010 <= atom < 5093 for atom in ids):
            raise ValueError('explicit ScriptVM function shadows a native global')
        directory = (head+4+2*explicit+3)&~3
        offsets = struct.unpack_from(f'<{count+1}I', blob, directory)
        body = directory+4*(count+1)
        if offsets[0] or body+offsets[-1]+4 > size:
            raise ValueError('invalid ScriptVM function directory')
        for index, (start, end) in enumerate(zip(offsets, offsets[1:])):
            if end-start < 12 or body+end > size:
                raise ValueError('invalid ScriptVM function bounds')
            atom = struct.unpack_from('<H', blob, body+start)[0]
            expected = 3961+index if index < count-explicit else ids[index-count+explicit]
            if atom != expected:
                raise ValueError('changed ScriptVM function ordinal')
        footer = body+offsets[-1]
        if struct.unpack_from('<HH', blob, footer) != (83, 0):
            raise ValueError('changed ScriptVM native global declarations')
    except (IndexError, KeyError, struct.error) as error:
        raise ValueError('invalid ScriptVM resource profile') from error
    return {'implicit_function_end': 5010, 'global_count': 83,
            'explicit_globals': 0, 'explicit_functions': explicit}


def apply(image, symbols):
    """Call after resource merging; retain every original resolver continuation."""
    native = {0xd62f0: 0xe59f210c, 0xd62f4: 0xe59f310c,
              0xd6348: 0xe59c1000, 0xd634c: 0xe59fc0bc, 0xd6350: 0xe1500001,
              0xd6400: 0x10822abc, 0xd6404: 0x108317b0, 0xd6410: 0x10822ac0}
    for at, expected in native.items():
        if word(image, at) != expected:
            raise ValueError(f'changed native ScriptVM resolver site {at:#x}')
    validate_profile(image)
    target = symbols['ipod55g_script_globals']
    if target < 0x7358c0 or target & 3 or target+40 > len(image):
        raise ValueError('invalid ScriptVM trampoline placement')
    trampoline = (0xe59f1010, 0xe0401001, 0xe3510053, 0x359ff008,
                  0xe59f2008, 0xe59ff008, 5010, 0xd6348, 0x108317b0, 0xd62f4)
    if image[target:target+40] != struct.pack('<10I', *trampoline):
        raise ValueError('changed ScriptVM trampoline contract')
    struct.pack_into('<I', image, 0xd62f0, branch(0xd62f0, target))
    return [0xd62f0]
