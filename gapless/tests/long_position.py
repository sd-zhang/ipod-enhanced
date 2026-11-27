"""Counter-limit fallback preserves PCM, AAC pre-roll and native EOF draining.

Position is seeded near its boundary; this does not emulate thirteen hours of
hardware playback. OS/decoder delivery and IRQ timing use existing fixtures.
"""
from pathlib import Path
import json,struct,sys
from elftools.elf.elffile import ELFFile
from unicorn import arm_const as arm
sys.path[:0]=[str(Path('gapless').resolve()),str(Path('gapless/tests').resolve())]
from verify_tx import Machine,QUEUE,RING,DESCRIPTORS,BUFFER,ENGINE,REG,STACK,RETURN
STOCK_PATH, IMAGE_PATH, ELF = map(Path, sys.argv[1:4])
STOCK=STOCK_PATH.read_bytes(); INSTALLED=IMAGE_PATH.read_bytes()
DECODER=0x10034000
results=[]
def fresh(codec,frames,position,*,decoder_packet=None):
 with ELF.open('rb') as f: m=Machine(STOCK,f,0x21eb4)
 m.cpu.mem_write(0,INSTALLED)
 m.reset(9216,wire_bytes=4*frames)
 m.write(ENGINE+0x2e80,QUEUE);m.write(ENGINE+0x90cc,frames);m.write(ENGINE+0x1940,1)
 m.cpu.mem_write(ENGINE+0x2e54,bytes([codec]));m.cpu.mem_write(ENGINE+0x2994,b'\0')
 m.cpu.mem_write(DESCRIPTORS+8,b'\x07');m.write(QUEUE+0xdc,BUFFER)
 for field in (0,1):m.cpu.mem_write(m.symbols[f'producer.{field}'],b'\x01')
 for field,val in ((4,codec),(5,1088 if codec==10 else 1105),(6,3000001105),(7,position)):
  m.write(m.symbols[f'producer.{field}'],val)
 if decoder_packet is not None:
  for off,val in ((0x2cd4,decoder_packet),(0x2d10,1),(0x2d14,1),(0x2cf4,0)):m.write(DECODER+off,val)
 return m

def publish(m):
 m.prepare(ENGINE,DECODER)
 for i in range(4,12):m.cpu.reg_write(REG[i],0x77770000+i)
 sp=m.cpu.reg_read(arm.UC_ARM_REG_SP)
 masks=m.cpu.reg_read(arm.UC_ARM_REG_CPSR)&0xc0
 m.run(m.symbols['gapless_trim_publish'])
 assert m.cpu.reg_read(arm.UC_ARM_REG_SP)==sp
 assert all(m.cpu.reg_read(REG[i])==0x77770000+i for i in range(4,12))
 assert m.cpu.reg_read(arm.UC_ARM_REG_CPSR)&0xc0==masks
 return struct.unpack('<7I',m.cpu.mem_read(m.symbols['notes'],28))

