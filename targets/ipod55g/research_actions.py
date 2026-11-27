"""Characterize stock 5.5G actions; native ARM/VM, no firmware/device writes.

Unicorn needs JIT permission. Run with the repository .venv outside its sandbox.
OS allocation, graphics and event boundaries are supplied, not device evidence.
"""
import argparse
import hashlib
import json
import struct
import unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn import arm_const as arm
from image import load_stock, resources

REG = [getattr(arm, f'UC_ARM_REG_R{i}') for i in range(13)]
APP, RENDERER, UI, QUERY, PREFS, OUT = (0x10001000,0x10003000,0x10005000,0x10007000,0x10009000,0x10010000)
STACK, RETURN = 0x100fe000,0x100ff000
CLASS_BASE, MEMBER_BASE = 0x4fee68,0x10821690

class Machine:
    def __init__(self, image):
        self.image=image
        self.cpu=Uc(UC_ARCH_ARM,UC_MODE_ARM)
        self.cpu.mem_map(0,0x800000)
        self.cpu.mem_write(0,image)
        self.cpu.mem_map(0x10000000,0x100000)
        self.cpu.mem_map(0x10700000,0x100000)
        self.cpu.mem_write(0x10700000,image[0x700000:])
        self.cpu.mem_map(0x10800000,0x100000)
        for entry in range(0x84058,0x840d0,20):
            source,dest,size=struct.unpack_from('<3I',image,entry)
            if 0x10800000<=dest<0x10900000:
                source &=0x3ffffff
                self.cpu.mem_write(dest,image[source:source+size])
        self.hooks={}
        self.cpu.hook_add(UC_HOOK_CODE,self.dispatch)
        self.cursor=0x10020000
        self.allocations={}
        self.freed=[]
        for addr in (0x120cbc,0x11e6ac):self.hook(addr,self.allocate)
        for addr in (0x11c794,0x11ab70):self.hook(addr,self.free)
    def reg(self,n):return self.cpu.reg_read(REG[n])
    def read(self,a):return struct.unpack('<I',self.cpu.mem_read(a,4))[0]
    def write(self,a,*v):self.cpu.mem_write(a,struct.pack(f'<{len(v)}I',*(x&0xffffffff for x in v)))
    def hook(self,a,f):self.hooks[a]=f
    def dispatch(self,cpu,pc,size,context):
        if pc in self.hooks:
            value=self.hooks[pc]()
            if value is not None:cpu.reg_write(REG[0],value&0xffffffff)
            cpu.reg_write(arm.UC_ARM_REG_PC,cpu.reg_read(arm.UC_ARM_REG_LR))
    def allocate(self):
        size=self.reg(0)
        if self.cpu.reg_read(arm.UC_ARM_REG_PC)==0x11e6ac:
            assert size==0
            size=self.reg(1)
        at=self.cursor;self.cursor+=(size+15)&~15
        assert self.cursor<0x100d0000,(size,hex(self.cursor))
        self.allocations[at]=size
        return at
    def free(self):self.freed.append(self.reg(0))
    def call(self,address,*args):
        for r in REG:self.cpu.reg_write(r,0)
        for r,value in zip(REG,args[:4]):self.cpu.reg_write(r,value&0xffffffff)
        if len(args)>4:self.write(STACK,*args[4:])
        self.cpu.reg_write(arm.UC_ARM_REG_SP,STACK)
        self.cpu.reg_write(arm.UC_ARM_REG_LR,RETURN)
        try:self.cpu.emu_start(address,RETURN,count=getattr(self,'instruction_limit',300000))
        except Exception as e:
            raise AssertionError(f'ARM failure at PC={self.cpu.reg_read(arm.UC_ARM_REG_PC):#x}, entry={address:#x}') from e
        assert self.cpu.reg_read(arm.UC_ARM_REG_PC)==RETURN,'instruction limit'
        assert self.cpu.reg_read(arm.UC_ARM_REG_SP)==STACK,'unbalanced stack'
        return self.reg(0)
    def member(self,typ,selector):
        fields=struct.unpack_from('<17H',self.image,CLASS_BASE+34*typ)
        assert fields[9]<=selector<fields[10]
        slot=MEMBER_BASE+8*(fields[14]-fields[15]+selector-fields[9])
        return self.read(slot),self.read(slot+4)

