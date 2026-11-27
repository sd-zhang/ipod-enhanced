"""Native 5.5G Pomodoro UI helpers, stock widgets and script integration."""
import datetime as dt
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from unicorn import arm_const as arm, UC_HOOK_CODE
from image import resources,word
from layout import link,install_elf,finish,extend_resources
from scripts import merge_scripts
from pomodoro_patch import apply as apply_runtime
from pomodoro_ui import apply as apply_ui,resource_updates,script_updates,records,sequence,VIEW,MENU,OPEN,SCREEN,TOGGLE,RESET,LEAVE,SELECT
from research_clock import stock_image,INPUT,OUTPUT,STOP,RUN,EXPIRED,TIME
from test_pomodoro_native import Runtime

SOURCE=Path(__file__).resolve().parent
OWNER,MENU_OBJECT,TASK,QUEUE,EVENT=0x10071000,0x10072000,0x10073000,0x10074000,0x10075000
BUTTON_PAYLOAD,BUTTON_DELTA=0x10076000,0x10076100


class UI(Runtime):
    def __init__(self,image,symbols):
        super().__init__(image,symbols)
        self.now=100000;self.notifications=[];self.fallback=[]
        self.write(OWNER,0x66fa6c)
        self.write(OWNER+0x0c,TASK)
        self.write(OWNER+0x28,MENU_OBJECT)
        self.write(MENU_OBJECT,0x66aa64)
        self.write(MENU_OBJECT+0x1c,MENU)
        self.write(TASK+0x1c,QUEUE)
        self.write(0x10800004,0)
        self.write(0x10800010,0xffffffff)
        self.hook(0x101800,lambda:TASK)
        self.hook(0xd6aac,lambda:TASK)
        self.hook(0xa0b10,lambda:self.now)
        for at in (0xc9a84,0xc9b58,0xa09b0,0xa0bcc,0xd1e70,0xc9a8c):self.hook(at,lambda:0)
        self.hook(0x21f37c,lambda:self.notifications.append((self.reg(1),self.reg(2))))
        self.hook(0x19e790,lambda:self.fallback.append(('select',self.reg(1))))
        self.hook(0x19f2e0,lambda:self.fallback.append(('event',self.reg(1))) or 7)
        self.resource_addresses={(int.from_bytes(t.encode(),'big'),k):a for t,items in resources(image).items() for k,(a,s) in items.items()}
        self.hook(0x21f3dc,lambda:self.resource_addresses.get((self.reg(0),self.reg(1)),0))
        def resource_property():
            self.fallback.append(('property',self.reg(1),self.reg(2)))
            pointer=self.resource_addresses.get((self.reg(1),self.reg(2)),0)
            self.write(self.reg(3),pointer)
            return int(bool(pointer))
        self.hook(0x1d021c,resource_property)
        self.cursor=0x10020000;self.allocations={}
        def alloc():
            old=0;size=self.reg(0)
            if self.cpu.reg_read(arm.UC_ARM_REG_PC)==0x11e6ac:old,size=self.reg(0),self.reg(1)
            p=self.cursor;self.cursor+=(size+15)&~15
            assert self.cursor<0x10060000
            self.allocations[p]=size
            if old:self.cpu.mem_write(p,bytes(self.cpu.mem_read(old,min(size,self.allocations.get(old,0)))))
            return p
        for at in (0x25a974,0x11ca20,0x11e6ac):self.hook(at,alloc)
        for at in (0x11ab70,0x25a8f4):self.hook(at,lambda:0)
        self.hook(0x1d3050,lambda:OWNER if self.reg(0) in (8,0x5e00) else 0)
        self.hook(0x13d480,lambda:0)
        self.hook(0x25aa40,lambda:0)  # external atexit registration for wheel registry
        self.hook(0x1c2510,lambda:self.now//1000)  # monotonic milliseconds
        # Native publication allocates from an external event pool. Keep native
        # event/payload constructors, observer lists and Renderer delivery live.
        self.hook(0x130090,lambda:0x10079000)
        def event_alloc():
            self.cpu.reg_write(arm.UC_ARM_REG_R0,16)
            return alloc()
        self.hook(0x218be4,event_alloc)

    @property
    def timer(self):return self.symbols['pomo_ui_timer']
    def command_ui(self,command):return self.call(self.read(0x66fb34),OWNER,command)
    def property(self,tag,key):
        result=self.call(self.read(0x66fac8),OWNER,tag,key,OUTPUT)
        return result,self.read(OUTPUT)
    def event(self):
        self.write(EVENT,0x677a28,9,self.timer)
        return self.call(self.read(0x66fa9c),OWNER,EVENT)
    def select_button(self,delta,key=MENU):
        self.write(EVENT,0x666728,16,BUTTON_PAYLOAD)
        self.write(BUTTON_PAYLOAD,0x66eb94,key,4,BUTTON_DELTA)
        self.write(BUTTON_DELTA,delta)
        return self.call(self.read(0x66fa9c),OWNER,EVENT)
    def text(self,key):
        ok,p=self.property(0x53747220,key);assert ok
        return bytes(self.cpu.mem_read(p,64)).split(b'\0')[0].decode()


    def tree(self):
        self.call(0x170874)
        _,roots=resource_updates(stock_image());self.cpu.mem_write(INPUT,roots[0][4:])
        root=self.call(0x21a4f4,0,0,INPUT,0x1d2310,0)
        self.write(root+0x58,0,0,216,320)
        children=[self.read(self.read(root+0x80)+i*4) for i in range(self.read(root+0x7c))]
        def layout(child):
            self.call(0x21a5e4,child,OUTPUT)
            self.cpu.mem_write(child+0x58,bytes(self.cpu.mem_read(OUTPUT,16)))
        for child in children:layout(child)
        group=children[-1]
        for i in range(self.read(group+0x7c)):
            layout(self.read(self.read(group+0x80)+i*4))
        # External enclosing-screen state, followed by native enable/focus
        # traversal. Do not assign the active control at Renderer+0x28.
        self.write(OWNER+0x18,root);self.write(OWNER+0x28,0)
        parent=0x1007a000
        self.write(parent,self.read(root));self.write(parent+0x20,0x800)
        self.write(root+0x10,parent)
        self.write(root+0x20,self.read(root+0x20)|0x1800)
        self.hook(0x21ac04,lambda:0);self.hook(0x21ab98,lambda:0)
        self.call(0x17de1c,root,1)
        self.call(0x19d024,OWNER,0,root,0)
        return root,children

    def direction_key(self,group,key):
        self.write(EVENT+0x10,key<<16)
        return self.call(0x16cc54,group,EVENT)

    def wheel(self,group,delta):
        self.write(EVENT,0x677aec,15,1,delta,0,0)
        return self.call(0x16cd4c,group,EVENT)

    def publish(self,accepted=0,after=None,timer=None):
        timer=timer or self.timer
        class Wait(Exception):pass
        waits=[];self.posts=[]
        def wait():
            if waits:raise Wait
            waits.append(1);return timer
        def post():
            self.posts.append((self.reg(1),bytes(self.cpu.mem_read(self.reg(2),28))))
            if after:after()
            return accepted
        self.hook(0xc27e0,wait);self.hook(0x1517d4,post)
        # The kernel worker has removed this expiry from its deadline list.
        self.write(0x10800004,0);self.write(timer+0x18,EXPIRED)
        try:self.call(0x1519e8)
        except Wait:pass
        else:raise AssertionError('timer worker did not wait again')


class PomodoroUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=TemporaryDirectory();directory=Path(cls.build.name)
        stub=directory/'alarm.c';stub.write_text('void ipod55g_alarm_signal(void) {}\n')
        elf=link([SOURCE/'pomodoro_runtime.c',SOURCE/'pomodoro_hooks.S',SOURCE/'pomodoro_ui.c',stub],directory,'pomo_boot')
        original=stock_image();image,cls.symbols=install_elf(original,elf)
        apply_runtime(image,cls.symbols);apply_ui(image,cls.symbols)
        updates,roots=resource_updates(original)
        a,s=resources(original)['View'][0x5e15]
        updates['View'][0x5e15]=sequence(records(original[a:a+s])+roots)
        updates['TCmd']={0x2380:merge_scripts(original,script_updates())}
        extend_resources(image,updates);cls.image=finish(image)

    @classmethod
    def tearDownClass(cls):cls.build.cleanup()

    def test_start_pause_reset_labels_and_native_timer_ownership(self):
        m=UI(self.image,self.symbols)
        m.command_ui(-0x5500)
        self.assertEqual(m.read(m.timer+0x20),OWNER)
        self.assertEqual(m.read(m.timer+0x24),QUEUE)
        self.assertEqual(m.text(0x7124),'Start')
        self.assertEqual(m.read(m.timer+0x1c),STOP)
        m.command_ui(-0x5502)
        self.assertEqual(m.text(0x7124),'Pause')
        self.assertEqual(m.read(m.timer+0x1c),RUN)
        m.command_ui(-0x5502)
        self.assertEqual(m.text(0x7124),'Start')
        self.assertEqual(m.read(m.timer+0x1c),STOP)
        m.command_ui(-0x5503)
        self.assertEqual(m.state(),(0,0,0,1500,0))

    def test_leave_stops_only_refresh_background_timer_keeps_running(self):
        m=UI(self.image,self.symbols)
        m.command_ui(-0x5500);m.command_ui(-0x5502)
        m.command_ui(-0x5501)
        self.assertEqual(m.read(m.timer+0x1c),STOP)
        self.assertEqual(m.state()[2],1)
        m.notifications.clear()
        self.assertEqual(m.event(),1)
        self.assertEqual(m.notifications,[])
        m.set_time(dt.datetime(2026,11,25,12,25));m.deliver()
        self.assertEqual(m.state()[:4],(1,0,1,300))
        m.command_ui(-0x5500)
        self.assertEqual(m.text(0x7122),'Break')
        self.assertEqual(m.read(m.timer+0x1c),RUN)

    def test_stale_refresh_after_pause_reset_or_screen_change_stays_stopped(self):
        for transition in ('pause','reset','screen'):
            with self.subTest(transition=transition):
                m=UI(self.image,self.symbols);m.command_ui(-0x5500);m.command_ui(-0x5502)
                if transition=='screen':m.write(OWNER+0x28,0)
                else:m.command_ui(-0x5502 if transition=='pause' else -0x5503)
                m.event()
                self.assertEqual(m.read(m.timer+0x1c),STOP)
                if transition=='screen':self.assertEqual(m.read(self.symbols['pomo_ui_visible']),0)

    def test_unrelated_renderer_dispatch_uses_original_subclass(self):
        m=UI(self.image,self.symbols);m.command_ui(0x5fba)
        self.assertEqual(m.fallback,[('select',0x5fba)])
        self.assertEqual(m.property(0x53747220,0x1234)[0],0)
        m.write(EVENT,0,1,0)
        self.assertEqual(m.call(m.read(0x66fa9c),OWNER,EVENT),7)

    def test_stock_property_dispatch_preserves_abi_with_bounded_overlay_cost(self):
        m=UI(self.image,self.symbols);calls=[];instructions=[0]
        def provider():
            calls.append(tuple(m.reg(i) for i in range(4)))
            m.write(m.reg(3),0x12345678)
            return 0x87654321
        m.hook(0x1d021c,provider)
        m.cpu.hook_add(UC_HOOK_CODE,lambda *args:instructions.__setitem__(0,instructions[0]+1))
        # Standard/unknown tags and signed resource IDs all retain the native
        # provider ABI. Budget counts only overlay dispatch and its boundary.
        # r7 costs 21..32 instructions per ordinary stock property request.
        for tag in (0x53747220,0x424d6170,0x44726177,0x4c737443,0,0xffffffff):
            for key in (0,0x1234,0x5e29,0x5fe9,0x618d,0x7100,0x7121,0x7129,0x7140,0xfffffedf,0xffffffff):
                with self.subTest(tag=hex(tag),key=hex(key)):
                    calls.clear();instructions[0]=0
                    self.assertEqual(m.property(tag,key),(0x87654321,0x12345678))
                    self.assertEqual(calls,[(OWNER,tag,key,OUTPUT)])
                    self.assertLessEqual(instructions[0],20)

    def test_native_stopwatch_countdown_string_tracks_rtc_pause_and_boundary(self):
        m=UI(self.image,self.symbols)
        self.assertEqual(m.text(0x7123),'25:00')
        m.command_ui(-0x5500);m.command_ui(-0x5502)
        m.set_time(dt.datetime(2026,11,25,12,24,59))
        self.assertEqual(m.text(0x7123),'00:01')
        m.set_time(dt.datetime(2026,11,25,12,25))
        self.assertEqual(m.text(0x7123),'00:00')
        m.deliver()
        self.assertEqual(m.text(0x7123),'05:00')
        self.assertEqual(m.text(0x7122),'Break')
        m.command_ui(-0x5502)
        m.set_time(dt.datetime(2026,11,25,12,40))
        self.assertEqual(m.text(0x7123),'05:00')
        m.command_ui(-0x5503)
        self.assertEqual(m.text(0x7123),'25:00')

    def test_screen_uses_stock_stopwatch_panel_digits_and_two_button_group(self):
        groups=resources(self.image)
        a,size=groups['View'][VIEW]
        rows=records(self.image[a:a+size])
        self.assertEqual([word(row,4) for row in rows],
                         [0x5b80,0x5200,0x5200,0x5200,0x1580,0x5c80,0x5b00])
        self.assertEqual(word(rows[0],12),0x5fe9)
        self.assertEqual([word(row,12) for row in rows[1:4]],[0x56b9,0x56b8,0x56ba])
        self.assertEqual(word(rows[5],12),0x7123)
        self.assertEqual(word(rows[5],0x54),0x003c5f51)
        self.assertEqual(word(rows[6],0x60),MENU)
        a,size=groups['View'][MENU]
        buttons=records(self.image[a:a+size])
        self.assertEqual([word(row,12) for row in buttons],[0x7127,0x7124,0x7128,0x7125])
        self.assertTrue(all(word(row,16)==0x5e00 for row in buttons))

    def test_native_buttons_select_and_activate_only_the_selected_action(self):
        m=UI(self.image,self.symbols);m.command_ui(-0x5500)
        def assets():return [m.property(0x424d6170,key) for key in (0x7127,0x7128)]
        blue=m.resource_addresses[(0x424d6170,0x56be)]
        silver=m.resource_addresses[(0x424d6170,0x56bb)]
        self.assertEqual(assets(),[(1,blue),(1,silver)])
        m.command_ui(-0x5504)
        self.assertEqual(m.state()[2],1)
        self.assertEqual(m.text(0x7124),'Pause')
        self.assertEqual(m.select_button(1),1)
        self.assertEqual(assets(),[(1,silver),(1,blue)])
        self.assertEqual(m.select_button(1),1)  # clamps at Reset
        self.assertEqual(assets(),[(1,silver),(1,blue)])
        m.command_ui(-0x5504)
        self.assertEqual(m.state()[:4],(0,0,0,1500))
        m.select_button(-1);m.select_button(-1)
        self.assertEqual(assets(),[(1,blue),(1,silver)])
        before=m.state()
        self.assertEqual(m.select_button(1,key=0x5680),7)
        self.assertEqual(m.state(),before)
        self.assertEqual(assets(),[(1,blue),(1,silver)])

    def test_native_widget_geometry_and_focus_select_the_button_group(self):
        m=UI(self.image,self.symbols);root,children=m.tree()
        self.assertEqual([tuple(m.read(c+0x58+i*4) for i in range(4)) for c in children],
                         [(0,0,216,320),(8,16,174,304),(8,8,174,16),
                          (8,304,174,312),(26,16,50,304),(68,86,128,234),
                          (182,0,214,320)])
        self.assertEqual([m.read(c+0x14) for c in children],[OWNER]*6+[0])
        group=children[-1]
        self.assertEqual(m.read(OWNER+0x28),group)
        self.assertEqual(m.call(0x19d3b0,OWNER,MENU),group)
        buttons=[m.read(m.read(group+0x80)+i*4) for i in range(m.read(group+0x7c))]
        self.assertEqual([tuple(m.read(c+0x58+i*4) for i in range(4)) for c in buttons],
                         [(182,53,214,150),(188,53,208,150),
                          (182,168,214,265),(188,168,208,265)])

    def test_native_direction_delivery_and_observer_lifecycle(self):
        m=UI(self.image,self.symbols);root,children=m.tree();group=children[-1]
        for _ in range(3):m.command_ui(-0x5500)
        self.assertEqual(m.call(0x149ec4,group,OWNER),1)
        m.notifications.clear()
        self.assertEqual(m.direction_key(group,0x58),1)
        self.assertEqual(m.read(self.symbols['pomo_ui_selected']),1)
        self.assertEqual(m.notifications,[(0x424d6170,0x7127),(0x424d6170,0x7128)])
        m.notifications.clear();m.direction_key(group,0x58)
        self.assertEqual(m.notifications,[])  # clamped, no redundant redraw
        self.assertEqual(m.direction_key(group,0x6f),0)  # Center uses its script
        for _ in range(3):m.command_ui(-0x5501)
        self.assertEqual(m.call(0x149ec4,group,OWNER),0)
        self.assertEqual(m.read(group+4),0)
        m.direction_key(group,0x57)
        self.assertEqual(m.read(self.symbols['pomo_ui_selected']),1)
        m.command_ui(-0x5500)
        self.assertEqual(m.read(self.symbols['pomo_ui_selected']),0)
        # Native focus can move before the leave callback. Lookup still detaches
        # the live owned group, without saving a pointer across screens.
        m.write(OWNER+0x28,0);m.command_ui(-0x5501)
        self.assertEqual(m.call(0x149ec4,group,OWNER),0)
        _,replacement=m.tree();new_group=replacement[-1]
        m.command_ui(-0x5500);m.direction_key(new_group,0x58)
        self.assertEqual(m.read(self.symbols['pomo_ui_selected']),1)
        m.direction_key(group,0x57)
        self.assertEqual(m.read(self.symbols['pomo_ui_selected']),1)
        m.command_ui(-0x5501)
        self.assertEqual(m.read(new_group+4),0)

    def test_native_wheel_quantization_selects_and_clamps_buttons(self):
        m=UI(self.image,self.symbols);_,children=m.tree();group=children[-1]
        m.command_ui(-0x5500)
        self.assertEqual(m.wheel(group,1),1)
        self.assertEqual(m.read(self.symbols['pomo_ui_selected']),0)
        self.assertEqual(m.wheel(group,1),1)
        self.assertEqual(m.read(self.symbols['pomo_ui_selected']),1)
        m.wheel(group,2)
        self.assertEqual(m.read(self.symbols['pomo_ui_selected']),1)
        m.wheel(group,-2)
        self.assertEqual(m.read(self.symbols['pomo_ui_selected']),0)
        m.command_ui(-0x5501);m.wheel(group,2)
        self.assertEqual(m.read(self.symbols['pomo_ui_selected']),0)

    def test_malformed_closed_and_unfocused_button_events_do_not_change_selection(self):
        for mode in ('short','null-data','zero','large','closed','unfocused','null-payload','foreign-type'):
            with self.subTest(mode=mode):
                m=UI(self.image,self.symbols);m.command_ui(-0x5500)
                m.select_button(1);m.notifications.clear();before=m.state()
                m.write(EVENT,0x666728,16,BUTTON_PAYLOAD)
                m.write(BUTTON_PAYLOAD,0x66eb94,MENU,4,BUTTON_DELTA)
                m.write(BUTTON_DELTA,-1)
                if mode=='short':m.write(BUTTON_PAYLOAD+8,0)
                if mode=='null-data':m.write(BUTTON_PAYLOAD+12,0)
                if mode in ('zero','large'):m.write(BUTTON_DELTA,0 if mode=='zero' else -2)
                if mode=='closed':m.command_ui(-0x5501)
                if mode=='unfocused':m.write(OWNER+0x28,0)
                if mode=='null-payload':m.write(EVENT+8,0)
                if mode=='foreign-type':m.write(EVENT+4,12)
                self.assertEqual(m.call(m.read(0x66fa9c),OWNER,EVENT),
                                 7 if mode in ('null-payload','foreign-type') else 1)
                self.assertEqual(m.read(self.symbols['pomo_ui_selected']),1)
                self.assertEqual(m.notifications,[])
                self.assertEqual(m.state(),before)
                if mode in ('closed','unfocused'):
                    m.command_ui(-0x5504);self.assertEqual(m.state(),before)

    def test_countdown_string_formats_bounds_and_phase_lengths(self):
        m=UI(self.image,self.symbols)
        for seconds,expected in ((0,'00:00'),(1,'00:01'),(9,'00:09'),(10,'00:10'),
                                 (59,'00:59'),(60,'01:00'),(300,'05:00'),(900,'15:00'),
                                 (1500,'25:00'),(1499,'24:59'),(1501,'25:00')):
            with self.subTest(seconds=seconds):
                m.write(self.symbols['pomo_state']+24,seconds)
                self.assertEqual(m.text(0x7123),expected)

    def test_native_digit_consumer_copies_text_and_draws_stock_atlas_crops(self):
        m=UI(self.image,self.symbols);_,children=m.tree();widget=children[5]
        m.hook(0x21a360,lambda:0)  # external redraw invalidation
        def chars():return ''.join(chr(m.call(0x256828,widget+0x7c,i)) for i in range(5))
        graphics,rect=0x10077000,0x10077100
        m.cpu.mem_write(graphics+0x10,b'\x07')
        m.cpu.mem_write(graphics+0x15,struct.pack('<I',0x12345678))
        m.cpu.mem_write(rect,bytes(m.cpu.mem_read(widget+0x58,16)))
        blits=[]
        def blit():
            blits.append((m.reg(1),struct.unpack('<4i',m.cpu.mem_read(m.reg(2),16)),
                          struct.unpack('<4i',m.cpu.mem_read(m.reg(3),16)),
                          m.read(graphics+0x15),m.cpu.mem_read(graphics+0x10,1)[0]))
        # Native atlas loading, glyph slicing and graphics state execute; stop
        # at the final framebuffer-copy boundary, not the digit draw itself.
        m.hook(0x210114,blit)
        def draw(text,sources):
            blits.clear();m.call(0x1abd78,widget,graphics,rect)
            self.assertEqual(chars(),text)
            self.assertEqual([b[1] for b in blits],[(0,l,60,r) for l,r in sources])
            self.assertEqual([b[2] for b in blits],
                             [(68,86,128,119),(68,119,128,152),(68,152,128,168),
                              (68,168,128,201),(68,201,128,234)])
            self.assertTrue(all(b[3:]==(0xffffffff,1) for b in blits))
            self.assertEqual(m.read(graphics+0x15),0x12345678)
            self.assertEqual(m.cpu.mem_read(graphics+0x10,1),b'\x07')
        m.call(0x1abcc4,widget)
        draw('25:00',[(66,99),(165,198),(330,346),(0,33),(0,33)])
        m.command(0);m.set_time(dt.datetime(2026,11,25,12,0,1))
        self.assertEqual(m.text(0x7123),'24:59')
        self.assertEqual(chars(),'25:00','widget retained mutable provider storage')
        m.call(0x1abcc4,widget)
        draw('24:59',[(66,99),(132,165),(330,346),(165,198),(297,330)])
        wrapper=m.read(widget+0x78)
        self.assertEqual(struct.unpack('<2H',m.cpu.mem_read(widget+0x74,4)),(0x5f51,60))
        self.assertEqual(struct.unpack('<H',m.cpu.mem_read(wrapper+4,2))[0],0x5f51)
        self.assertTrue(all(b[0]==m.read(wrapper+0xbc) for b in blits))

    def test_real_property_notifications_refresh_live_countdown_and_button_bitmaps(self):
        m=UI(self.image,self.symbols);_,children=m.tree();digit,group=children[-2:]
        buttons=[m.read(m.read(group+0x80)+i*4) for i in (0,2)]
        del m.hooks[0x21f37c]  # execute native subscription matching and delivery
        m.hook(0x21a360,lambda:0)  # external redraw invalidation
        def chars():return ''.join(chr(m.call(0x256828,digit+0x7c,i)) for i in range(5))
        def bitmaps():return [m.read(button+0x7c) for button in buttons]
        # The embedded native bitmap stores pixels after the 28-byte header.
        blue=m.resource_addresses[(0x424d6170,0x56be)]+28
        silver=m.resource_addresses[(0x424d6170,0x56bb)]+28
        m.command_ui(-0x5500)
        self.assertEqual(chars(),'25:00')
        self.assertEqual(bitmaps(),[blue,silver])
        m.command_ui(-0x5504)
        m.set_time(dt.datetime(2026,11,25,12,0,1));m.event()
        self.assertEqual(chars(),'24:59')
        m.command_ui(-0x5504)  # pause
        m.set_time(dt.datetime(2026,11,25,13));m.event()
        self.assertEqual(chars(),'24:59')
        m.command_ui(-0x5501);m.command_ui(-0x5500)
        self.assertEqual(chars(),'24:59')
        m.direction_key(group,0x58)
        self.assertEqual(bitmaps(),[silver,blue])
        m.command_ui(-0x5504)
        self.assertEqual(chars(),'25:00')
        m.direction_key(group,0x57);m.command_ui(-0x5504)
        m.set_time(dt.datetime(2026,11,25,13,25));m.deliver();m.event()
        self.assertEqual(chars(),'05:00')

    def test_rejected_native_publication_rearms_after_worker_cleanup(self):
        m=UI(self.image,self.symbols);m.command_ui(-0x5500);m.command_ui(-0x5502)
        m.publish()
        self.assertEqual(m.read(m.timer+0x18),RUN)
        self.assertEqual(m.read(m.timer+0x1c),RUN)
        self.assertEqual(m.read(0x10800004),m.timer)
        self.assertEqual(m.posts,[(QUEUE,struct.pack('<7I',TIME,m.now,0,m.timer,0,0,0))])

    def test_rejected_publication_does_not_restart_closed_paused_reset_or_newer_timer(self):
        for transition in ('closed','paused','reset','newer'):
            with self.subTest(transition=transition):
                m=UI(self.image,self.symbols);m.command_ui(-0x5500);m.command_ui(-0x5502)
                def race():
                    if transition=='closed':m.write(self.symbols['pomo_ui_visible'],0)
                    if transition in ('paused','reset'):m.write(self.symbols['pomo_state']+20,0)
                    if transition!='newer':m.write(m.timer+0x1c,STOP)
                if transition=='newer':
                    def unlock():
                        if m.reg(0)==0x10800014 and m.read(m.timer+0x18)==STOP:
                            m.write(m.timer+0x18,RUN,RUN);m.write(m.timer+8,1234567)
                    m.hook(0xc9b58,unlock)
                m.publish(after=race)
                if transition=='newer':
                    self.assertEqual(m.read(m.timer+8),1234567)
                    self.assertEqual(m.read(m.timer+0x18),RUN)
                else:self.assertEqual(m.read(m.timer+0x18),STOP)
                self.assertEqual(m.read(0x10800004),0)

    def test_successful_publication_waits_for_ui_and_unrelated_timer_stays_native(self):
        m=UI(self.image,self.symbols);m.command_ui(-0x5500);m.command_ui(-0x5502)
        m.publish(accepted=1)
        self.assertEqual(m.read(m.timer+0x18),STOP)
        self.assertEqual(m.read(m.timer+0x1c),EXPIRED)
        self.assertEqual(m.read(0x10800004),0)
        m.event();self.assertEqual(m.read(m.timer+0x18),RUN)
        other=0x10077000
        m.call(0x13d7c8,OWNER,other,0,0)
        m.publish(timer=other)
        self.assertEqual(m.read(other+0x18),STOP)
        self.assertEqual(m.read(other+0x1c),EXPIRED)
        self.assertEqual(struct.unpack('<4I',m.posts[0][1][:16]),(TIME,m.now,0,other))

    def test_native_vm_screen_keeps_play_and_installs_leave_callbacks(self):
        from research_actions import ScriptVM,CLASS_BASE,MEMBER_BASE
        m=UI(self.image,self.symbols)
        m.tree()  # real allocations/widgets before invoking native VM
        alloc=m.hooks[0x25a974]
        m.hook(0x120cbc,alloc);m.hook(0x11c794,lambda:0)
        m.call(0x25aea0)
        self.assertEqual(m.call(0xd6424,0x106468,8192),1)
        self.assertEqual(m.call(0xfc270),1);self.assertEqual(m.call(0xe0d80),1)
        a,size=resources(self.image)['TCmd'][0x2380];m.call(0x106280,a,size)
        m.root=m.read(0x108c880c);m.globals=m.read(0x10822ac0)
        m.object=lambda typ,raw:ScriptVM.object(m,typ,raw)
        app=0x10078000;m.write(app,0x66fe10)
        ScriptVM.bind(m,5010,2,app);ScriptVM.bind(m,5011,3,OWNER)
        for atom in range(5012,5016):m.write(m.globals+4*(atom-5010),0xc0000000)
        calls=[]
        for typ,selector,vt in ((2,0x7c,0x66fe10),(2,0x8b,0x66fe10),(2,0x8c,0x66fe10),(3,0xb6,0x66fa6c)):
            fields=struct.unpack_from('<17H',self.image,CLASS_BASE+34*typ)
            slot=MEMBER_BASE+8*(fields[14]-fields[15]+selector-fields[9])
            ptr,virtual=m.read(slot),m.read(slot+4)
            m.hook(m.read(vt+ptr) if virtual else ptr,
                   lambda selector=selector:calls.append((selector,m.reg(1),m.reg(2),m.reg(3))) or 0)
        self.assertEqual(ScriptVM.run(m,SCREEN,0),0xc0000002)
        self.assertIn((0x7c,3,0x6f,4017*4+2),calls) # stock Center binding, overridden below
        self.assertIn((0x7c,3,0x6e,3973*4+2),calls) # stock Menu navigation
        self.assertIn((0x7c,2,0x10,4220*4+2),calls) # Play remains music
        self.assertEqual([c for c in calls if c[:3]==(0x7c,3,0x6f)][-1],
                         (0x7c,3,0x6f,SELECT*4+2))
        self.assertIn((0xb6,VIEW,0,0),calls)
        for selector in (0x8b,0x8c):self.assertIn((selector,LEAVE*4+2),[c[:2] for c in calls])
        self.assertEqual(m.read(self.symbols['pomo_ui_visible']),1)
        ScriptVM.run(m,SELECT,0);self.assertEqual(m.state()[2],1)
        m.select_button(1)
        ScriptVM.run(m,SELECT,0);self.assertEqual(m.state()[:4],(0,0,0,1500))
        ScriptVM.run(m,LEAVE,0);self.assertEqual(m.read(self.symbols['pomo_ui_visible']),0)

    def test_clock_menus_preserve_exact_stock_payloads(self):
        original=stock_image()
        for key in (0x6181,0x618e):
            with self.subTest(menu=hex(key)):
                a,s=resources(original)['Menu'][key];old=original[a:a+s]
                a,s=resources(self.image)['Menu'][key]
                self.assertEqual(self.image[a:a+s],old)

    def test_extras_inserts_pomodoro_below_stopwatch_preserving_all_stock_rows(self):
        original=stock_image()
        a,s=resources(original)['Menu'][0x5f75];old=records(original[a:a+s])
        a,s=resources(self.image)['Menu'][0x5f75];new=records(self.image[a:a+s])
        self.assertEqual(len(new),len(old)+1)
        added=[i for i,row in enumerate(new) if word(row,12)==5111*4+2]
        self.assertEqual(len(added),1)
        index=added[0]
        self.assertEqual(new[:index]+new[index+1:],old)
        self.assertEqual(struct.unpack_from('<h',new[index-1],24)[0],0x5f85)
        self.assertEqual(struct.unpack_from('<h',new[index],24)[0],0x7120)
        self.assertEqual(struct.unpack_from('<h',new[index],32)[0],0x7120)

    def test_native_extras_widget_resolves_pomodoro_action_and_stock_submenu_arrow(self):
        from research_actions import Widgets,RENDERER,WIDGET_SETTINGS
        a,s=resources(self.image)['Menu'][0x5f75]
        selected=[r for r in records(self.image[a:a+s])
                  if struct.unpack_from('<h',r,24)[0] in (0x5f85,0x7120)]
        self.assertEqual(len(selected),2)
        # Other Extras entries need device controllers. Keep the actual Extras
        # hierarchy, stock Stopwatch, and Pomodoro in their emitted order.
        image=bytearray(self.image)
        extend_resources(image,{'Menu':{0x5f75:sequence(selected)}})
        image=bytes(image);m=Widgets(image)
        # Provider 8 is the native resource-file registry entry used by stock
        # submenu arrows. Only this external registry boundary is supplied;
        # the factory, item flags and secondary bitmap getter execute ARM.
        m.hook(0x1d3050,lambda:{8:RENDERER,0x5e00:RENDERER,0x6000:WIDGET_SETTINGS}.get(m.reg(0),0))
        a,s=resources(image)['View'][0x5e15]
        record=next(r for r in records(image[a:a+s]) if word(r,12)==0x5f71)
        root=m.construct(record);menu=m.children(m.children(root)[1])[0]
        m.call(0x23cb0c,menu)
        self.assertEqual(m.read(menu+0xb8),2)
        arrow,size=resources(image)['BMap'][0x5ecc]
        for index,caption,action in ((0,b'Stopwatch',4919*4+2),(1,b'Pomodoro',OPEN*4+2)):
            with self.subTest(caption=caption):
                item=m.item(menu,index);vtable=m.read(item)
                pointer=m.call(m.read(vtable+0x18),item)
                self.assertEqual(bytes(m.cpu.mem_read(pointer,32)).split(b'\0')[0],caption)
                self.assertEqual(m.call(m.read(vtable+0x34),item),action)
                bitmap=m.call(m.read(vtable+0x2c),item)
                self.assertNotEqual(bitmap,0,'native item suppressed the submenu indicator')
                self.assertEqual(bytes(m.cpu.mem_read(bitmap,size)),image[arrow:arrow+size])
        self.assertEqual(m.debug,[])


if __name__=='__main__':unittest.main(verbosity=2)