def enabled(m):return m.cpu.mem_read(m.symbols['producer.0'],1)[0]
for codec,frames in ((0,1152),(0,576),(10,1024),(10,1023)):
 position=(0x7fffffff//frames)*frames
 m=fresh(codec,frames,position)
 note=publish(m)
 assert enabled(m)==0 and m.cpu.mem_read(m.symbols['producer.1'],1)==b'\1'
 assert m.read(m.symbols['producer.7'])==position+frames
 assert (note[3]-note[2])==frames
 second=publish(m)
 assert second[2]==0 and second[3]==frames and not(second[1]&4)
 m.call('gapless_trim_seek',0,1)
 assert enabled(m)==0
 third=publish(m)
 assert third[2]==0 and third[3]==frames
 results.append({'case':f'codec{codec} frames{frames} overflow, nextnote, seekback','passed':True})
for initial in (0,20480,0x7fffff80):
 for frames in (1024,1023):
  m=fresh(10,frames,initial,decoder_packet=0)
  note=publish(m)
  assert enabled(m)==1,(initial,frames,note)
  assert m.read(m.symbols['producer.7'])==(-1024+frames)&0xffffffff
  results.append({'case':f'AAC packetzero initial{initial} frames{frames}','passed':True})
# Derive the last valid AAC absolute index after a seek, then advance across the boundary.
m=fresh(10,1024,20480,decoder_packet=0x1ffffe)
for i in range(3):
 m.write(DECODER+0x2cd4,0x1ffffe+i)
 publish(m)
 assert enabled(m)==int(i<2)
results.append({'case':'AAC resync near limit then real 1024-frame crossing','passed':True})
m=fresh(10,4096,20480,decoder_packet=0x1ffffe)
publish(m)
assert enabled(m)==0
results.append({'case':'AAC oversized recovery block crosses during resync','passed':True})
m=fresh(0,1152,0x80000000)
publish(m)
assert enabled(m)==0
results.append({'case':'already-negative invalid position conservatively disabled','passed':True})
# A late AAC start initializes relative position to zero; a late seek keeps
# its previous count when the native decoder index is above the safe bound.
m=fresh(10,1024,0,decoder_packet=0x210000)
timing=m.symbols['gapless_engine_timing']
m.cpu.mem_write(timing,struct.pack('<4I',2112,3000000000,0,0));m.write(timing+32,7)
m.write(ENGINE+0x29c0,50000000)
m.call('gapless_trim_start',ENGINE,DECODER)
assert m.read(m.symbols['producer.7'])==0 and enabled(m)==1
note=publish(m)
assert note[2]==0 and note[3]==1024
m.write(m.symbols['producer.7'],20480)
m.call('gapless_trim_seek',50000000,1)
note=publish(m)
assert note[2]==0 and note[3]==1024 and enabled(m)==1
results.append({'case':'AAC start and seek beyond signed decoder range remain audible','passed':True})
# Mid-file MP3 starts already use the existing untrimmed behavior.
m=fresh(0,1152,0)
timing=m.symbols['gapless_engine_timing']
m.cpu.mem_write(timing,struct.pack('<4I',576,3000000000,0,0));m.write(timing+32,7)
m.cpu.mem_write(0x10060000,b'\xff\xfb\x90\x00')
m.call('gapless_mp3_probe',0x10060000,4)
m.write(ENGINE+0x29c0,50000000)
m.call('gapless_trim_start',ENGINE,DECODER)
assert enabled(m)==0
note=publish(m)
assert note[2]==0 and note[3]==1152
results.append({'case':'MP3 mid-file start beyond limit remains untrimmed','passed':True})
# Real audio carry leaves the existing FIFO in order after trim disables.
for codec,frames,held in ((0,1152,2304),(10,1024,3200)):
 m=fresh(codec,frames,(0x7fffffff//frames)*frames)
 m.write(m.symbols['producer.5'],0)
 if codec==10:
  m.cpu.mem_write(m.symbols['producer.10'],b'\1');m.write(m.symbols['producer.9'],2048)
 m.write(m.symbols['fifo_len'],held);m.write(m.symbols['output_started'],1)
 m.cpu.mem_write(m.symbols['fifo'],struct.pack('<'+'I'*held,*range(1,held+1)))
 output=b'';want=list(range(1,held+1))
 for block in range(5):
  values=list(range(held+1+block*frames,held+1+(block+1)*frames));want+=values
  m.cpu.mem_write(BUFFER,struct.pack('<'+'I'*frames,*values))
  publish(m)
  out=m.call('gapless_trim_output',frames*4,QUEUE)
  output+=bytes(m.cpu.mem_read(BUFFER,out))
 length=m.read(m.symbols['fifo_len'])
 carry=bytes(m.cpu.mem_read(m.symbols['fifo'],4*length))
 assert output+carry==struct.pack('<'+'I'*len(want),*want)
 assert length==0
 results.append({'case':f'codec{codec} buffered carry flush','passed':True,'frames_verified':len(want)})
# Replay true native EOF drain after the overflow fallback via the existing fixture.
sys.argv=[__file__,str(Path("gapless").resolve()),str(STOCK_PATH),str(IMAGE_PATH),str(ELF)]
import track_end
from repro_aac_edges import numbered
from verify_mixed_album import File
from verify_transition import ENGINE as REAL_ENGINE
from unicorn import UC_HOOK_CODE
track,_=numbered(0x100000,25000,1024)
track=File(10,track.delay,track.count,track.padding,track.blocks,reference_pcm=b''.join(track.blocks))
original_init=track_end.EndAlbum.__init__
def guard_album_init(self,*args,**kwargs):
 original_init(self,*args,**kwargs)
 m=self.m;first=[True]
 def force_long_boundary(cpu,pc,size,unused):
  if first[0]:
   first[0]=False
   m.write(m.symbols['producer.7'],0x7ffffc00)
   m.write(m.symbols['producer.6'],3000000000)
   # AAC counters no longer identify positions beyond their signed range.
   m.write(track_end.DECODER+0x2cf4,1)
 m.cpu.hook_add(UC_HOOK_CODE,force_long_boundary,begin=m.symbols['gapless_trim_publish'],end=m.symbols['gapless_trim_publish'])
track_end.EndAlbum.__init__=guard_album_init
for adapter in (0x21eb4,0x21f3c):
 blocked=track_end.end_track(STOCK,INSTALLED,ELF,track,adapter,delayed=True)
 results.append({'case':f'native final drain after counterfallback adapter{adapter:#x}','passed':True,'blocked_polls':blocked})
print(json.dumps(results,indent=2))
