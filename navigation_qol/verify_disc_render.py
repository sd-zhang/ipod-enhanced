"""Render 4G disc headings with the actual stock font and monochrome raster.

The fixture supplies a RAM framebuffer, display locks and resource directory;
native font selection, measurement, text, rectangle drawing and clipping run.
It does not establish physical LCD appearance or full-device behavior.
"""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from elftools.elf.elffile import ELFFile
from unicorn import UC_HOOK_CODE
from unicorn import arm_const as arm
from native_fixture import NativeMachine, load_image
from verify_resources import directory

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r35.ipsw'
LIST, CONTEXT, SURFACE, LOCKS = 0x10010000, 0x10011000, 0x10012000, 0x10013000
CLIP, RECT, FRAMEBUFFER = 0x10014000, 0x10014100, 0x10070000


def compile_standalone(directory):
    image = load_image(BASE)
    obj, ld, elf = [directory/name for name in ('disc.o','disc.ld','disc.elf')]
    subprocess.run(['clang','--target=armv4t-none-eabi','-mcpu=arm7tdmi','-marm',
        '-Os','-ffreestanding','-fno-builtin','-fno-unwind-tables',
        '-fno-asynchronous-unwind-tables','-Wall','-Wextra','-Werror',
        '-c',str(ROOT/'navigation_qol/disc_render.c'),'-o',str(obj)],check=True)
    start = len(image)-4
    ld.write_text(f'SECTIONS {{ .text {start} : {{ *(.text*) *(.rodata*) . = ALIGN(4); }} '
        '.bss (NOLOAD) : { *(.bss*) *(COMMON) } .data : { *(.data*) } '
        '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }\n'
        'ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "no retained state")\n')
    zig=Path(next(iter(importlib.util.find_spec('ziglang').submodule_search_locations)))/'zig'
    subprocess.run([str(zig),'ld.lld','-m','armelf','-T',str(ld),'-e','discs_header_draw',
        '-o',str(elf),str(obj)],check=True)
    parsed=ELFFile(io.BytesIO(elf.read_bytes()))
    text=parsed.get_section_by_name('.text')
    return image[:-4]+text.data()+image[-4:], parsed['e_entry']


def raster_machine(image, clip, rect, origin, visible, shown=True):
    m=NativeMachine(image)
    m.instruction_limit=1000000
    m.hook(0x9f9bc,lambda:0)
    m.hook(0xec,lambda:m.cpu.mem_write(m.reg(0),bytes(m.cpu.mem_read(m.reg(1),m.reg(2)))))
    resources=directory(image)
    addresses={(int.from_bytes(tag.encode(),'big'),key):image.find(raw)
               for tag,(_,entries) in resources.items() for key,raw in entries.items()}
    m.hook(0x72a88,lambda:addresses.get((m.reg(0),m.reg(1)),0))
    # Execute the actual static-font initializer, as boot does.
    m.call(0x1cb84)
    m.write(LIST,0x29030)
    m.write(LIST+0x20,0x800 if shown else 0)
    m.write(LIST+0x58,*origin)
    m.write(LIST+0x78,*visible)
    m.write(CLIP,*clip)
    m.write(RECT,*rect)
    m.write(CONTEXT+0x90,0,0,128,160)
    m.write(CONTEXT+0xa4,1)
    m.hook(0xc0634,lambda:CONTEXT)
    m.write(0x103cdcc8,SURFACE)
    m.write(SURFACE+4,FRAMEBUFFER,20,1,0,0,0,128,160,LOCKS)
    m.hook(0x2e384,lambda:None)
    m.hook(0xbb734,lambda:None)
    m.cpu.mem_write(FRAMEBUFFER-16,b'\xa5'*16+bytes(2560)+b'\xa5'*16)
    # No allocations should occur in a row paint, including after font setup.
    def unexpected(): raise AssertionError('disc row paint allocated memory')
    for at in (0xd9a60,0xd97b8,0x9b8e0):m.hook(at,unexpected)
    return m


