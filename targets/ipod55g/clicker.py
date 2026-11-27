"""Restore the native four-mode 5.5G Clicker setting, without changing audio."""
import struct

from image import word


def apply(image):
    # UI enum0/1/2/3 uses Apple's localized On/Off/Speaker/Headphones labels.
    # Stored flags retain their physical meaning: bit0 piezo, bit1 PCM click.
    # The dormant single-output cases had these meanings reversed. Correct the
    # setter and both Settings refresh paths together; retain On/Off defaults,
    # preference layout, notifications, audio mixer and native video guard.
    edits = (
        (0x192368, 0xe3510001, 0xe3510003),  # cycle through all four choices
        (0x192394, 0x03c00001, 0x03c00002),  # Speaker: clear PCM
        (0x192398, 0x03800002, 0x03800001),  # Speaker: enable piezo
        (0x1923a4, 0x03c00002, 0x03c00001),  # Headphones: clear piezo
        (0x1923a8, 0x03800001, 0x03800002),  # Headphones: enable PCM
        (0x193804, 0xe3a00003, 0xe3a00002),  # initial refresh, piezo only
        (0x19381c, 0xe3a00002, 0xe3a00003),  # initial refresh, PCM only
        (0x193e3c, 0x03a00002, 0x03a00003),  # live refresh, PCM only
        (0x193e4c, 0x03a00003, 0x03a00002),  # live refresh, piezo only
    )
    for at, before, _ in edits:
        if word(image, at) != before:
            raise ValueError(f'unexpected native Clicker instruction at {at:#x}')
    for at, _, after in edits:
        struct.pack_into('<I', image, at, after)
    return [at for at, _, _ in edits]
