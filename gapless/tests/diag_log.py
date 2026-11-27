"""Run the About version-field builder with the diag build and stub the
firmware file routines; check the log write's calls and contents."""
import ctypes, mmap, struct, sys
from unicorn import UC_ARCH_ARM, UC_MODE_ARM, UC_PROT_ALL, Uc, UC_HOOK_MEM_UNMAPPED, UC_HOOK_CODE
from unicorn import arm_const as arm
osos = open(sys.argv[1], 'rb').read()
ram = mmap.mmap(-1, 0x2000000)
ptr = ctypes.addressof(ctypes.c_char.from_buffer(ram))
cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
for base in (0, 0x04000000, 0x10000000, 0x14000000):
    cpu.mem_map_ptr(base, 0x2000000, UC_PROT_ALL, ptr)
cpu.mem_write(0, osos)
cpu.mem_map(0x40000000, 0x18000); cpu.mem_map(0x60000000, 0x10000)
cpu.mem_write(0x60000000, bytes([0x55]))
cpu.hook_add(UC_HOOK_MEM_UNMAPPED, lambda uc, a, addr, *r: print('unmapped', hex(addr), hex(uc.reg_read(arm.UC_ARM_REG_PC))) or False)
R = lambda r: cpu.reg_read(getattr(arm, f'UC_ARM_REG_{r}'))
rd = lambda a: struct.unpack('<I', cpu.mem_read(a, 4))[0]
calls, captured = [], {}
def ret(value):
    cpu.reg_write(arm.UC_ARM_REG_R0, value & 0xffffffff)
    cpu.reg_write(arm.UC_ARM_REG_PC, R('LR'))
def stub(uc, address, size, data):
    if address == 0xa2b60:
        calls.append('volume'); ret(0)
    elif address == 0x93974:
        spec, kind, perm, fdp = R('R0'), R('R1'), R('R2'), R('R3')
        path = bytes(cpu.mem_read(spec + 4, 260)).split(b'\0')[0]
        calls.append(('open', rd(spec), path, hex(kind), perm))
        cpu.mem_write(fdp, struct.pack('<i', 5)); ret(0)
    elif address == 0x93c50:
        fd, buf, n, flags = R('R0'), R('R1'), R('R2'), R('R3')
        written = rd(R('SP'))
        calls.append(('write', fd, n, flags, written != 0))
        captured['text'] = bytes(cpu.mem_read(buf, n))
        cpu.mem_write(written, struct.pack('<I', n)); ret(0)
    elif address == 0x938d4:
        calls.append(('close', R('R0'))); ret(0)
for a in (0xa2b60, 0x93974, 0x93c50, 0x938d4):
    cpu.hook_add(UC_HOOK_CODE, stub, begin=a, end=a)
for t in (1000, 1500000, 3600000):
    calls.clear(); captured.clear()
    cpu.mem_write(0x60005010, struct.pack('<I', t))
    cpu.reg_write(arm.UC_ARM_REG_SP, 0x40017000); cpu.reg_write(arm.UC_ARM_REG_R0, 0x1234)
    cpu.reg_write(arm.UC_ARM_REG_LR, 0)
    cpu.emu_start(0x7669c, 0x766d4, count=2000000)
    assert R('PC') == 0x766d4, hex(R('PC'))
    sp_now = R('SP'); pstr = bytes(cpu.mem_read(sp_now + 104, 64))
    print(t, 'page', pstr[1:1 + pstr[0]], calls)
    if 'text' in captured:
        text = captured['text']
        assert len(text) == 8192 and text.endswith(b'\n')   # diag.c LOG_BYTES
        lines = text.rstrip(b' \n').split(b'\n')
        print('  ', len(lines), 'lines;', lines[0], lines[1][:40], lines[-1][:60])
