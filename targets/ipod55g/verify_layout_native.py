"""Execute the native memory-arena setup and a linked writable-state fixture.

Run with JIT permitted. RAM-size detection and boot-state query are supplied;
the original 0xc90 arena calculations execute, not a host reimplementation.
"""
from pathlib import Path
import struct
import tempfile
import unittest

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn import arm_const as arm
from image import load_stock
import layout
from test_layout import FIXTURE, ROOT


class Machine:
    def __init__(self,data):
        self.cpu=c=Uc(UC_ARCH_ARM,UC_MODE_ARM)
        c.mem_map(0,0x800000);c.mem_write(0,data)
        c.mem_map(0x10000000,0x800000);c.mem_write(0x10000000,data)
        c.mem_map(0x02000000,0x20000)
        self.probes=[]
        def service(cpu,pc,size,user):
            if pc==0x82694:
                self.probes.append((cpu.reg_read(arm.UC_ARM_REG_R0),cpu.reg_read(arm.UC_ARM_REG_R1)))
                cpu.reg_write(arm.UC_ARM_REG_R0,1)
            elif pc==0x970:cpu.reg_write(arm.UC_ARM_REG_R0,0)
            else:raise AssertionError(f'native memory assertion at {pc:#x}')
            cpu.reg_write(arm.UC_ARM_REG_PC,cpu.reg_read(arm.UC_ARM_REG_LR))
        for pc in (0x82694,0x970,0x144cc8):c.hook_add(UC_HOOK_CODE,service,begin=pc,end=pc)

    def call(self,pc,r0=0):
        c=self.cpu
        c.reg_write(arm.UC_ARM_REG_SP,0x0201f000)
        c.reg_write(arm.UC_ARM_REG_LR,0x0201fff0)
        c.reg_write(arm.UC_ARM_REG_R0,r0)
        c.emu_start(pc,0x0201fff0,count=100000)
        if c.reg_read(arm.UC_ARM_REG_PC)!=0x0201fff0:raise AssertionError('call did not return')
        return c.reg_read(arm.UC_ARM_REG_R0)

    def arena(self):
        record=0x02000000
        self.cpu.mem_write(record,struct.pack('<4I',1,0,0,0))
        self.cpu.mem_write(record+0x20,struct.pack('<I',0x02000000))
        self.call(0xc90,record)
        return struct.unpack('<7I',self.cpu.mem_read(record+0x10,28))


class LayoutNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();directory=Path(cls.tmp.name)
        cls.stock=load_stock(ROOT/'iPod_25.1.3.ipsw')
        source=directory/'fixture.c';source.write_text(FIXTURE)
        elf=layout.link([source],directory)
        working,cls.symbols=layout.install_elf(cls.stock.osos,elf)
        cls.image=layout.finish(working)

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_native_arena_stays_above_extension(self):
        old,new=Machine(self.stock.osos),Machine(self.image)
        original,extended=old.arena(),new.arena()
        self.assertEqual(original,extended)
        self.assertEqual(extended[5],0x00a0e2a0)
        # The final alignment block stores the cached alias in +0x28.
        self.assertEqual(extended[6],0x10a0e2a0)
        marker=struct.unpack_from('<I',self.image,0xe8)[0]-4
        self.assertEqual(old.probes,[(0x4000000,0x7358c0)])
        self.assertEqual(new.probes,[(0x4000000,marker)])
        self.assertLess(self.symbols['counter'],0x10800000)

    def test_linked_arm_uses_loaded_cached_state_and_reboot_resets(self):
        m=Machine(self.image)
        self.assertEqual(m.call(self.symbols['next_value']),1)
        self.assertEqual(m.call(self.symbols['next_value']),2)
        self.assertEqual(struct.unpack('<I',m.cpu.mem_read(self.symbols['counter'],4))[0],2)
        self.assertEqual(Machine(self.image).call(self.symbols['next_value']),1)


if __name__=='__main__':unittest.main()
