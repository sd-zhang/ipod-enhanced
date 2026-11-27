"""The dispose hook (BL at 0xa7f20) on the installed image: a retired
request that is not the one playing is released without the stock stop."""
import ctypes, mmap, struct, sys
from pathlib import Path
from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_MODE_ARM, UC_PROT_ALL, Uc, UC_HOOK_CODE
from unicorn import arm_const as arm

osos = Path(sys.argv[1]).read_bytes()
with open(sys.argv[2], 'rb') as f:
    sym = {s.name: s['st_value'] for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols() if s.name}
ram = mmap.mmap(-1, 0x2000000)
ptr = ctypes.addressof(ctypes.c_char.from_buffer(ram))
cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
for base in (0, 0x04000000, 0x10000000, 0x14000000):
    cpu.mem_map_ptr(base, 0x2000000, UC_PROT_ALL, ptr)
cpu.mem_write(0, osos)
cpu.mem_map(0x60000000, 0x10000)
R = lambda r: cpu.reg_read(getattr(arm, f'UC_ARM_REG_{r}'))
w32 = lambda a, v: cpu.mem_write(a, struct.pack('<I', v))
r32 = lambda a: struct.unpack('<I', cpu.mem_read(a, 4))[0]
calls = []
STOP = 0x00fff000
def stub(uc, address, size, data):
    calls.append((address, R('R0'), R('R1')))
    uc.reg_write(arm.UC_ARM_REG_R0, 0x5150 if address == 0x123318 else 0)
    uc.reg_write(arm.UC_ARM_REG_PC, R('LR'))
for a in (0xdcd48, 0x123318, 0x9b640):
    cpu.hook_add(UC_HOOK_CODE, stub, begin=a, end=a)
REQUEST, TPLY = 0x00f00000, 0x00f00014
RETIRED_HEAD = sym['gapless_handoff_state'] + 0      # first word read by the hook
def run(request_cookie, playing, claimed=False):
    calls.clear()
    cpu.mem_write(REQUEST, bytes(0x400))
    w32(TPLY, 0x74706c79)                     # 'tply'
    w32(REQUEST + 0x2cc, request_cookie)
    w32(sym['gapless_engine_timing'] + 0x20, playing)
    # Read RETIRED_HEAD's offset from the hook's own literal pool.
    head = sym['gapless_handoff_state'] + HEAD_OFFSET
    w32(head, REQUEST if claimed else 0)
    w32(REQUEST + 0x2bc, 0)
    cpu.reg_write(arm.UC_ARM_REG_R0, TPLY); cpu.reg_write(arm.UC_ARM_REG_R4, REQUEST)
    cpu.reg_write(arm.UC_ARM_REG_SP, 0x00e00000); cpu.reg_write(arm.UC_ARM_REG_LR, STOP)
    cpu.emu_start(sym['gapless_dispose_request'], STOP, count=20000)
    assert R('PC') == STOP
    return [hex(c[0]) for c in calls], cpu.mem_read(TPLY + 0x22a, 1)[0], r32(TPLY + 0x224)
# RETIRED_HEAD is an .equ in handoff.S; find it from the first literal the hook loads.
hook = sym['gapless_dispose_request']
first = r32(hook)                               # ldr r12, =gapless_handoff_state+RETIRED_HEAD
lit = hook + 8 + (first & 0xfff)
HEAD_OFFSET = r32(lit) - sym['gapless_handoff_state']
cases = (('the request being played', 7, 7, False, ['0xdcd48'], 0),
         ('a request handed over gaplessly', 7, 8, False, ['0x123318', '0x9b640'], 1),
         ('a request claimed by the retire hook', 7, 8, True, ['0x9b640'], 0),
         ('no selection known', 7, 0, False, ['0xdcd48'], 0))
ok = True
for name, cookie, playing, claimed, want, flag in cases:
    got, disposed, stamp = run(cookie, playing, claimed)
    good = got == want and disposed == flag and (not flag or stamp == 0x5150)
    ok &= good
    print(f'{name}: {"OK" if good else "MISMATCH"} calls {got} disposed flag {disposed}')
sys.exit(0 if ok else 1)
