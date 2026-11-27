"""5.5G Pomodoro native resources/scripts; no global Clock navigation rewrite."""
import struct
from image import resources, word
from scripts import make_record
from pomodoro_patch import branch

VIEW, MENU, PHASE, COUNTDOWN, ACTION, RESET_LABEL = range(0x7120, 0x7126)
OPEN, SCREEN, LEAVE, TOGGLE, RESET, SELECT = range(5111, 5117)
ENTER_CMD, LEAVE_CMD, TOGGLE_CMD, RESET_CMD, SELECT_CMD = (-0x5500, -0x5501, -0x5502, -0x5503, -0x5504)
LEFT_BUTTON, RIGHT_BUTTON = 0x7127, 0x7128


def records(blob):
    result=[];at=4
    for _ in range(word(blob,0)):
        size=word(blob,at)+4
        result.append(blob[at:at+size]);at+=size
    if at!=len(blob):raise ValueError('invalid native record bounds')
    return result


def sequence(rows):
    return struct.pack('<I',len(rows))+b''.join(rows)


def op(code,value):return bytes([code])+struct.pack('>H',value)
def integer(value):return op(0x58,abs(value))+(b'\x22' if value<0 else b'')
def symbol(value):return op(0x3b,value)
def call(atom,*args,owner=None):
    target=symbol(atom) if owner is None else symbol(owner)+op(0x35,atom)
    return target+b'\x39'+b''.join(args)+op(0x3a,len(args))+b'\x02'
def method(selector,*args,owner=5010):return call(selector,*args,owner=owner)


def script_updates():
    end=b'\x43\x05'
    opening=call(4020,integer(0x80),integer(VIEW))+method(0x88,symbol(SCREEN))+end
    screen=(call(4012)+call(4073)+method(0x9d,integer(0x5fba),owner=5011)
            +method(0x7c,integer(3),integer(0x6f),symbol(SELECT))
            +call(3981,integer(VIEW))+method(0x8b,symbol(LEAVE))+method(0x8c,symbol(LEAVE))
            +method(0x9d,integer(ENTER_CMD),owner=5011)+end)
    result={OPEN:opening,SCREEN:screen}
    for atom,command in ((LEAVE,LEAVE_CMD),(TOGGLE,TOGGLE_CMD),(RESET,RESET_CMD),(SELECT,SELECT_CMD)):
        result[atom]=method(0x9d,integer(command),owner=5011)+end
    return {atom:make_record(atom,code,parameters=1) for atom,code in result.items()}


def resource_updates(image):
    groups=resources(image)
    def get(tag,key):
        at,size=groups[tag][key];return image[at:at+size]
    root=bytearray(next(r for r in records(get('View',0x5e15)) if word(r,12)==0x618d))
    struct.pack_into('<I',root,12,VIEW)
    # Return root separately so the package composer can merge all features.
    roots=[bytes(root)]
    stopwatch=records(get('View',0x568d))
    background=bytearray(stopwatch[0])
    panels=[bytearray(stopwatch[i]) for i in (6,7,8)]
    text=bytearray(stopwatch[2])
    digits=bytearray(stopwatch[9])
    controls=bytearray(stopwatch[17])
    def geometry(row,top,left,height,right):
        for at,triple in ((0x1c,(0,0x80000002,top)),(0x28,(0,0x80000002,left)),
                          (0x34,(0,0x80000008,height)),(0x40,(0,0x80000002,right))):
            struct.pack_into('<3I',row,at,*[x&0xffffffff for x in triple])
    struct.pack_into('<II',text,12,PHASE,0x5e00)
    geometry(text,26,16,24,-16)
    for panel,left,right in zip(panels,(16,8,304),(-16,-304,-8)):
        geometry(panel,8,left,166,right)
    # The stock digit widget queries Str, but its notification filter inherits
    # None. Bind both contracts to Str so live countdown refreshes reach it.
    struct.pack_into('<III',digits,8,0x53747220,COUNTDOWN,0x5e00)
    # Stock large-digit advances: four 33px digits plus one 16px colon.
    geometry(digits,68,86,60,-86)
    struct.pack_into('<I',controls,12,MENU)
    struct.pack_into('<I',controls,0x60,MENU)
    geometry(controls,182,0,32,0)
    native_buttons=records(get('View',0x568e))
    buttons=[bytearray(native_buttons[i]) for i in (0,2,3,5)]
    for button,key in zip(buttons,(LEFT_BUTTON,ACTION,RIGHT_BUTTON,RESET_LABEL)):
        struct.pack_into('<II',button,12,key,0x5e00)
    # Dynamic bitmap properties select the original silver/blue button images;
    # the generic group retains Apple's wheel quantization and event delivery.
    entries=records(get('Menu',0x6181))
    # Extras owns the entry; both native Clock menus remain byte-for-byte stock.
    extras=records(get('Menu',0x5f75));extra=bytearray(entries[0])
    stopwatch,=[i for i,rec in enumerate(extras) if word(rec,32)==0x5f85]
    # The native secondary-bitmap getter needs flag 2 and the resource-file
    # provider/key pair. Copy Stopwatch's submenu indicator, not a setting.
    struct.pack_into('<I',extra,4,word(extra,4)|2)
    struct.pack_into('<I',extra,12,OPEN*4+2)
    extra[28:32]=extras[stopwatch][28:32]
    struct.pack_into('<h',extra,24,VIEW)
    struct.pack_into('<h',extra,32,VIEW)
    extras.insert(stopwatch+1,bytes(extra))
    updates={'View':{VIEW:sequence([background,*panels,text,digits,controls]),MENU:sequence(buttons)},
             'Menu':{0x5f75:sequence(extras)},
             'Str ':{VIEW:b'Pomodoro\0',PHASE:b'Focus\0',ACTION:b'Start\0',RESET_LABEL:b'Reset\0'}}
    return updates,roots


def apply(image,symbols):
    edits=[]
    for at,expected,name in ((0x66fb34,0x19e790,'pomo_ui_command'),
                             (0x66fac8,0x1d021c,'pomo_ui_property'),
                             (0x66fa9c,0x19f2e0,'pomo_ui_event')):
        if word(image,at)!=expected:raise ValueError(f'changed iPod Renderer slot {at:#x}')
        struct.pack_into('<I',image,at,symbols[name]);edits.append(at)
    for at,expected,value in ((0x1519fc,0xebffffcc,branch(0x1519fc,symbols['pomo_timer_deliver'],link=True)),
                              (0x151a04,0xebfe0106,0xe1a00000)):
        if word(image,at)!=expected:raise ValueError(f'changed timer worker {at:#x}')
        struct.pack_into('<I',image,at,value);edits.append(at)
    return edits
