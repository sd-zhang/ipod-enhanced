"""Verify native 4G ScriptVM lookup behavior in an exact packaged candidate.

Uses the repository Python environment outside the macOS sandbox for Unicorn
JIT memory. Exhaustive 65,536-ID evidence is separate; this release suite covers
all declared functions/classes/globals plus boundaries and malformed values.
The VM and playback control path execute native ARM; allocator, storage, audio
dispatch and rendering boundaries are supplied by the existing fixtures.
"""
import argparse
import hashlib
import json
import struct
import sys
import unittest
from pathlib import Path

REPO = next(p for p in Path(__file__).resolve().parents if (p/'navigation_qol/native_fixture.py').is_file())
sys.path.insert(0, str(REPO/'navigation_qol'))
from native_fixture import ScriptVM, OUT, load_image
from verify_resources import directory
from verify_native import REG, STACK, RETURN
import verify_actions_playback as playback
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn import arm_const as arm

REFERENCE = REPO/'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r35.ipsw'
REFERENCE_SHA = '75d7da743dc01c4f427709d56b800b713a15261828fa8412906066830ca9501b'
CODE = struct.pack('<10I', 0xe59f1010,0xe0401001,0xe351004b,0x359ff008,
                   0xe59f2008,0xe59ff008,2461,0x94af4,0x1041fb1c,0x94aa0)


def resolve(machine, value, flags=0xa0000000):
    for n, register in enumerate(REG):
        machine.cpu.reg_write(register, 0x11110000+n*0x1234)
    for n, value in enumerate((value,OUT,OUT+4)):
        machine.cpu.reg_write(REG[n],value&0xffffffff)
    machine.cpu.reg_write(arm.UC_ARM_REG_SP,STACK)
    machine.cpu.reg_write(arm.UC_ARM_REG_LR,RETURN)
    cpsr=machine.cpu.reg_read(arm.UC_ARM_REG_CPSR)
    machine.cpu.reg_write(arm.UC_ARM_REG_CPSR,(cpsr&~0xf0000000)|flags)
    machine.cpu.emu_start(0x949d0,RETURN,count=200000)
    assert machine.cpu.reg_read(arm.UC_ARM_REG_PC)==RETURN
    assert machine.cpu.reg_read(arm.UC_ARM_REG_SP)==STACK
    assert [machine.reg(n) for n in range(4,12)]==[0x11110000+n*0x1234 for n in range(4,12)]
    return machine.reg(0),bytes(machine.cpu.mem_read(OUT,8)),machine.cpu.reg_read(arm.UC_ARM_REG_CPSR)


def writes(machine):
    result=[]
    def capture(cpu,access,at,size,value,context):
        # r3 is caller-saved; expired stack frames may retain its new value.
        # The prototype contains no store. Require identical non-stack writes.
        if not 0x100fc000<=at<STACK: result.append((at,size,value))
    machine.cpu.hook_add(UC_HOOK_MEM_WRITE,capture)
    return result


def instructions(machine, action):
    count=0
    def trace(cpu,pc,size,context):
        nonlocal count
        count+=1
    handle=machine.cpu.hook_add(UC_HOOK_CODE,trace)
    result=action()
    machine.cpu.hook_del(handle)
    return result,count


