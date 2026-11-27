"""Native ARM/ScriptVM action checks; graphics, storage allocator and OS are fixtures.

Run using the repository .venv with Unicorn JIT permission. No device writes.
"""
import struct
import tempfile
import unittest
from pathlib import Path
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
import actions
from image import load_stock,resources,word,script_functions
from layout import link,install_elf,extend_resources,finish
from scripts import merge_scripts,make_record
from research_actions import Machine,ScriptVM,APP,RENDERER,UI,QUERY,PREFS

PLAYING,QUEUE,OTG=0x10010800,0x10011000,0x10012000
HEADER,OTG_HEADER,ORDER,SHUFFLE=0x10013000,0x10013100,0x10014000,0x10014100
TRACK,ENTRY,SECOND,SOURCES=0x10015000,0x10016000,0x10016040,0x10017000
TRUE,FALSE=0xc0000002,0xc0000000


def seed(m,removing=False):
    m.write(0x1081e9ec,1) # Database scope singleton already initialized at boot.
    m.write(UI+0x1c,QUERY,PLAYING)
    m.write(QUERY,1,QUEUE,QUEUE)
    m.write(QUERY+0xca0,PREFS);m.write(QUERY+0xce0,OTG)
    m.write(QUERY+0xcfc,SOURCES,1);m.write(SOURCES,QUEUE)
    for queue,header in ((QUEUE,HEADER),(OTG,OTG_HEADER)):
        m.write(queue+4,0x706c7374);m.write(queue+0xc,0x10018000)
        m.write(queue+0x28,header)
        m.cpu.mem_write(header+0x32,struct.pack('<H',2))
        m.write(queue+0x300,ORDER,SHUFFLE)
    live=OTG if removing else QUEUE
    m.write(PLAYING+4,live);m.write(PLAYING+0x14,0)
    for entry,ident in ((ENTRY,17),(SECOND,18)):
        m.write(entry,live);m.write(entry+0x10,ident);m.write(entry+0x20,TRACK)
    m.write(TRACK+0x24,ENTRY);m.write(ENTRY+0x28,SECOND)
    m.write(ORDER+0x10,ENTRY,SECOND);m.write(SHUFFLE+0x10,SECOND,ENTRY)
    m.events=[]
    m.hook(0x1ea6f8,lambda:m.events.append(('refresh-add',)))
    m.hook(0x1e7f08,lambda:m.events.append(('refresh-remove',)))
    m.hook(0xa7f9c,lambda:m.freed.append(m.reg(0)))


def metadata(m,text='Track Album'):
    m.write(UI+0x28,0x676608,TRACK,QUERY,1)
    m.hook(0x255c60,lambda:1)
    def get():
        blob=struct.pack('<H',len(text))+text.encode('utf-16le')
        m.cpu.mem_write(m.reg(1),blob)
        return 0
    m.hook(0x9f8a4,get);m.hook(0x9f8f0,get)


class ActionsNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock=load_stock(Path('iPod_25.1.3.ipsw')).osos
        Path('build').mkdir(exist_ok=True)
        cls.directory=Path(tempfile.mkdtemp(prefix='ipod55g-actions-native-',dir='build'))
        elf=link([Path('targets/ipod55g/actions.c')],cls.directory,'actions_album')
        working,cls.symbols=install_elf(cls.stock,elf)
        cls.updates,cls.scripts=actions.changes(cls.stock)
        actions.patch(working,cls.symbols)
        updates={tag:dict(rows)for tag,rows in cls.updates.items()}
        updates['TCmd']={0x2380:merge_scripts(cls.stock,cls.scripts)}
        extend_resources(working,updates);cls.image=finish(working)
        (cls.directory/'osos.bin').write_bytes(cls.image)
    def machine(self,removing=False,vm=False):
        m=ScriptVM(self.image) if vm else Machine(self.image)
        seed(m,removing)
        return m
    def run_action(self,m,operation,album=True):
        return m.call(self.symbols['actions_album' if album else 'actions_artist'],UI,operation)
    def vm(self,removing=False):
        m=self.machine(removing,True)
        for typ,raw,atom,vt in ((2,APP,0x1392,0x66fe10),(3,RENDERER,0x1393,0x66fa6c),(33,UI,0x13a4,0x670fbc)):
            m.write(raw,vt);m.bind(atom,typ,raw)
        for atom in range(0x1394,0x1398):m.write(m.globals+4*(atom-5010),FALSE)
        return m
    def selector_hook(self,m,typ,selector,fn):
        ptr,virtual=m.member(typ,selector)
        vt={2:0x66fe10,3:0x66fa6c,33:0x670fbc}[typ]
        m.hook(m.read(vt+ptr) if virtual else ptr,fn)
    def test_resource_tree_links_all_contexts(self):
        for view,child,menu,count in ((0x7100,0x7101,0x7102,6),(0x7107,0x7108,0x7109,6),(0x710a,0x710b,0x710c,5)):
            parent=actions.records(self.updates['View'][view]);listing=actions.records(self.updates['View'][child])
            self.assertEqual((word(parent[0],0x64),word(parent[1],0xc),word(parent[1],0x14)),(child,)*3)
            self.assertEqual(word(listing[0],0xc),menu)
            self.assertEqual(len(actions.records(self.updates['Menu'][menu])),count)
        root=actions.records(self.updates['View'][0x5e15])
        keys=[word(r,12)for r in root]
        for key in (0x7100,0x7107,0x710a):self.assertEqual(keys.count(key),1)
    def test_native_vm_screen_live_queue_and_stock_keys(self):
        for removing,expected in ((False,0x7100),(True,0x7107)):
            m=self.vm(removing);calls=[]
            for typ,selector in ((2,0x7c),(3,0x9d),(3,0xb6)):
                self.selector_hook(m,typ,selector,lambda selector=selector:calls.append((selector,m.reg(1),m.reg(2),m.reg(3))) or 0)
            self.assertEqual(m.run(actions.SCREEN,0),TRUE)
            self.assertIn((0x7c,3,0x6f,4017*4+2),calls)
            self.assertIn((0x7c,3,0x6e,3973*4+2),calls)
            self.assertIn((0x7c,2,0x10,4220*4+2),calls)
            self.assertIn((0xb6,expected,0,0),calls)
    def test_native_vm_empty_context_omits_otg(self):
        m=self.vm();m.write(PLAYING+4,0);seen=[]
        for typ,selector in ((2,0x7c),(3,0x9d)):
            self.selector_hook(m,typ,selector,lambda:0)
        self.selector_hook(m,3,0xb6,lambda:seen.append(m.reg(1)) or 0)
        self.assertEqual(m.run(actions.SCREEN,0),TRUE);self.assertEqual(seen,[0x710a])
    def test_native_vm_failed_otg_does_not_flash(self):
        m=self.vm();m.write(PLAYING+4,0);seen=[]
        self.selector_hook(m,3,0xcc,lambda:seen.append(m.reg(1)) or 0)
        for atom in (actions.ADD,actions.REMOVE):self.assertEqual(m.run(atom,0),TRUE)
        self.assertEqual(seen,[])
    def test_native_selector_fallthrough(self):
        m=self.machine();seen=[]
        for name,original in (('actions_artist',0x1e969c),('actions_album',0x1e8e04)):
            m.hook(original,lambda original=original:seen.append((original,m.reg(0),m.reg(1))) or 37)
            self.assertEqual(m.call(self.symbols[name],UI,7),37)
        self.assertEqual(seen,[(0x1e969c,UI,7),(0x1e8e04,UI,7)])
    def test_browse_missing_and_empty_metadata_has_no_mutation(self):
        m=self.machine();before=bytes(m.cpu.mem_read(QUERY,0xd10))
        self.assertEqual(self.run_action(m,-3),0)
        metadata(m,'');self.assertEqual(self.run_action(m,-3),0)
        self.assertEqual(bytes(m.cpu.mem_read(QUERY,0xd10)),before)
    def test_browse_availability_only_does_not_reset(self):
        m=self.machine();metadata(m,'Álbum 🎵');before=bytes(m.cpu.mem_read(QUERY,0xd10))
        self.assertEqual(self.run_action(m,-2),1)
        self.assertEqual(bytes(m.cpu.mem_read(QUERY,0xd10)),before)
    def test_native_browse_reset_keeps_playback_and_copies_filter(self):
        for album,offset,mode in ((True,0x628,9),(False,0x228,5)):
            m=self.machine();metadata(m,'Café');playing=bytes(m.cpu.mem_read(PLAYING,0x30))
            m.write(QUERY+0x10,12,13,14,15)
            for off in (0xc88,0xc8c):m.write(QUERY+off,0x10019000+off)
            self.assertEqual(self.run_action(m,-3,album),1)
            self.assertEqual(bytes(m.cpu.mem_read(QUERY+offset,10)),b'\x04\x00C\x00a\x00f\x00\xe9\x00')
            self.assertEqual(bytes(m.cpu.mem_read(PLAYING,0x30)),playing)
            self.assertEqual(m.read(QUERY+0x10),0xffffffff)
            self.assertEqual(m.read(QUERY+0x18),0xffffffff)
            self.assertEqual(m.read(QUERY+0xc88),0)
            self.assertIn(0x10019000+0xc88,m.freed)
            self.assertEqual(m.cpu.mem_read(UI+0x48d,1)[0],mode)
    def test_browse_source_failure_does_not_install_filter(self):
        m=self.machine();metadata(m);m.write(QUERY+0xd00,0)
        self.assertEqual(self.run_action(m,-3),0)
        self.assertEqual(m.read(QUERY+0x628),0)
    def test_context_modes_and_stale_action_guard(self):
        m=self.machine();self.assertEqual(self.run_action(m,-5),0x7100)
        self.assertEqual(self.run_action(m,-7),0)
        m.write(PLAYING+4,OTG);m.write(ENTRY,OTG);m.write(SECOND,OTG)
        self.assertEqual(self.run_action(m,-5),0x7107)
        self.assertEqual(self.run_action(m,-6),0)
        self.assertEqual(m.events,[])
    def test_no_queue_invalid_index_and_special_entries(self):
        for offset,value in ((PLAYING+4,0),(PLAYING+0x14,2),(PLAYING+0x14,0xffffffff),(ENTRY+0x10,0)):
            m=self.machine();m.write(offset,value)
            self.assertEqual(self.run_action(m,-5),0x710a)
            self.assertEqual(self.run_action(m,-6),0)
        m=self.machine();m.cpu.mem_write(ENTRY+0x1d,b'\x01');self.assertEqual(self.run_action(m,-5),0x710a)
    def test_otg_capacity_and_allocation_failure_no_dirty(self):
        m=self.machine();m.cpu.mem_write(OTG_HEADER+0x32,struct.pack('<H',1000))
        self.assertEqual(self.run_action(m,-6),0)
        m.cpu.mem_write(OTG_HEADER+0x32,struct.pack('<H',999));m.hook(0xd1df0,lambda:0)
        self.assertEqual(self.run_action(m,-6),0)
        self.assertEqual(m.cpu.mem_read(PREFS+0xb76,1),b'\0');self.assertEqual(m.events,[])
    def test_native_add_clones_exact_shuffled_duplicate(self):
        m=self.machine();new=0x10016100;m.cpu.mem_write(QUEUE+0xec,b'\x01')
        def allocate_entry():
            self.assertEqual(m.reg(0),OTG);m.write(new,OTG);m.write(new+0x10,77);return new
        m.hook(0xd1df0,allocate_entry)
        m.hook(0xd6614,lambda:m.events.append(('insert',m.reg(0),m.reg(1),m.reg(2))))
        m.hook(0xb2bc4,lambda:m.events.append(('notify',m.reg(0),m.reg(1),m.reg(2))))
        self.assertEqual(self.run_action(m,-6),1)
        self.assertEqual(m.read(new+0x18),18) # SECOND exact entry, same underlying TRACK
        self.assertEqual(m.read(new+0x20),TRACK)
        self.assertEqual(m.events,[('insert',new,0,1),('notify',OTG+0x30,0x61706c69,new),('refresh-add',)])
        self.assertEqual(m.cpu.mem_read(PREFS+0xb76,1),b'\x01')
    def test_native_remove_notifies_before_free_and_unlinks_track(self):
        m=self.machine(True)
        m.hook(0xb2bc4,lambda:m.events.append(('notify',m.reg(1),m.reg(2),m.read(TRACK+0x24))))
        m.hook(0x96030,lambda:m.events.append(('free',m.reg(1),m.read(TRACK+0x24))))
        m.hook(0xad504,lambda:m.events.append(('reindex',m.reg(0))))
        self.assertEqual(self.run_action(m,-7),1)
        self.assertEqual(m.events,[('notify',0x64706c69,ENTRY,ENTRY),('free',ENTRY,SECOND),('reindex',OTG),('refresh-remove',)])
        self.assertEqual(m.read(TRACK+0x24),SECOND)
        self.assertEqual(m.cpu.mem_read(PREFS+0xb76,1),b'\x01')
    def test_native_remove_uses_current_index_when_track_changes(self):
        m=self.machine(True);m.write(PLAYING+0x14,1);seen=[]
        m.hook(0x95958,lambda:seen.append(m.reg(0)) or 0)
        m.hook(0xad504,lambda:0)
        self.assertEqual(self.run_action(m,-7),1);self.assertEqual(seen,[SECOND])
    def test_stock_toggle_resources_keep_native_cycle_command(self):
        rows=actions.records(self.updates['Menu'][0x7102])
        for row,prop in ((rows[2],0x6057),(rows[3],0x605b)):
            self.assertEqual(word(row,12),4320*4+2)
            self.assertEqual(word(row,28),0x60000000|prop)
            self.assertEqual(word(row,4),0x22)
    def test_patch_rejects_nonstock_binding(self):
        bad=bytearray(self.stock);struct.pack_into('<I',bad,0x4fcd68,0)
        with self.assertRaises(ValueError):actions.patch(bad,self.symbols)

    def history(self,m):
        history=0x10018000
        m.hook(0x25a974,m.allocate);m.hook(0x25a8f4,m.free)
        m.hook(0x13d080,lambda:0) # kernel ownership registration
        m.hook(0x1d2b6c,lambda:0) # app display snapshot outside isolated stack
        for off in (0x64,0x8c,0xb4):m.call(0x13b604,APP+off)
        m.write(0x1081da80,APP);m.call(0x13ca24,history);m.write(APP+0xe0,history)
        m.navigation=[]
        m.hook(0x140384,lambda:m.navigation.append(('dispatch',m.reg(0))) or 1)
        for selector in (0x71,0x72,0x75,0x7c,0x7d,0x7e,0x7f):
            self.selector_hook(m,2,selector,lambda selector=selector:m.navigation.append((selector,m.reg(1),m.reg(2),m.reg(3))) or 0)
        for selector in range(0x9d,0xe7):
            try:self.selector_hook(m,3,selector,lambda:0)
            except AssertionError:pass
        for selector in range(0x107,0x196):
            if selector not in (0x10f,0x111):self.selector_hook(m,33,selector,lambda:0)
        m.write(m.globals+4*(0x13a5-5010),0x5fdd*2+1)
        for atom in range(0x1398,0x13a4):m.write(m.globals+4*(atom-5010),FALSE)
        for atom in (0x13a6,0x13a7,0x13a0):m.write(m.globals+4*(atom-5010),FALSE)
        # Stock4102/4103 enter/suspend the Now Playing Timer controller.
        # Supply its scheduling boundary while executing the original scripts.
        m.write(0x1001d000,0x1001d100)
        m.write(0x1001d19c,0x1001d300,0x1001d304)
        m.hook(0x1001d300,lambda:0);m.hook(0x1001d304,lambda:0)
        m.bind(0x13a3,32,0x1001d000)
        m.write(0x1001a000,0);m.bind(0x13d5,56,0x1001a000);m.hook(0x248138,lambda:0)
        return history
    def history_chain(self,m,history):
        result=[];node=m.read(history+4)
        while node:
            result.append(m.read(node+0x24)//4);node=m.read(node+0x10)
            self.assertLess(len(result),20)
        return result
    def test_native_hold_center_open_consumes_release_and_keeps_home_back(self):
        m=self.vm();history=self.history(m)
        for atom in (4002,4244):m.call(0x1d27e8,APP,atom*4+2)
        self.assertEqual(m.run(4244,0),TRUE)
        self.assertIn((0x7c,2,0x6f,actions.OPEN*4+2),m.navigation)
        self.assertEqual(m.run(actions.OPEN,0),TRUE)
        self.assertEqual(self.history_chain(m,history),[actions.SCREEN,4244,4002])
        self.assertEqual(m.navigation[-1],(0x7c,3,0x6f,4008*4+2))
        # Native short Menu command pops one actual history node.
        self.assertEqual(m.run(3973,0),TRUE)
        self.assertEqual(self.history_chain(m,history),[4244,4002])
        self.assertEqual(m.run(4239,FALSE),TRUE)
        self.assertTrue(any(e[:3]==(0x7d,2,0x6f)for e in m.navigation))
        self.assertFalse(any(e[0]in (0x7c,0x7d)and e[1:3]==(2,0x6e)for e in m.navigation))
    def test_native_browse_replaces_old_query_history_then_menu_returns_actions(self):
        for command,leaf in ((actions.ALBUM,4164),(actions.ARTIST,4184)):
            m=self.vm();history=self.history(m);metadata(m,'Café')
            for atom in (4002,4202,4244,actions.SCREEN):m.call(0x1d27e8,APP,atom*4+2)
            self.assertEqual(m.run(command,0),TRUE)
            self.assertEqual(self.history_chain(m,history),[leaf,actions.SCREEN,4244,4002])
            self.assertEqual(m.run(3973,0),TRUE)
            self.assertEqual(self.history_chain(m,history),[actions.SCREEN,4244,4002])
            # Home's real native unwind frees the obsolete nodes.
            self.assertEqual(m.run(3969,0),TRUE)
            self.assertEqual(self.history_chain(m,history),[4002])
            self.assertTrue(m.freed)
    def test_native_browse_missing_metadata_stays_in_actions(self):
        m=self.vm();history=self.history(m)
        for atom in (4002,4244,actions.SCREEN):m.call(0x1d27e8,APP,atom*4+2)
        before=self.history_chain(m,history)
        self.assertEqual(m.run(actions.ALBUM,0),TRUE)
        self.assertEqual(self.history_chain(m,history),before)
    def test_native_browse_reuses_safe_initialized_parents(self):
        for command,leaf in ((actions.ALBUM,4164),(actions.ARTIST,4184)):
            m=self.vm();history=self.history(m);metadata(m,'Café')
            m.instruction_limit=1000000
            m.hooks.pop(0x140384) # Execute initial activation and cleanup too.
            m.call(0x1d27e8,APP,4002*4+2)
            self.assertEqual(m.run(4246),TRUE)
            self.assertEqual(m.run(actions.OPEN,0),TRUE)
            head=m.read(history+4)
            playing=bytes(m.cpu.mem_read(PLAYING,0x30))
            callbacks=[]
            def observe(cpu,pc,size,context):
                if pc==0x140384:callbacks.append(m.reg(0)//4)
            m.cpu.hook_add(UC_HOOK_CODE,observe)
            for _ in range(2):
                callbacks.clear()
                self.assertEqual(m.run(command,0),TRUE)
                # Re-entering NP/Home needlessly restarts their scripts and
                # timers. Already safe parents should remain suspended.
                self.assertEqual(callbacks,[leaf])
                self.assertEqual(self.history_chain(m,history),[leaf,actions.SCREEN,4244,4002])
                self.assertEqual(m.read(m.read(history+4)+0x10),head)
                self.assertNotIn(head,m.freed)
                self.assertEqual(bytes(m.cpu.mem_read(PLAYING,0x30)),playing)
                self.assertEqual(m.read(m.globals+4*(0x1394-5010)),FALSE)
                self.assertEqual(m.run(3973,0),TRUE)
                self.assertEqual(m.read(history+4),head)
            self.assertEqual(m.run(3973,0),TRUE)
            self.assertEqual(self.history_chain(m,history),[4244,4002])
            self.assertEqual(m.run(3969,0),TRUE)
            self.assertEqual(self.history_chain(m,history),[4002])
    def test_parent_reuse_guard_is_read_only_and_rejects_other_history(self):
        for parents in ((4002,4244,actions.SCREEN),
                        (4002,4202,4244,actions.SCREEN),
                        (4002,actions.SCREEN),(4244,actions.SCREEN),
                        (4202,4002,4244,actions.SCREEN)):
            m=self.vm();history=self.history(m)
            for atom in parents:m.call(0x1d27e8,APP,atom*4+2)
            before={history:bytes(m.cpu.mem_read(history,0x2c))}
            node=m.read(history+4)
            while node:
                before[node]=bytes(m.cpu.mem_read(node,0x30));node=m.read(node+0x10)
            self.assertEqual(self.run_action(m,-4),int(parents==(4002,4244,actions.SCREEN)))
            for address,data in before.items():
                self.assertEqual(bytes(m.cpu.mem_read(address,len(data))),data)
        # Restore an exact chain, then reject active mutations and nonstandard
        # node kinds rather than interpreting their layouts as script history.
        m=self.vm();history=self.history(m)
        for atom in (4002,4244,actions.SCREEN):m.call(0x1d27e8,APP,atom*4+2)
        nodes=[];node=m.read(history+4)
        while node:nodes.append(node);node=m.read(node+0x10)
        cases=[(0x1081da80,bytes(4)),(APP+0xe0,bytes(4)),
               (history,bytes(4)),(history+4,bytes(4)),(history+8,b'\x01')]
        for node in nodes:
            cases.extend(((node,bytes(4)),(node+0x1c,b'\x01'),(node+0x1d,b'\x01')))
        for address,value in cases:
            before=bytes(m.cpu.mem_read(address,len(value)))
            m.cpu.mem_write(address,value)
            self.assertEqual(self.run_action(m,-4),0)
            self.assertEqual(bytes(m.cpu.mem_read(address,len(value))),value)
            m.cpu.mem_write(address,before)
        self.assertEqual(self.run_action(m,-4),1)
    def test_reused_parents_survive_metadata_change_without_callbacks(self):
        for command,getter in ((actions.ALBUM,0x9f8a4),(actions.ARTIST,0x9f8f0)):
            m=self.vm();history=self.history(m);metadata(m,'Café')
            for atom in (4002,4244,actions.SCREEN):m.call(0x1d27e8,APP,atom*4+2)
            head=m.read(history+4);calls=[]
            get=m.hooks[getter]
            def fail_second_read():
                calls.append('metadata')
                return get() if len(calls)==1 else -1
            m.hook(getter,fail_second_read)
            m.navigation.clear()
            self.assertEqual(m.run(command,0),TRUE)
            self.assertEqual(calls,['metadata','metadata'])
            self.assertEqual(m.read(history+4),head)
            self.assertFalse(any(event[0]=='dispatch' for event in m.navigation))
            self.assertEqual(m.read(m.globals+4*(0x1394-5010)),FALSE)
    def test_reused_browse_preserves_drawing_mode_including_failures(self):
        for command,leaf,getter,view in ((actions.ALBUM,4164,0x9f8a4,0x5fa3),
                                         (actions.ARTIST,4184,0x9f8f0,0x5fa4)):
            for mode in (FALSE,TRUE):
                for outcome in ('success','missing','reread','source'):
                    with self.subTest(command=command,mode=mode,outcome=outcome):
                        m=self.vm();history=self.history(m);metadata(m,'Café')
                        for atom in (4002,4244,actions.SCREEN):
                            m.call(0x1d27e8,APP,atom*4+2)
                        head=m.read(history+4)
                        m.hooks.pop(0x140384)
                        for selector in (0xb3,0xb4,0xb6):
                            ptr,virtual=m.member(3,selector)
                            m.hooks.pop(m.read(0x66fa6c+ptr) if virtual else ptr,None)
                        m.hook(0x19ec64,lambda:RENDERER);m.hook(0x254620,lambda:0)
                        m.write(0x1081ec78,0x10);shown=[]
                        for offset in (0xe4,0xec):
                            m.hook(m.read(0x66fa6c+offset),lambda:shown.append(m.reg(1)) or 0)
                        drawing=m.globals+4*(0x1394-5010)
                        m.write(drawing,mode)
                        reads=[];get=m.hooks[getter]
                        def read_metadata():
                            reads.append(True)
                            if outcome=='missing' or (outcome=='reread' and len(reads)==2):return -1
                            return get()
                        m.hook(getter,read_metadata)
                        if outcome=='source':m.hook(0xb29b0,lambda:-1)
                        query=bytes(m.cpu.mem_read(QUERY,0xd10))
                        playing=bytes(m.cpu.mem_read(PLAYING,0x30))
                        writes=[];callbacks=[]
                        m.cpu.hook_add(UC_HOOK_MEM_WRITE,
                            lambda cpu,access,address,size,value,context:writes.append(value),
                            begin=drawing,end=drawing+3)
                        m.cpu.hook_add(UC_HOOK_CODE,
                            lambda cpu,pc,size,context:callbacks.append(m.reg(0)//4),
                            begin=0x140384,end=0x140384)
                        self.assertEqual(m.run(command,0),TRUE)
                        self.assertEqual(len(reads),1 if outcome=='missing' else 2)
                        self.assertEqual(m.read(drawing),mode)
                        self.assertEqual(m.read(RENDERER+0x10c),0)
                        self.assertEqual(bytes(m.cpu.mem_read(PLAYING,0x30)),playing)
                        if outcome=='success':
                            self.assertEqual(self.history_chain(m,history),[leaf,actions.SCREEN,4244,4002])
                            self.assertEqual(m.read(m.read(history+4)+0x10),head)
                            self.assertEqual(callbacks,[leaf])
                            self.assertEqual(shown,[view] if mode==FALSE else [])
                        else:
                            self.assertEqual(m.read(history+4),head)
                            self.assertEqual(bytes(m.cpu.mem_read(QUERY,0xd10)),query)
                            self.assertEqual(callbacks,[])
                            self.assertEqual(shown,[])
                        # Reused parents never draw: there is no reason to
                        # mutate and restore the global suppression mode.
                        self.assertEqual(writes,[])
    def test_native_browse_draws_only_destination_through_real_history_callbacks(self):
        for command,view in ((actions.ALBUM,0x5fa3),(actions.ARTIST,0x5fa4)):
            m=self.vm();history=self.history(m);metadata(m,'Café')
            # Actual nested activation/cleanup scripts need more than the
            # isolated-method budget. The probe completes in this bounded run.
            m.instruction_limit=1000000
            for atom in (4002,4202,4244,actions.SCREEN):m.call(0x1d27e8,APP,atom*4+2)
            m.hooks.pop(0x140384) # execute the native callback dispatcher
            for selector in (0xb3,0xb4,0xb6):
                ptr,virtual=m.member(3,selector)
                m.hooks.pop(m.read(0x66fa6c+ptr) if virtual else ptr,None)
            # Native render-mode conversion, locking and show/hide gating run;
            # only display lookup, capability and final drawing are supplied.
            m.hook(0x19ec64,lambda:RENDERER);m.hook(0x254620,lambda:0)
            m.write(0x1081ec78,0x10);shown=[]
            for offset in (0xe4,0xec):
                m.hook(m.read(0x66fa6c+offset),lambda:shown.append(m.reg(1)) or 0)
            self.assertEqual(m.run(command,0),TRUE)
            self.assertEqual(shown,[view])
            self.assertEqual(m.read(RENDERER+0x10c),0)
            self.assertEqual(m.read(m.globals+4*(0x1394-5010)),FALSE)
    def widget_menu(self,m,view):
        record=next(r for r in actions.records(self.updates['View'][0x5e15])if word(r,12)==view)
        root=m.construct(record);menu=m.children(m.children(root)[1])[0]
        m.call(0x23cb0c,menu)
        self.assertTrue(m.read(menu+0xa8)) # real row widget was constructed
        return menu
    def test_native_widgets_construct_every_context_and_resolve_actual_captions(self):
        from research_actions import Widgets
        for view,labels in ((0x7100,['Browse Album','Browse Artist','Shuffle','Repeat','Sleep Timer','Add to On-The-Go']),
                            (0x7107,['Browse Album','Browse Artist','Shuffle','Repeat','Sleep Timer','Remove from On-The-Go']),
                            (0x710a,['Browse Album','Browse Artist','Shuffle','Repeat','Sleep Timer'])):
            m=Widgets(self.image);menu=self.widget_menu(m,view)
            self.assertEqual(m.read(menu+0xb8),len(labels))
            for index,label in enumerate(labels):
                pointer=m.call(0x1bf740,m.item(menu,index))
                self.assertNotEqual(pointer,0)
                self.assertEqual(bytes(m.cpu.mem_read(pointer,64)).split(b'\0')[0].decode(),label)
            self.assertEqual(m.debug,[])
    def test_native_stock_cycle_script_reads_real_selected_menu_properties(self):
        from research_actions import VMWidgets,WIDGET_SETTINGS
        m=VMWidgets(self.image);menu=self.widget_menu(m,0x7100)
        m.write(RENDERER+0x28,menu);m.bind(0x1393,3,RENDERER);m.bind(0x13b3,35,WIDGET_SETTINGS)
        calls=[];m.hook(0x156ef4,lambda:calls.append(tuple(m.reg(i)for i in range(4))) or 0)
        for index,key in ((2,0x6057),(3,0x605b)):
            m.write(m.read(menu+0xa8)+0xa4,index)
            self.assertEqual(m.run(4320,0),TRUE)
            self.assertEqual(calls[-1],(WIDGET_SETTINGS,key,0x6000,0x60be))
            self.assertEqual(m.call(0x257260,menu),index)

    def test_native_sleep_picker_uses_sleep_setting_from_selected_action_row(self):
        from research_actions import VMWidgets,WIDGET_SETTINGS
        for view in (0x7100,0x7107,0x710a):
            m=VMWidgets(self.image);menu=self.widget_menu(m,view)
            m.write(APP,0x66fe10)
            history=self.history(m)
            self.selector_hook(m,2,0x70,lambda:0) # picker model publication
            # Restore the real selected-row property/group getters. Other
            # renderer calls are supplied while the stock picker scripts run.
            for selector in (0xc4,0xc5):
                ptr,virtual=m.member(3,selector)
                m.hooks.pop(m.read(0x66fa6c+ptr) if virtual else ptr,None)
            m.write(RENDERER+0x28,menu);m.bind(0x1393,3,RENDERER)
            m.bind(0x1392,2,APP);m.bind(0x13b3,35,WIDGET_SETTINGS)
            m.write(m.read(menu+0xa8)+0xa4,4)
            calls=[]
            m.hook(0x156ec4,lambda:calls.append((m.reg(1),m.reg(2))) or 0)
            self.assertEqual(m.run(4326,0),TRUE)
            self.assertEqual(calls,[(0x6081,0x6000)])
            self.assertEqual(self.history_chain(m,history),[4328])
            # Native duration selection must use the same captured setting.
            self.selector_hook(m,3,0xc4,lambda:30)
            writes=[];m.hook(0x156ef4,lambda:writes.append(tuple(m.reg(i)for i in (1,2,3))) or 0)
            self.assertEqual(m.run(4330,0),TRUE)
            self.assertEqual(writes,[(0x6081,0x6000,30)])

    def test_native_settings_widget_resolves_alarm_backlight_caption(self):
        from alarm_settings import resource_updates
        from research_actions import Widgets
        image=self.image
        if 0x7140 not in resources(image)['Str ']:
            working=bytearray(image);extend_resources(working,resource_updates(image));image=bytes(working)
        # Isolate the added record: unrelated stock Settings rows require other
        # device controllers and capability state absent from this fixture.
        at,size=resources(image)['Menu'][0x6016]
        added=[r for r in actions.records(image[at:at+size]) if word(r,24)==0x7140]
        self.assertEqual(len(added),1)
        working=bytearray(image)
        extend_resources(working,{'Menu':{0x6016:struct.pack('<I',1)+added[0]}})
        image=bytes(working)
        m=Widgets(image)
        at,size=resources(image)['View'][0x5e15]
        record=next(r for r in actions.records(image[at:at+size])if word(r,12)==0x6017)
        root=m.construct(record);menu=m.children(m.children(root)[1])[0];m.call(0x23cb0c,menu)
        matches=[]
        for index in range(m.read(menu+0xb8)):
            item=m.item(menu,index)
            if struct.unpack('<h',m.cpu.mem_read(m.read(item+0x10)+0x14,2))[0]==0x7140:
                pointer=m.call(0x1bf740,item)
                matches.append(bytes(m.cpu.mem_read(pointer,32)).split(b'\0')[0])
        self.assertEqual(matches,[b'Alarm Backlight'])

if __name__=='__main__':unittest.main(verbosity=2)
