"""Small native Clock screen; old commands and active resource payloads stay intact."""
import struct
import sys
from pathlib import Path

ROOT_PATH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_PATH / 'navigation_qol'))
sys.path.insert(0, str(ROOT_PATH / 'menu_scroll'))
from actions_patch import functions, call, integer, symbol, method
from verify_resources import directory, records, word

ROOT = 0x1a507c
OPEN, SCREEN, LEAVE, TOGGLE, RESET = range(2553, 2558)
VIEW, MENU, PHASE, COUNTDOWN, ACTION, RESET_LABEL = range(0x6140, 0x6146)
CLOCK_VIEWPORT = 0x6146
HOME_KEY = 0x25f4  # unused entry in the stock Main Menu visibility tables
# Preference slots are independent of caption IDs: 2607..2609 already have
# unrelated stock strings, which remain untouched. The order follows Clock.
HOME_ITEMS = ((0x2607, 0x6147, 0xff57, 2444, 'Alarm Clock'),
              (0x2608, 0x6148, 0xff56, 2160, 'Sleep Timer'),
              (0x2609, 0x6149, 0xff4e, 2142, 'Date & Time'),
              (HOME_KEY, 0x614a, HOME_KEY, OPEN, 'Pomodoro'))
SCRIPT_AT, SCRIPT_LIMIT = 0x3714e4, 0x37fbc8
# Every appended helper in the pinned r19 image, independently checked against
# the carried ELF sections during packaging. Gapless ends before this region.
LIVE_CODE = ((0x35b9ec, 0x35e454), (0x35e454, 0x35eb38),
             (0x36ef10, 0x36f6d9), (0x37fde4, 0x38050c), (0x381a98, 0x381d88))


def extend_script(blob):
    head, count, explicit, footer, original = functions(blob)
    if (word(blob, 0), count, explicit) != (2553, 633, 19):
        raise ValueError('unexpected r19 command table')
    finish = b'\x43\x05'
    opening = call(1899, integer(0x80), integer(VIEW)) + method(0x76, symbol(SCREEN)) + finish
    screen = (call(1892) + call(1950) + method(0x8a, integer(0x261d), owner=0x99e)
              + call(1866, integer(VIEW)) + method(0x79, symbol(LEAVE))
              + method(0x7a, symbol(LEAVE)) + finish)
    added = [(OPEN, 1, 0, 6, opening), (SCREEN, 1, 0, 6, screen)]
    # These commands are consumed at the native command boundary; declaring
    # them still gives the command table a complete, valid atom range.
    added += [(atom, 1, 0, 2, finish) for atom in (LEAVE, TOGGLE, RESET)]
    updated = []
    for atom, argc, local, stack, code in original:
        if atom == 1879:
            anchor = call(1878, integer(0x25f8))
            # Native property3 identifies a menu item by its signed caption
            # ID, not the separate record ID. Keep the existing captions and
            # pass their visibility from each new preference independently.
            extra = b''.join(call(1877, integer(caption if caption < 0x8000 else caption - 0x10000),
                                  method(0x153, integer(key), owner=0x9ba, keep=True))
                             for key, _, caption, _, _ in HOME_ITEMS[:-1])
            extra += call(1878, integer(HOME_KEY))
            if code.count(anchor) != 1 or code[14:17] != b'\x07\x00\xe4' or code[242:] != finish:
                raise ValueError('unexpected stock Clock visibility command')
            code = bytearray(code.replace(anchor, anchor + extra))
            # This conditional skips the entire update outside the home
            # screen. Its displacement is relative to the opcode itself.
            struct.pack_into('>H', code, 15, 228 + len(extra))
            code = bytes(code)
            stack = 6  # outer row-update call plus the nested preference getter
        updated.append((atom, argc, local, stack, code))
    data, offsets = bytearray(), [0]
    for atom, argc, local, stack, code in updated + added:
        code += bytes(len(code) % 2)
        data.extend(struct.pack('<5H', atom, argc, local, stack, len(code)) + code + b'\0\0')
        offsets.append(len(data))
    header = bytearray(blob[:head])
    struct.pack_into('<H', header, 0, RESET + 1)
    header.extend(struct.pack('<HH', count + len(added), explicit + len(added)))
    header.extend(blob[head + 4:head + 4 + explicit * 2])
    header.extend(struct.pack('<5H', *range(OPEN, RESET + 1)))
    header.extend(bytes(-len(header) % 4))
    return bytes(header + struct.pack(f'<{len(offsets)}I', *offsets) + data + blob[footer:])