class ScriptGlobals(unittest.TestCase):
    def test_exact_candidate_native_code_contract(self):
        self.assertEqual(hashlib.sha256(self.reference).hexdigest(),REFERENCE_SHA)
        branch=struct.unpack_from('<I',self.image,0x94a9c)[0]
        self.assertEqual(branch&0xff000000,0xea000000)
        relative=branch&0xffffff
        if relative&0x800000: relative-=1<<24
        target=0x94a9c+8+4*relative
        self.assertGreaterEqual(target,len(self.reference)-4)
        self.assertEqual(target&3,0)
        self.assertEqual(self.image[target:target+len(CODE)],CODE)
        before=bytearray(self.image[0x949d0:0x94bd0])
        struct.pack_into('<I',before,0x94a9c-0x949d0,0xe59f210c)
        self.assertEqual(before,self.reference[0x949d0:0x94bd0])
        self.assertEqual(directory(self.image)['TCmd'][1][0x2380],
                         directory(self.reference)['TCmd'][1][0x2380])

    def test_all_declared_atoms_boundary_tags_flags_abi_and_ram(self):
        old,new=ScriptVM(self.reference),ScriptVM(self.image)
        old_writes,new_writes=writes(old),writes(new)
        blob=directory(self.reference)['TCmd'][1][0x2380]
        head=8+4*struct.unpack_from('<H',blob,6)[0]
        count,explicit=struct.unpack_from('<HH',blob,head)
        explicit_ids=struct.unpack_from(f'<{explicit}H',blob,head+4)
        # Entire class interval, all implicit/explicit functions, all globals,
        # and surrounding unknowns (also include the special atom 6).
        atoms=set(range(0,90))|set(range(1846,2537))|set(explicit_ids)
        atoms.update((153,155,352,354,2558,32767,32768,65534,65535))
        values=[atom*4+2 for atom in sorted(atoms)]
        values += [0,1,3,4,0xffffffff,0xfffffffe,0x80000002,0x7ffffffe,
                   0xfffc0012,0x40000002,0xc0000002,0x80002676,0xffff2676]
        for flags in (0,0x10000000,0x60000000,0xf0000000):
            for value in values:
                self.assertEqual(resolve(old,value,flags),resolve(new,value,flags),hex(value))
                self.assertEqual(old_writes,new_writes,hex(value))
                old_writes.clear(); new_writes.clear()
        self.assertEqual(old.allocations,new.allocations)
        self.assertEqual(old.freed,new.freed)
        for start,size in ((0x10000000,0xfc000),(STACK,0x10000000+0x100000-STACK),
                           (0x10300000,0x200000)):
            self.assertEqual(bytes(old.cpu.mem_read(start,size)),bytes(new.cpu.mem_read(start,size)))

    def test_every_global_with_changed_bindings_and_owned_output(self):
        old,new=ScriptVM(self.reference),ScriptVM(self.image)
        for atom in range(2461,2536):
            for value in (0,37*4+1,0xc0000002,0xc0000000):
                for machine in (old,new): machine.call(0xe7658,machine.global_slot(atom),value)
                for _ in range(2):
                    self.assertEqual(resolve(old,atom*4+2),resolve(new,atom*4+2),atom)
        self.assertEqual(old.allocations,new.allocations)
        self.assertEqual(old.freed,new.freed)

    def test_global_lookup_removes_extended_function_scan(self):
        measured=[]
        for image in (self.reference,self.image):
            machine=ScriptVM(image)
            counts=[]
            for atom in (2461,2462,2464,2501,2535):
                result,count=instructions(machine,lambda:machine.call(0x949d0,atom*4+2,OUT,OUT+4))
                self.assertEqual((result,machine.read(OUT+4)),(1,1))
                counts.append(count)
            measured.append(counts)
        for before,after in zip(*measured): self.assertLess(after,before*.4,measured)

    def test_ordinary_songs_and_albums_keep_native_controls_and_playback(self):
        oracle=playback.NativePlaybackEntry()
        for image in (self.reference,self.image):
            oracle.image=image
            machine=playback.playback_machine(image,2075)
            machine.call(0xe2d40,playback.UI)
            self.assertEqual(machine.run(2075),0xc0000002)
            self.assertEqual(machine.run(2076,machine.event),0xc0000002)
            oracle.check_now_playing(machine,[0,1,2],1,1)
            self.assertEqual(machine.run(2019),0xc0000002)
            self.assertEqual(machine.run(2015,machine.event),0xc0000002)
            machine=playback.playback_machine(image,2092)
            machine.write(machine.read(machine.wrappers[1]+0x20)+0x30,1)
            machine.call(0xe033c,playback.UI)
            self.assertEqual(machine.run(2092),0xc0000002)
            self.assertEqual(machine.run(2095,machine.event),0xc0000002)
            oracle.check_now_playing(machine,[0,1],0,-1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate',type=Path)
    parser.add_argument('--reference',type=Path,default=REFERENCE)
    args=parser.parse_args()
    ScriptGlobals.reference=load_image(args.reference)
    ScriptGlobals.image=load_image(args.candidate)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(ScriptGlobals)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
