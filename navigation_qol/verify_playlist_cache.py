"""4G native cache ownership and queue-copy equivalence; no device timing.

Only heap/storage services are supplied. Native clone, linking, cleanup,
query filtering and listener dispatch execute the firmware under test.
"""
import argparse
import json
import random
import struct
import unittest
from pathlib import Path

from unicorn import UC_HOOK_CODE
from unicorn import arm_const as arm
from native_fixture import NativeMachine, Library, load_image, QUERY, DB, PLAYLIST

QUEUE, HEADER, STORAGE, CLONES = 0x10015000, 0x10016000, 0x10017000, 0x11080000
BASES = (0x12c, 0x1c8, 0x264, 0x300)
SPECIAL = {6: 0x3a0, 7: 0x39c, 9: 0x3a4, 24: 0x3a8}


def original(image):
    data = bytearray(image)
    struct.pack_into('<I', data, 0x53478, 0xe92d4038)
    return bytes(data)


def counted(m, address, *args):
    count = [0]
    handle = m.cpu.hook_add(UC_HOOK_CODE, lambda *a: count.__setitem__(0, count[0]+1))
    result = m.call(address, *args)
    m.cpu.hook_del(handle)
    return result, count[0]


def cache_machine(image, values=None, callback=None):
    m = NativeMachine(image)
    for at, value in (values or {}).items(): m.write(QUEUE+at, value)
    m.destroyed = []
    def destroy():
        value = m.reg(0)
        m.destroyed.append(value)
        if callback: callback(m, value)
        return 0xface0123
    m.hook(0x9b6fc, destroy)
    return m


def queue_machine(image, count, reverse=False, album='', fail_at=None):
    m = Library(image, [])
    m.instruction_limit = 20000000
    m.cpu.mem_map(0x11000000, 0x400000)
    m.cpu.mem_map(0x12000000, 0x400000)
    source_header = m.read(PLAYLIST+0x24)
    m.cpu.mem_write(source_header+0x2e, struct.pack('<H', count))
    for i in range(count):
        entry, track, string = 0x11000000+i*64, 0x12000000+i*256, 0x11300000+i*64
        name = 'Alpha' if i%2 == 0 else 'Beta'
        raw = name.encode('utf-16le')
        m.cpu.mem_write(string, raw)
        m.strings[i+1] = (string, raw, name)
        m.write(entry, PLAYLIST)
        m.write(entry+0x10, i+1)
        m.write(entry+0x20, track)
        m.write(track, DB)
        for offset in (0x28, 0x30, 0x38, 0x3c): m.write(track+offset, i+1)
        m.wrappers.append(entry)
    if album:
        raw = album.encode('utf-16le')
        m.cpu.mem_write(QUERY+0x670, struct.pack('<H',len(album))+raw)
    m.write(QUEUE+0xc, DB)
    m.write(PLAYLIST+0xc, DB)
    m.write(QUEUE+8, STORAGE)
    m.write(QUEUE+0x24, HEADER)
    m.write(HEADER, QUEUE)
    # Native notifier and listener constructors; callback from query constructor.
    assert m.call(0x4a8dc, QUEUE+0x2c) == 0
    assert m.call(0x4a888, QUERY+4, 0x4df74, QUERY) == 0
    assert m.call(0x97650, QUEUE+0x2c, QUERY+4) == 0
    m.allocations_attempted, m.notifications, m.dirty = [], [], []
    def allocate():
        assert m.reg(0) == QUEUE
        index = len(m.allocations_attempted)
        m.allocations_attempted.append(index)
        if index == fail_at: return 0
        at = CLONES+index*64
        m.write(at, QUEUE)
        m.write(at+0x10, 10000+index)
        return at
    m.hook(0x9dbcc, allocate)
    m.hook(0xcdde8, lambda: m.dirty.append((m.reg(0),m.reg(1))))
    # The disk index is supplied; traversal/filter/clone remain native.
    def lookup():
        assert m.reg(0) == PLAYLIST and m.reg(1) == 1
        index = count-1-m.reg(3) if m.reg(2) else m.reg(3)
        return m.wrappers[index]
    m.hook(0x6e400, lookup)
    def notified(cpu, pc, size, context):
        m.notifications.append((m.reg(0),m.reg(1)))
    m.cpu.hook_add(UC_HOOK_CODE, notified, begin=0x4df74, end=0x4df74)
    m.copy_args = (QUERY, PLAYLIST, 1, int(reverse), QUEUE)
    return m