def pixels(m):
    raw=bytes(m.cpu.mem_read(FRAMEBUFFER,2560))
    assert bytes(m.cpu.mem_read(FRAMEBUFFER-16,16))==b'\xa5'*16
    assert bytes(m.cpu.mem_read(FRAMEBUFFER+2560,16))==b'\xa5'*16
    return {(y,x) for y in range(128) for x in range(160)
            if raw[y*20+x//8]&(128>>(x%8))}


class DiscHeaderRaster(unittest.TestCase):
    def frame(self,disc=2,clip=(20,0,128,150),rect=(38,0,56,150),
              origin=(20,0,128,150),visible=(20,0,128,150),shown=True,
              black_background=False):
        m=raster_machine(self.image,clip,rect,origin,visible,shown)
        if black_background:m.cpu.mem_write(FRAMEBUFFER,b'\xff'*2560)
        self.drawn_text=[]
        def text_call(cpu,pc,size,ctx):
            self.drawn_text.append(bytes(cpu.mem_read(m.reg(1),11)).split(b'\0')[0].decode('ascii'))
        m.cpu.hook_add(UC_HOOK_CODE,text_call,begin=0x5aed8,end=0x5aed8)
        saved=bytes(m.cpu.mem_read(LIST,0x200))
        context=bytes(m.cpu.mem_read(CONTEXT,0xb0))
        registers=[getattr(arm,f'UC_ARM_REG_R{i}') for i in range(4,12)]
        values=[0xabb00000+i for i in range(8)]
        def seed(cpu,pc,size,ctx):
            for r,v in zip(registers,values):cpu.reg_write(r,v)
        m.cpu.hook_add(UC_HOOK_CODE,seed,begin=self.entry,end=self.entry)
        m.call(self.entry,LIST,CLIP,RECT,disc)
        self.assertEqual(bytes(m.cpu.mem_read(LIST,0x200)),saved)
        self.assertEqual(bytes(m.cpu.mem_read(CONTEXT,0xb0)),context)
        self.assertEqual([m.cpu.reg_read(r) for r in registers],values)
        return pixels(m)

    def test_native_small_font_centered_between_one_pixel_rules(self):
        points=self.frame()
        # Independently captured native Espy Sans9 bold glyphs for "Disc 2".
        glyph=(
            '#####   ##              #### ',
            '##  ##                 ##  ##',
            '##   ## ##  ###  ###       ##',
            '##   ## ## ##   ##       ### ',
            '##   ## ## #### ##      ##   ',
            '##  ##  ##   ## ##     ##    ',
            '#####   ## ###   ###   ######')
        expected={(43+y,60+x) for y,row in enumerate(glyph) for x,c in enumerate(row) if c=='#'}
        expected|={(46,x) for x in range(4,56)}|{(46,x) for x in range(94,146)}
        self.assertEqual(points,expected)

    def test_partial_and_ancestor_clips_never_recenter_the_header(self):
        full=self.frame()
        for clip in ((42,10,49,85),(20,0,30,150),(46,80,47,150)):
            with self.subTest(clip=clip):
                expected={(y,x) for y,x in full if clip[0]<=y<clip[2] and clip[1]<=x<clip[3]}
                self.assertEqual(self.frame(clip=clip),expected)
        visible=(40,20,48,120)
        expected={(y,x) for y,x in full if visible[0]<=y<visible[2] and visible[1]<=x<visible[3]}
        self.assertEqual(self.frame(visible=visible),expected)

    def test_absolute_rectangles_survive_scrolling_and_nonzero_left_origin(self):
        baseline=self.frame()
        for top in (-500,-70,20):
            self.assertEqual(self.frame(origin=(top,0,top+900,150)),baseline)
        moved=self.frame(rect=(48,6,66,156),origin=(-500,6,400,156),
                         clip=(20,0,128,160),visible=(20,0,128,160))
        self.assertEqual(moved,{(y+10,x+6) for y,x in baseline})

    def test_disc_number_boundaries_and_narrow_rows(self):
        seen=[]
        for disc in (1,2,9,10,99,100,999,1000,9999,10000,65535):
            points=self.frame(disc)
            self.assertEqual(self.drawn_text,[f'Disc {disc}'])
            self.assertTrue(points)
            self.assertTrue(all(38<=y<56 and 0<=x<150 for y,x in points))
            self.assertNotIn(points,seen)
            seen.append(points)
        for disc in (0,65536,0xffffffff):self.assertEqual(self.frame(disc),set())
        self.assertEqual(self.frame(shown=False),set())
        self.assertEqual(self.frame(rect=(38,0,38,150)),set())
        self.assertEqual(self.frame(rect=(38,0,45,150)),set())
        narrow=self.frame(65535,rect=(38,0,56,40))
        self.assertTrue(narrow)
        self.assertTrue(all(0<=x<40 and 38<=y<56 for y,x in narrow))

    def test_white_header_clear_is_confined_to_dirty_row_area(self):
        clip=(42,10,49,120)
        drawn=self.frame(clip=clip)
        black={(y,x) for y in range(128) for x in range(160)}
        cleared={(y,x) for y in range(42,49) for x in range(10,120)}
        self.assertEqual(self.frame(clip=clip,black_background=True),
                         (black-cleared)|drawn)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate',type=Path,nargs='?')
    args,remaining=parser.parse_known_args()
    with tempfile.TemporaryDirectory(prefix='ipod4g-disc-render-') as temporary:
        if args.candidate:
            DiscHeaderRaster.image=load_image(args.candidate)
            symbols=json.loads((args.candidate.with_suffix('.artifacts')/'disc-separators-symbols.json').read_text())
            DiscHeaderRaster.entry=symbols['discs_header_draw']
        else:
            DiscHeaderRaster.image,DiscHeaderRaster.entry=compile_standalone(Path(temporary))
        unittest.main(argv=[__file__,*remaining],verbosity=2)
