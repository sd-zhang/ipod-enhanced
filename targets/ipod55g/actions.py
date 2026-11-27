"""OEM+ Now Playing actions resources, scripts and checked native bindings."""
import struct
from image import resources,script_functions,word
from scripts import make_record

OPEN,SCREEN,ALBUM,ARTIST,PARENTS,ADD,REMOVE,OTG_RESULT=range(5093,5101)
VIEW,LIST,MENU=0x7100,0x7101,0x7102
REMOVE_VIEW,REMOVE_LIST,REMOVE_MENU=0x7107,0x7108,0x7109
EMPTY_VIEW,EMPTY_LIST,EMPTY_MENU=0x710a,0x710b,0x710c


def op(code,n):return bytes([code])+struct.pack('>H',n)
def integer(n):return op(0x58,abs(n))+(b'\x22' if n<0 else b'')
def symbol(n):return op(0x3b,n)
def call(command,*args,owner=None,keep=False):
    target=symbol(command) if owner is None else symbol(owner)+op(0x35,command)
    return target+b'\x39'+b''.join(args)+op(0x3a,len(args))+(b'' if keep else b'\x02')
def method(selector,*args,owner=0x1392,keep=False):return call(selector,*args,owner=owner,keep=keep)
def records(blob):
    pos=4;rows=[]
    for _ in range(word(blob,0)):
        n=word(blob,pos)+4;rows.append(blob[pos:pos+n]);pos+=n
    if pos!=len(blob):raise ValueError('resource record bounds')
    return rows