def install_resources(image, output):
    groups = directory(image)
    base = ROOT + word(image, ROOT + 4)
    changes = {}

    def place(data):
        output.extend(bytes(-len(output) % 4))
        at = len(output)
        output.extend(data)
        return at, len(data)

    def put(tag, key, data):
        changes.setdefault(tag, {})[key] = place(data)

    script = extend_script(groups['TCmd'][1][0x2380])
    if SCRIPT_AT + len(script) > SCRIPT_LIMIT:
        raise ValueError('new commands exceed verified obsolete-directory space')
    touched = SCRIPT_AT, SCRIPT_AT + len(script)
    for tag, (header, entries) in groups.items():
        table = ROOT + word(image, header + 12)
        live = [(table, table + len(entries) * 12)]
        for key, offset, size in struct.iter_unpack('<III', image[table:table + len(entries) * 12]):
            if tag == 'TCmd' and key == 0x2380:
                if base + offset != SCRIPT_AT:
                    raise ValueError('command resource moved')
                continue
            live.append((base + offset, base + offset + size))
        if any(a < touched[1] and touched[0] < b for a, b in live):
            raise ValueError('command extension overlaps a live resource')
    if any(a < touched[1] and touched[0] < b for a, b in LIVE_CODE):
        raise ValueError('command extension overlaps existing helper code')
    output[SCRIPT_AT:SCRIPT_AT + len(script)] = script
    script_table = ROOT + word(image, groups['TCmd'][0] + 12)
    struct.pack_into('<I', output, script_table + 8, len(script))

    for tag in ('View', 'Menu', 'Str '):
        if any(VIEW <= key <= 0x614a for key in groups[tag][1]):
            raise ValueError('private Pomodoro resource key already exists')
        if HOME_KEY in groups[tag][1]:
            raise ValueError('Main Menu Pomodoro key already exists')

    roots = records(groups['View'][1][0x250d])
    root = bytearray(next(r for r in roots if word(r, 12) == 0x6100))
    struct.pack_into('<I', root, 12, VIEW)
    put('View', 0x250d, struct.pack('<I', len(roots) + 1) + b''.join(roots) + root)

    def text(key, top, height, style):
        row = bytearray(records(groups['View'][1][0x3308])[0])
        struct.pack_into('<II', row, 12, key, 0x2500)
        for at, triple in ((0x1c, (0, 0x80000002, top)),
                           (0x28, (0, 0x80000002, 4)),
                           (0x34, (0, 0x80000008, height)),
                           (0x40, (0, 0x80000002, 0xfffffffc))):
            struct.pack_into('<3I', row, at, *triple)
        struct.pack_into('<I', row, 0x4c, style << 16)
        return row

    menu_view = bytearray(records(groups['View'][1][0x4582])[0])
    struct.pack_into('<II', menu_view, 12, MENU, 0x2500)
    struct.pack_into('<3I', menu_view, 0x1c, 0, 0x80000002, 60)
    # Native Clock numerals are 20x30, with a 4x30 colon. The UI provider
    # composes those existing resources for a standard bitmap widget.
    for key in range(0x25b9, 0x25c4):
        glyph = groups['BMap'][1][key]
        width, stride = (4, 4) if key == 0x25b9 else (20, 8)
        if (glyph[:28] != struct.pack('<4I2H2I', 0, 0, 30, width, stride, 2, 0, 30 * stride)
                or len(glyph) != 28 + 30 * stride):
            raise ValueError('unexpected native Clock digit bitmap')
    countdown = bytearray(records(groups['View'][1][0x4583])[0])
    struct.pack_into('<II', countdown, 12, COUNTDOWN, 0x2500)
    for at, triple in ((0x1c, (0, 0x80000002, 26)),
                       (0x28, (0, 0x80000022, 0)),
                       (0x34, (0, 0x80000008, 30)),
                       (0x40, (0, 0x80000004, 90))):
        struct.pack_into('<3I', countdown, at, *triple)
    put('View', VIEW, struct.pack('<I', 3) + text(PHASE, 4, 18, 0x2577)
        + countdown + menu_view)
    # Native Menu looks up View(menu ID) for its reusable row template before
    # loading Menu(menu ID). Both resources are required, even for two rows.
    put('View', MENU, groups['View'][1][0x6102])

    template = records(groups['Menu'][1][0x6102])[0]
    rows = []
    for command, label in ((TOGGLE, ACTION), (RESET, RESET_LABEL)):
        row = bytearray(template)
        for at, value in ((12, command * 4 + 2), (20, label), (32, label)):
            struct.pack_into('<I', row, at, value)
        rows.append(row)
    put('Menu', MENU, struct.pack('<I', 2) + b''.join(rows))

    clock = records(groups['Menu'][1][0x4581])
    # Native Contrast provider contributes the > arrow, with no setting value.
    entry = bytearray(next(r for r in records(groups['Menu'][1][0x278d])
                           if word(r, 12) == 2190 * 4 + 2))
    for at, value in ((4, 0x22), (12, OPEN * 4 + 2), (20, VIEW), (32, VIEW)):
        struct.pack_into('<I', entry, at, value)
    put('Menu', 0x4581, struct.pack('<I', len(clock) + 1) + b''.join(clock) + entry)
    # Clock reserves three visible rows below its time display. Give the
    # four-row menu a stock viewport, preserving the header and separator.
    clock_view = records(groups['View'][1][0x4582])
    viewport = bytearray(records(groups['View'][1][0x6100])[1])
    viewport[0x1c:0x4c] = clock_view[0][0x1c:0x4c]
    for at in (12, 20):
        struct.pack_into('<I', viewport, at, CLOCK_VIEWPORT)
    put('View', 0x4582, struct.pack('<I', len(clock_view)) + viewport + b''.join(clock_view[1:]))
    clock_menu = bytearray(records(groups['View'][1][0x6101])[0])
    struct.pack_into('<I', clock_menu, 12, 0x4581)
    put('View', CLOCK_VIEWPORT, struct.pack('<I', 1) + clock_menu)
    # Keep the order identical in the customization screen and home list.
    # The stock command, property provider, Yes/No value and save/reset path
    # remain in use; only the previously vacant option key is new.
    for menu_key, field in ((0x2512, 20), (0x278f, 24)):
        original = records(groups['Menu'][1][menu_key])
        at = next(i for i, row in enumerate(original)
                  if word(row, field) & 0xffff == 0x25f8)
        shortcuts = []
        for key, caption, home_caption, command, _ in HOME_ITEMS:
            shortcut = bytearray(original[at])
            if menu_key == 0x2512:
                if command == 2160:
                    # The native picker reads the selected item's prompt,
                    # value key and provider. Preserve the Clock row's fields.
                    shortcut = bytearray(next(row for row in clock
                                             if word(row, 12) == command * 4 + 2))
                for offset, value in ((4, 0x22), (12, command * 4 + 2),
                                      (20, home_caption), (32, key)):
                    struct.pack_into('<I', shortcut, offset, value)
            else:
                for offset, value in ((20, caption), (24, key), (32, caption)):
                    struct.pack_into('<I', shortcut, offset, value)
            shortcuts.append(shortcut)
        put('Menu', menu_key, struct.pack('<I', len(original) + len(shortcuts))
            + b''.join(original[:at + 1]) + b''.join(shortcuts)
            + b''.join(original[at + 1:]))
    put('Str ', HOME_KEY, b'Pomodoro\0')
    # OEM 4G customization labels use U+00A0 for the Extras indentation.
    # Home shortcuts and screen titles retain their plain captions.
    for _, caption, _, _, label in HOME_ITEMS:
        put('Str ', caption, ('\u00a0' + label).encode() + b'\0')
    for key, label in ((VIEW, 'Pomodoro'), (PHASE, 'Focus'), (COUNTDOWN, '25:00'),
                       (ACTION, 'Start'), (RESET_LABEL, 'Reset')):
        put('Str ', key, label.encode() + b'\0')

    edits = [script_table + 8]
    for tag, replacements in changes.items():
        header, entries = groups[tag]
        table = ROOT + word(image, header + 12)
        positions = {key: (base + at, size) for key, at, size in struct.iter_unpack(
            '<III', image[table:table + len(entries) * 12])}
        positions.update(replacements)
        at, _ = place(b''.join(struct.pack('<III', key, at - base, size)
                              for key, (at, size) in sorted(positions.items())))
        struct.pack_into('<I', output, header + 4, len(positions))
        struct.pack_into('<I', output, header + 12, at - ROOT)
        edits += [header + 4, header + 12]
    return edits, touched