class ScriptVM(Machine):
    def __init__(self,image):
        super().__init__(image)
        self.call(0x25aea0)
        assert self.call(0xd6424,0x106468,8192)==1
        assert self.call(0xfc270)==1
        assert self.call(0xe0d80)==1
        offset,size=resources(image)['TCmd'][0x2380]
        self.call(0x106280,offset,size)
        self.root=self.read(0x108c880c)
        self.globals=self.read(0x10822ac0)
    def object(self,typ,raw):
        obj=self.call(0xc81b0,CLASS_BASE+34*typ,self.root,self.read(0x108c8810))
        self.write(obj+0x2c,raw|1)
        return obj
    def bind(self,atom,typ,raw):
        obj=self.object(typ,raw)
        self.call(0x15ffd8,self.globals+4*(atom-5010),obj)
        return obj
    def run(self,atom,*args):
        assert self.call(0xd6220,atom*4+2,OUT,OUT+4)==1
        if args:self.write(OUT+0x100,*args)
        assert self.call(0xd466c,self.root,self.read(OUT),len(args),OUT+0x100,OUT+8)==1
        return self.read(OUT+8)

class NativeActions(unittest.TestCase):
    image=None
    def test_native_binding_initializer(self):
        m=Machine(self.image);m.call(0x25aea0)
        for selector,expected in ((0x108,0x1e7b98),(0x10f,0x1e969c),(0x111,0x1e8e04),(0x117,0x1eee48),(0x119,0x1ee56c),(0x124,0x1e7628)):
            self.assertEqual(m.member(33,selector),(expected,0))
    def test_stock_vm_simple_script(self):
        m=ScriptVM(self.image)
        self.assertEqual(m.run(4014),0xc0000002) # stock true literal, no OS services

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock',type=Path,default=Path('iPod_25.1.3.ipsw'))
    args=parser.parse_args()
    NativeActions.image=load_stock(args.stock).osos
    unittest.main(argv=[__file__],verbosity=2)

# Reusable display boundary for stock View/Menu factory characterization.
# The framebuffer/display ancestor, allocation and resource registry are supplied;
# class factories, resource recursion, menu models and item objects execute ARM.
WIDGET_TASK,WIDGET_SETTINGS,WIDGET_INPUT=0x10019000,0x1001c000,0x1001a000
class WidgetFixture:
    def __init__(self,image):
        super().__init__(image)
        self.hook(0x101800,lambda:WIDGET_TASK)
        self.hook(0x255b18,lambda:0x1001b000) # enclosing display ancestor
        self.write(0x1001b0a4,0x1001b400);self.write(0x1001b480,0x1001b800)
        self.cpu.mem_write(0x1001b814,b'\x02\x00')
        self.write(RENDERER,0x66fa6c)
        self.write(WIDGET_SETTINGS,0x66c8f0);self.write(WIDGET_SETTINGS+0x98,QUERY)
        self.write(QUERY+0xca0,PREFS);self.call(0xf3dc0,PREFS+8)
        self.hook(0x1d3050,lambda:{0x5e00:RENDERER,0x6000:WIDGET_SETTINGS}.get(self.reg(0),0))
        self.hook(0x13d480,lambda:0)
        self.addresses={(int.from_bytes(t.encode(),'big'),k):a for t,rs in resources(image).items()for k,(a,s)in rs.items()}
        self.hook(0x21f3dc,lambda:self.addresses.get((self.reg(0),self.reg(1)),0))
        def provider():
            pointer=self.addresses.get((self.reg(1),self.reg(2)&0xffffffff),0)
            if not pointer:pointer=self.addresses.get((self.reg(1),self.reg(2)&0xffff),0)
            self.write(self.reg(3),pointer)
            return int(bool(pointer))
        self.hook(0x1d021c,provider) # enclosing resource-file provider
        for at in (0x25a974,0x11ca20,0x11e6ac):self.hook(at,self.widget_allocate)
        for at in (0x11ab70,0x25a8f4):self.hook(at,self.free)
        self.debug=[];self.hook(0x26c0ac,lambda:self.debug.append(chr(self.reg(0)&255)) or self.reg(0))
        self.call(0x170874)
    def widget_allocate(self):
        old=0;size=self.reg(0)
        if self.cpu.reg_read(arm.UC_ARM_REG_PC)==0x11e6ac:old,size=self.reg(0),self.reg(1)
        at=self.cursor;self.cursor+=(size+15)&~15
        assert self.cursor<0x100d0000
        self.allocations[at]=size
        if old:self.cpu.mem_write(at,bytes(self.cpu.mem_read(old,min(size,self.allocations[old]))))
        return at
    def construct(self,record):
        self.cpu.mem_write(WIDGET_INPUT,record[4:])
        return self.call(0x21a4f4,RENDERER,0,WIDGET_INPUT,0x1d2310,0)
    def children(self,obj):
        count,array=self.read(obj+0x7c),self.read(obj+0x80)
        assert count<20
        return tuple(self.read(array+4*i)for i in range(count))
    def item(self,menu,index):return self.call(0x2183ec,self.read(menu+0x144),index)

class Widgets(WidgetFixture,Machine):pass
class VMWidgets(WidgetFixture,ScriptVM):pass