def changes(image):
    groups=resources(image)
    def resource(tag,key):
        at,size=groups[tag][key];return image[at:at+size]
    def title(key):return call(4020,integer(0x80),integer(key))
    finish=b'\x43\x05'
    # Normalize transient Now Playing panels before native history captures keys.
    opening=(call(4252,b'\x42')+title(VIEW)+method(0x88,symbol(SCREEN))
             +method(0x7c,integer(3),integer(0x6f),symbol(4008))+finish)
    screen=(call(4012)+call(4073)+method(0x9d,integer(0x5fba),owner=0x1393)
            +call(3981,method(0x111,integer(-5),owner=0x13a4,keep=True))+finish)
    parents=(call(3969)+call(4246)+title(VIEW)+method(0x88,symbol(SCREEN))+finish)
    def browse(selector,heading,destination):
        show=title(heading)+destination
        # Global 0x1394=true means suppress drawing (native mode 8), not
        # animate. Rebuild safe history without displaying its parent screens.
        normalize=call(3986,symbol(PARENTS),b'\x43')
        # Exact safe parents need neither lifecycle work nor draw suppression.
        # Native branch-on-true 0x08 skips the whole normalization wrapper.
        prepare=(method(0x111,integer(-4),owner=0x13a4,keep=True)
                 +op(0x08,3+len(normalize))+normalize)
        body=(prepare
              +method(selector,integer(-3),owner=0x13a4,keep=True)
              +op(0x07,3+len(show))+show)
        return (method(selector,integer(-2),owner=0x13a4,keep=True)
                +op(0x07,3+len(body))+body+finish)
    scripts={
        OPEN:make_record(OPEN,opening,parameters=1),
        SCREEN:make_record(SCREEN,screen,parameters=1),
        ALBUM:make_record(ALBUM,browse(0x111,0x7105,call(4162,b'\x42')),parameters=1),
        # Stock 4180 skips Albums for a one-album artist. Keep this shortcut's
        # explicit Albums destination while using its native model and screen.
        ARTIST:make_record(ARTIST,browse(0x10f,0x7106,
            method(0x127,owner=0x13a4)+method(0x7e,integer(3),integer(0x10))
            +method(0x88,symbol(4184))+method(0x8c,symbol(4073))),parameters=1),
        PARENTS:make_record(PARENTS,parents),
    }
    # Feedback remains in the actions menu and refreshes the contextual label.
    for atom,operation in ((ADD,-6),(REMOVE,-7)):
        body=method(0x111,integer(operation),owner=0x13a4,keep=True)
        flash=method(0xcc,integer(5),owner=0x1393)
        scripts[atom]=make_record(atom,body+op(0x07,3+len(flash))+flash+finish,parameters=1)
    original=script_functions(image)
    for atom,prefix in ((4244,method(0x7c,integer(2),integer(0x6f),symbol(OPEN))),
                        (4239,method(0x7d,integer(2),integer(0x6f)))):
        record=original[atom];argc,locals=struct.unpack_from('<2H',record,2)
        size=struct.unpack_from('<H',record,8)[0]
        scripts[atom]=make_record(atom,prefix+record[10:10+size],parameters=argc,locals=locals)
    updates={'View':{},'Menu':{},'Str ':{}}
    def clone(view,list_id,menu,rows):
        parts=[bytearray(r) for r in records(resource('View',0x6017))]
        # Scroll container and its child resource both point to the cloned list.
        struct.pack_into('<I',parts[0],0x64,list_id)
        struct.pack_into('<I',parts[1],0x0c,list_id)
        struct.pack_into('<I',parts[1],0x14,list_id)
        parent=struct.pack('<I',len(parts))+b''.join(parts)
        child=bytearray(resource('View',0xfffffe85))
        struct.pack_into('<I',child,16,menu)
        updates['View'][view]=bytes(parent);updates['View'][list_id]=bytes(child)
        # Native list construction resolves its row widget from View(Menu ID).
        updates['View'][menu]=resource('View',0x6016)
        updates['Menu'][menu]=struct.pack('<I',len(rows))+b''.join(rows)
    action=records(resource('Menu',0x5f78))[0]
    def row(command,label):
        r=bytearray(action)
        for at,value in ((4,0x20),(12,command*4+2),(20,label),(24,label),(32,label)):
            struct.pack_into('<I',r,at,value)
        return bytes(r)
    settings=records(resource('Menu',0x6016));rows=[row(ALBUM,0x7103),row(ARTIST,0x7104)]
    for index in (2,3):
        r=bytearray(settings[index]);struct.pack_into('<I',r,4,0x22);struct.pack_into('<H',r,22,0);rows.append(bytes(r))
    # The picker reads its setting and group from the selected row. Preserve
    # the native Sleep Timer pair 0x6081/0x6000 and its duration-choice metadata.
    sleep=bytearray(next(r for r in records(resource('Menu',0x6181)) if word(r,12)==4326*4+2))
    struct.pack_into('<I',sleep,4,0x22);struct.pack_into('<H',sleep,26,0)
    rows.append(bytes(sleep))
    clone(VIEW,LIST,MENU,rows+[row(ADD,0x710d)])
    clone(REMOVE_VIEW,REMOVE_LIST,REMOVE_MENU,rows+[row(REMOVE,0x710e)])
    clone(EMPTY_VIEW,EMPTY_LIST,EMPTY_MENU,rows)
    root_rows=records(resource('View',0x5e15))
    template=next(r for r in root_rows if word(r,12)==0x6017)
    for view in (VIEW,REMOVE_VIEW,EMPTY_VIEW):
        r=bytearray(template);struct.pack_into('<I',r,12,view);root_rows.append(bytes(r))
    updates['View'][0x5e15]=struct.pack('<I',len(root_rows))+b''.join(root_rows)
    for key,text in ((VIEW,'Now Playing'),(0x7103,'Browse Album'),(0x7104,'Browse Artist'),
                     (0x7105,'Album Songs'),(0x7106,'Artist Albums'),
                     (0x710d,'Add to On-The-Go'),(0x710e,'Remove from On-The-Go')):
        updates['Str '][key]=text.encode()+b'\0'
    for tag,rows in updates.items():
        if any(key in groups[tag] for key in rows if key!=0x5e15):raise ValueError('actions resource ID collision')
    return updates,scripts


def patch(image,symbols):
    for at,expected,name in ((0x4fcd68,0x1e969c,'actions_artist'),(0x4fcd78,0x1e8e04,'actions_album')):
        if word(image,at)!=expected:raise ValueError('unexpected native actions binding')
        struct.pack_into('<I',image,at,symbols[name])