def copied(m):
    order, node = [], m.read(HEADER+0x20)
    while node:
        assert len(order) < 65536
        order.append(m.read(node+0x18))
        node = m.read(node+8)
    assert len(order) == struct.unpack('<H',m.cpu.mem_read(HEADER+0x2e,2))[0]
    return order


class PlaylistCache(unittest.TestCase):
    def compare(self, values, callback=None):
        machines = [cache_machine(image, values, callback) for image in (original(self.image),self.image)]
        results = [counted(m,0x53478,QUEUE)[0] for m in machines]
        self.assertEqual(results[0],results[1])
        self.assertEqual(machines[0].destroyed,machines[1].destroyed)
        self.assertEqual(bytes(machines[0].cpu.mem_read(QUEUE,0x400)),bytes(machines[1].cpu.mem_read(QUEUE,0x400)))
        return machines[1]

    def test_empty_cache_work_is_reduced(self):
        costs=[]
        for image in (original(self.image),self.image):
            m=cache_machine(image)
            result,cost=counted(m,0x53478,QUEUE)
            self.assertEqual(result,0)
            self.assertEqual(m.destroyed,[])
            costs.append(cost)
        self.assertLess(costs[1],costs[0]*0.65,costs)

    def test_each_owned_slot_and_adjacent_guards(self):
        guards={at:0xabad0000+at for at in (0,8,*BASES,0x3ac,0x3fc)}
        for at in [b+4*i for i in range(1,39) for b in BASES]+list(SPECIAL.values()):
            with self.subTest(offset=hex(at)):
                value=0x13000000+at
                m=self.compare({**guards,at:value})
                self.assertEqual(m.destroyed,[value])
                self.assertEqual(m.read(QUEUE+at),0)

    def test_sparse_full_and_mutating_cleanup(self):
        slots=[b+4*i for i in range(1,39) for b in BASES]+list(SPECIAL.values())
        rng=random.Random(4035)
        for mask in ([True]*len(slots),*[[rng.randrange(4)==0 for _ in slots] for _ in range(16)]):
            self.compare({at:0x13000000+at for at,present in zip(slots,mask) if present})
        def mutate(m,value):
            if value==0x13000000:
                for at,v in ((0x1c8+12,0x13000004),(0x12c+20,0x13000008),
                             (0x3a8,0x1300000c),(0x12c+4,0x13000010)):
                    m.write(QUEUE+at,v)
        m=self.compare({0x12c+12:0x13000000},mutate)
        self.assertEqual(m.destroyed,[0x13000000,0x13000004,0x13000008,0x1300000c])
        self.assertEqual(m.read(QUEUE+0x12c+4),0x13000010)

    def test_callee_saved_registers_and_final_native_return(self):
        m=cache_machine(self.image,{0x300+38*4:0x13000000})
        regs=[getattr(arm,f'UC_ARM_REG_R{i}') for i in range(4,12)]
        vals=[0xabba0000+i for i in range(8)]
        def seed(cpu,pc,size,ctx):
            for reg,val in zip(regs,vals): cpu.reg_write(reg,val)
        m.cpu.hook_add(UC_HOOK_CODE,seed,begin=0x53478,end=0x53478)
        self.assertEqual(m.call(0x53478,QUEUE),0xface0123)
        self.assertEqual([m.cpu.reg_read(r) for r in regs],vals)

    def test_native_queue_copy_filter_order_failure_and_notifications(self):
        for reverse,album,fail in ((False,'',None),(True,'',None),(False,'Alpha',None),
                                   (True,'Beta',None),(False,'Missing',None),(False,'',2)):
            outcomes=[]
            for image in (original(self.image),self.image):
                m=queue_machine(image,6,reverse,album,fail)
                m.call(0x48878,*m.copy_args)
                order=copied(m)
                self.assertEqual(len(m.notifications),len(order))
                self.assertEqual(m.dirty,[(DB,1)]*len(order))
                outcomes.append((order,m.notifications,bytes(m.cpu.mem_read(QUEUE,0x400)),bytes(m.cpu.mem_read(QUERY,0xb00))))
            self.assertEqual(outcomes[0],outcomes[1])
            expected=[i+1 for i in range(6) if not album or ('Alpha' if i%2==0 else 'Beta')==album]
            if reverse: expected.reverse()
            expected=[ident for i,ident in enumerate(expected) if i!=fail]
            self.assertEqual(outcomes[1][0],expected)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate',type=Path)
    args,remaining=parser.parse_known_args()
    PlaylistCache.image=load_image(args.candidate)
    unittest.main(argv=[__file__,*remaining],verbosity=2)
