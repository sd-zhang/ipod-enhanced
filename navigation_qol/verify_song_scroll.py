"""Execute 4G title import and Songs wheel navigation in the native list."""
import argparse
import json
import struct
import types
import unittest
from pathlib import Path

from native_fixture import load_image, QUERY, DB
from verify_disc_fast_scroll import wheel_fixture, active
from verify_disc_separators import LIST, ROW, MODEL, UI, virtual
from verify_letters import WheelMachine, EVENT


def physical_input(m, mode=1, period=25):
    from verify_wheel_input import WheelInputMachine
    m.cpu.mem_map(0x60005000, 0x1000)
    m.cpu.mem_map(0x7000c000, 0x1000)
    m.hook(0x87654, lambda: mode)
    m.hook(0xb1394, lambda: None)
    m.packet = types.MethodType(WheelInputMachine.packet, m)
    m.move = types.MethodType(WheelInputMachine.move, m)
    m.position = 0
    m.write(0x103c9954, m.now*1000, 0, 0)
    m.packet(0x8000001a)
    m.packet(0xc000001a)
    m.call(0x862dc)
    for _ in range(16):
        m.move(8, period, drain=True)
    return m


def cache_title(m, track, text):
    data = text.encode('utf-16le', errors='surrogatepass')
    at = 0x113f0000
    m.cpu.mem_write(at, data or b'\0\0')
    def title():
        assert m.reg(0) == DB + 0xb0
        m.write(m.reg(2), at)
        m.write(m.reg(3), len(data))
        return 0
    m.hook(0x795dc, title)
    # Keep the original classification call and flag update observable.
    m.hook(0x92e58, lambda: int((m.reg(0), m.reg(1), m.reg(2)) ==
                               (at, len(data)//2, 0x1d)))
    m.call(0x3cfc0, track)


def songs_fixture(image, titles=None, viewport=False):
    if titles is None:
        titles = [letter + str(n) for letter in 'ABCD' for n in range(20)]
    m = wheel_fixture(image, (1,)*len(titles), album=False, viewport=viewport)
    # Same native query setter used by the main Songs script's fb(4).
    m.call(0xcdf90, QUERY, 4)
    if not viewport:
        # Native constructed Songs list has this no-wrap flag.
        m.write(LIST+0x20, m.read(LIST+0x20)|0x04000000)
    for entry, title in zip(m.wrappers, titles):
        cache_title(m, m.read(entry+0x20), title)
    return m


class SongScroll(unittest.TestCase):
    def test_ascii_title_import_avoids_full_prefix_parser_cost(self):
        from native_fixture import NativeMachine
        from unicorn import UC_HOOK_CODE
        for text,expected in (('Alpha','A'),('zebra','Z'),('The Alpha','T')):
            m=NativeMachine(self.image)
            track,at=0x10040000,0x10030000
            raw=text.encode('utf-16le')
            m.write(track,DB)
            m.cpu.mem_write(at,raw)
            def title():
                self.assertEqual(m.reg(0),DB+0xb0)
                m.write(m.reg(2),at)
                m.write(m.reg(3),len(raw))
                return 0
            m.hook(0x795dc,title)
            count=[0]
            h=m.cpu.hook_add(UC_HOOK_CODE,lambda *a:count.__setitem__(0,count[0]+1))
            try:m.call(0x3cfc0,track)
            finally:m.cpu.hook_del(h)
            self.assertEqual(bytes(m.cpu.mem_read(track+0xff,1)),expected.encode())
            self.assertLessEqual(count[0],95,'simple ASCII titles should bypass full Unicode prefix scanning')

    def test_title_cache_fast_path_matches_parser_at_ascii_boundaries(self):
        from native_fixture import NativeMachine
        m=NativeMachine(self.image)
        at,track=0x10030000,0x10040000
        # Include Unicode codepoints with ASCII-looking low bytes, and odd
        # pointers. The shared parser uses byte reads; the shortcut must too.
        for point in (*range(128),0x141,0x161,0x241,0x1041,0xd841):
            for odd in (0,1):
                raw=(chr(point)+'Zebra').encode('utf-16le',errors='surrogatepass')
                m.cpu.mem_write(at+odd,raw)
                expected=m.call(0x35c718,at+odd,len(raw),1,0) or ord('#')
                m.cpu.mem_write(track+0xfc,b'XYZ\0')
                m.call(self.symbols['songs_cache_title'],at+odd,len(raw)//2,track)
                self.assertEqual(bytes(m.cpu.mem_read(track+0xfc,4)),b'XYZ'+bytes([expected]),(point,odd))

    def test_host_title_sort_controls_jump_letter_in_either_import_order(self):
        from verify_sort_letters import attach_stream, import_sort, text_payload
        from native_fixture import OUT
        for endian in ('<', '>'):
            for encoding in (1, 2):
                for sort_first in (False, True):
                    with self.subTest(endian=endian, encoding=encoding, sort_first=sort_first):
                        m=songs_fixture(self.image, ['The Alpha', 'A Bravo', 'Custom title'])
                        attach_stream(m, map_reader=False)
                        for row,(display,sort,letter) in enumerate((
                                ('The Alpha','Alpha','A'), ('A Bravo','Bravo','B'),
                                ('Custom title','  (Zebra)','Z'))):
                            track=m.read(m.wrappers[row]+0x20)
                            m.cpu.mem_write(track+0xfc,b'XYZ')
                            if sort_first: m.cpu.mem_write(track+0xff,b'\0')
                            self.assertEqual(import_sort(m,27,text_payload(sort,endian,encoding),endian,track),0)
                            if sort_first: cache_title(m,track,display)
                            self.assertEqual(m.call(self.symbols['songs_fast_target'],LIST,row,0,OUT),row)
                            self.assertEqual(m.read(OUT),ord(letter))
                            self.assertEqual(bytes(m.cpu.mem_read(track+0xfc,3)),b'XYZ')
                        m.wheel(80)
                        m.wheel(80,40)
                        self.assertTrue(active(m))
                        self.assertEqual(m.read(m.read(LIST+0xe4)+96),ord('Z'))

    def test_invalid_title_sort_preserves_display_fallback_and_stream_errors(self):
        from verify_sort_letters import attach_stream, import_sort, text_payload
        from native_fixture import OUT
        invalid=[b'',b'x'*15,text_payload(''),text_payload('   '),
                 struct.pack('<4I',1,2,0,0)+b'\x00\xd8',
                 struct.pack('<4I',1,40,0,0)+b'ZZ']
        for data in invalid:
            m=songs_fixture(self.image,['Bravo'])
            attach_stream(m,map_reader=False)
            track=m.read(m.wrappers[0]+0x20)
            self.assertEqual(import_sort(m,27,data,track=track),0)
            self.assertEqual(m.stream_position,len(data))
            m.call(self.symbols['songs_fast_target'],LIST,0,0,OUT)
            self.assertEqual(m.read(OUT),ord('B'))
        m.stream_error=0xffffff30
        self.assertEqual(import_sort(m,27,text_payload('Zebra'),track=track),m.stream_error)
        m.call(self.symbols['songs_fast_target'],LIST,0,0,OUT)
        self.assertEqual(m.read(OUT),ord('B'))

    def test_mixed_host_and_display_groups_merge_and_duplicate_hints_are_safe(self):
        from verify_sort_letters import attach_stream, import_sort, text_payload
        from native_fixture import OUT
        m=songs_fixture(self.image,['The Alpha','Amber','A Bravo'])
        attach_stream(m,map_reader=False)
        for row,sort in ((0,'Alpha'),(2,'Bravo')):
            self.assertEqual(import_sort(m,27,text_payload(sort),track=m.read(m.wrappers[row]+0x20)),0)
        self.assertEqual(m.call(self.symbols['songs_fast_target'],LIST,0,1,OUT),2)
        self.assertEqual(m.read(OUT),ord('B'))
        self.assertEqual(m.call(self.symbols['songs_fast_target'],LIST,2,-1,OUT),0)
        self.assertEqual(m.read(OUT),ord('A'))
        track=m.read(m.wrappers[0]+0x20)
        for sort,expected in (('Zebra','Z'),('','Z'),('   ','Z'),('###','#')):
            self.assertEqual(import_sort(m,27,text_payload(sort),track=track),0)
            cache_title(m,track,'Replacement Display')
            m.call(self.symbols['songs_fast_target'],LIST,0,0,OUT)
            self.assertEqual(m.read(OUT),ord(expected))
        # Without a host hint, a repeated display title must update normally.
        track=m.read(m.wrappers[1]+0x20)
        cache_title(m,track,'Delta')
        m.call(self.symbols['songs_fast_target'],LIST,1,0,OUT)
        self.assertEqual(m.read(OUT),ord('D'))

    def test_full_track_import_and_clone_preserve_host_title_priority(self):
        from native_fixture import NativeMachine, OUT
        from verify_sort_letters import attach_stream, text_payload, READER, TRACK
        from verify_native import STACK, REG
        from unicorn import arm_const as arm
        for sort_first in (False,True):
            m=NativeMachine(self.image)
            attach_stream(m)
            m.cpu.mem_map(0x113f0000,0x1000)
            records=[(1,'The Alpha'),(27,'Alpha'),(28,'Album')]
            if sort_first: records[:2]=reversed(records[:2])
            children=b''
            for kind,name in records:
                data=text_payload(name)
                children+=b'mhod'+struct.pack('<5I',24,24+len(data),kind,0,0)+data
            header=bytearray(0xf4)
            header[:4]=b'mhit'
            struct.pack_into('<4I',header,4,0xf4,0xf4+len(children),len(records),12345)
            struct.pack_into('<I',header,0x14,1)
            struct.pack_into('<f',header,0x88,44100.0)
            gapless=(291,0x76543210,2,1110)
            struct.pack_into('<3I',header,0xb8,*gapless[:3])
            struct.pack_into('<I',header,0xc8,gapless[3])
            m.stream_data=bytes(header)+children
            m.hook(0x9de90,lambda:TRACK)
            m.write(TRACK,DB)  # native track constructor's database owner
            stored=[]
            def store():
                raw=bytes(m.cpu.mem_read(m.reg(1),m.reg(2)))
                stored.append(raw)
                m.cpu.mem_write(0x10030000,raw)
                m.write(m.reg(3),1)
                return 0
            def title():
                m.write(m.reg(2),0x10030000)
                m.write(m.reg(3),len(stored[-1]))
                return 0
            m.hook(0x3056c,store)
            m.hook(0x795dc,title)
            m.cpu.mem_write(TRACK+256,b'\xa5'*16)
            m.write(STACK+0x524,1)
            for reg in REG: m.cpu.reg_write(reg,0)
            for reg,value in ((6,READER),(7,READER+0x40000),(8,DB)):
                m.cpu.reg_write(REG[reg],value)
            m.cpu.reg_write(arm.UC_ARM_REG_SP,STACK)
            m.cpu.emu_start(0xb68c0,0xb6f2c,count=200000)
            self.assertEqual(m.cpu.reg_read(arm.UC_ARM_REG_PC),0xb6f2c)
            self.assertEqual(m.reg(5),0)
            self.assertEqual(m.stream_position,len(m.stream_data))
            self.assertEqual(bytes(m.cpu.mem_read(TRACK+0xec,16)),struct.pack('<4I',*gapless))
            self.assertEqual(bytes(m.cpu.mem_read(TRACK+0xfc,3)),b'\0A\0')
            self.assertEqual(bytes(m.cpu.mem_read(TRACK+256,16)),b'\xa5'*16)
            # Clone with the native 256-byte copy, then reimport a display
            # title: the independently supplied host key must still win.
            destination=0x10050000
            m.cpu.reg_write(REG[4],destination)
            m.cpu.reg_write(REG[5],TRACK)
            m.cpu.reg_write(arm.UC_ARM_REG_SP,STACK)
            m.cpu.emu_start(0xda208,0xda218,count=20000)
            self.assertEqual(bytes(m.cpu.mem_read(destination,256)),bytes(m.cpu.mem_read(TRACK,256)))
            cache_title(m,destination,'Zebra Display Title')
            listing=songs_fixture(self.image,['Placeholder'])
            track=listing.read(listing.wrappers[0]+0x20)
            listing.cpu.mem_write(track,bytes(m.cpu.mem_read(destination,256)))
            listing.call(self.symbols['songs_fast_target'],LIST,0,0,OUT)
            self.assertEqual(listing.read(OUT),ord('A'))

    def test_unrelated_metadata_keeps_native_buffered_skip_and_arm_return(self):
        from native_fixture import NativeMachine
        from verify_sort_letters import attach_stream, attach_native_stream, import_sort, READER
        m=NativeMachine(self.image)
        attach_stream(m)
        attach_native_stream(m,64)
        self.assertEqual(import_sort(m,29,b'ignored metadata'),0)
        self.assertEqual(m.call(0x4d208,READER,0x10060000,len(m.sentinel)),0)
        self.assertEqual(bytes(m.cpu.mem_read(0x10060000,len(m.sentinel))),m.sentinel)

    def test_title_sort_buffered_read_and_skip_preserve_following_record(self):
        from native_fixture import NativeMachine
        from verify_sort_letters import attach_stream, attach_native_stream, import_sort, text_payload, READER, TRACK
        for capacity in (16,4096):
            for data,expected in ((text_payload('Zebra'),ord('Z')|0x80),
                                  (b'Q'*15,ord('B')),
                                  (b'Q'*(0x40000+1),ord('B'))):
                m=NativeMachine(self.image)
                attach_stream(m)
                attach_native_stream(m,capacity)
                m.cpu.mem_write(TRACK+0xff,b'B')
                self.assertEqual(import_sort(m,27,data),0)
                self.assertEqual(bytes(m.cpu.mem_read(TRACK+0xff,1)),bytes([expected]))
                self.assertEqual(m.call(0x4d208,READER,0x10060000,len(m.sentinel)),0)
                self.assertEqual(bytes(m.cpu.mem_read(0x10060000,len(m.sentinel))),m.sentinel)

    def test_cpu_model_keeps_arm_state_on_native_pop_pc(self):
        from native_fixture import NativeMachine
        from verify_native import STACK,RETURN
        from unicorn import arm_const as arm
        m=NativeMachine(self.image)
        # Actual Apple POP {r4,r5,r6,pc}. ARMv4T ignores the target's Thumb
        # marker here; ARMv5+ interworks and would conceal the boot defect.
        m.write(STACK,0,0,0,RETURN|1)
        m.cpu.reg_write(arm.UC_ARM_REG_SP,STACK)
        m.cpu.emu_start(0x92ed8,RETURN,count=8)
        self.assertEqual(m.cpu.reg_read(arm.UC_ARM_REG_PC),RETURN)
        self.assertEqual(m.cpu.reg_read(arm.UC_ARM_REG_CPSR)&0x20,0)

    def test_legacy_title_classifier_is_called_with_an_arm_return_address(self):
        from native_fixture import NativeMachine
        from unicorn import UC_HOOK_CODE, arm_const as arm
        m=NativeMachine(self.image)
        m.cpu.mem_map(0x11000000,0x400000)
        track=0x11100000
        m.write(track,DB)
        calls=[]
        def check(cpu,pc,size,context):
            if pc==0x92e58:
                lr=cpu.reg_read(arm.UC_ARM_REG_LR)
                calls.append(lr)
                self.assertEqual(lr&1,0,
                    'ARMv4T Apple classifier returns with POP pc, which cannot return to Thumb')
        m.cpu.hook_add(UC_HOOK_CODE,check)
        cache_title(m,track,'Alpha')
        self.assertEqual(len(calls),1)

    def test_paint_refreshes_caption_after_same_count_native_index_rebuild(self):
        for rect in ((20,0,128,150),(20,0,30,30)):
            with self.subTest(clip=rect):
                m=songs_fixture(self.image)
                virtual(m,0x130,15,0)
                m.wheel(80)
                m.wheel(80,40)
                state=m.read(LIST+0xe4)
                self.assertEqual(m.read(state+96),ord('C'))
                selection=m.read(ROW+0x8c)
                prior_index=m.read(QUERY+0xaac)
                m.call(0x9129c,QUERY)
                m.wrappers.reverse()
                m.call(0xe2d40,UI)
                self.assertNotEqual(m.read(QUERY+0xaac),prior_index)
                self.assertNotEqual(m.read(QUERY+0xaac),0)
                self.assertEqual(m.read(QUERY+0xab0),80)
                frame,clip=m.graphics(rect)
                m.events.clear()
                before=dict(m.allocations),m.storage_reads
                m.virtual(0x60,MODEL,clip)
                self.assertTrue(active(m))
                self.assertEqual(m.read(state+96),ord('B'))
                self.assertEqual(m.read(ROW+0x8c),selection)
                self.assertEqual((dict(m.allocations),m.storage_reads),before)
                self.assertEqual(m.events.count(('invalidate',LIST)),1,
                                 'partial repaint must schedule the whole popup to be refreshed')
                reference=WheelMachine(self.image)
                reference.wheel()
                reference.write(reference.read(LIST+0xe4)+96,ord('B'))
                ref_frame,ref_clip=reference.graphics(rect)
                reference.virtual(0x60,MODEL,ref_clip)
                self.assertEqual(bytes(m.cpu.mem_read(frame,2560)),bytes(reference.cpu.mem_read(ref_frame,2560)))
                m.events.clear()
                m.virtual(0x60,MODEL,clip)
                self.assertNotIn(('invalidate',LIST),m.events)

    def test_pending_stroke_paint_keeps_original_letter_until_entry(self):
        m=physical_input(songs_fixture(self.image))
        virtual(m,0x130,15,0)
        _,clip=m.graphics()
        for _ in range(7):
            m.move()
            m.virtual(0x60,MODEL,clip)
        self.assertFalse(active(m))
        self.assertEqual(m.read(m.read(LIST+0xe4)+96),ord('A'))
        m.move()
        self.assertTrue(active(m))
        self.assertEqual(m.read(ROW+0x8c),23)
        self.assertEqual(m.read(m.read(LIST+0xe4)+96),ord('B'))

    def test_every_run_boundary_matches_ordered_sequence_oracle(self):
        groups=b'AABBBBAA##!!B'
        m=songs_fixture(self.image,['東京' if x==ord('!') else chr(x) for x in groups])
        from native_fixture import OUT
        for row in range(len(groups)):
            for direction in (-1,0,1):
                with self.subTest(row=row,direction=direction):
                    if direction>0:
                        expected=next((i for i in range(row+1,len(groups))
                                       if groups[i]!=groups[row]),row)
                    elif direction<0:
                        boundaries=[0]+[i for i in range(1,len(groups)) if groups[i]!=groups[i-1]]
                        run=max(i for i in boundaries if i<=row)
                        expected=max((i for i in boundaries if i<run),default=0)
                    else:expected=row
                    result=m.call(self.symbols['songs_fast_target'],LIST,row,direction,OUT)
                    self.assertEqual((result,m.read(OUT)),(expected,groups[expected]))

    def test_current_letter_lookup_stays_within_instruction_budget(self):
        from unicorn import UC_HOOK_CODE
        from native_fixture import OUT
        m=songs_fixture(self.image,['A']*1000+['B']*1000+['C']*1000)
        count=[0]
        h=m.cpu.hook_add(UC_HOOK_CODE,lambda *a:count.__setitem__(0,count[0]+1))
        try:result=m.call(self.symbols['songs_fast_target'],LIST,1500,0,OUT)
        finally:m.cpu.hook_del(h)
        self.assertEqual((result,m.read(OUT)),(1500,ord('B')))
        self.assertLessEqual(count[0],90,'eligibility and paint must avoid loading the same initial twice')

    def test_long_reverse_search_stays_within_instruction_budget(self):
        from unicorn import UC_HOOK_CODE
        from native_fixture import OUT
        m=songs_fixture(self.image,['A']*1000+['B']*1000+['C']*1000)
        count=[0]
        h=m.cpu.hook_add(UC_HOOK_CODE,lambda *a:count.__setitem__(0,count[0]+1))
        try:result=m.call(self.symbols['songs_fast_target'],LIST,1999,-1,OUT)
        finally:m.cpu.hook_del(h)
        self.assertEqual((result,m.read(OUT)),(0,ord('A')))
        self.assertLess(count[0],28000,'reverse run search should avoid repeated loads and bookkeeping')

    def test_native_utf16_title_import_populates_cache_in_both_byte_orders(self):
        from native_fixture import NativeMachine
        from verify_sort_letters import attach_stream, text_payload, READER, TRACK
        from verify_native import STACK, REG
        from unicorn import arm_const as arm
        for endian in ('<','>'):
            with self.subTest(endian=endian):
                m=NativeMachine(self.image)
                attach_stream(m)
                m.write(TRACK,DB)
                m.stream_data=text_payload('  Éclair',endian,1)
                m.cpu.mem_write(READER+0x40083,bytes([endian=='>']))
                stored=[]
                def store_utf16():
                    self.assertEqual(m.reg(0),DB+0xb0)
                    raw=bytes(m.cpu.mem_read(m.reg(1),m.reg(2)))
                    stored.append(raw)
                    m.cpu.mem_write(0x10030000,raw)
                    m.write(m.reg(3),1)
                    return 0
                def get_title():
                    m.write(m.reg(2),0x10030000)
                    m.write(m.reg(3),len(stored[-1]))
                    return 0
                m.hook(0x3056c,store_utf16)
                m.hook(0x795dc,get_title)
                m.write(STACK+0x5cc,DB+0xb0)
                m.write(STACK+0x410,24,24+len(m.stream_data),1)
                m.cpu.reg_write(REG[4],TRACK)
                m.cpu.reg_write(REG[6],READER)
                m.cpu.reg_write(REG[7],READER+0x40000)
                m.cpu.reg_write(arm.UC_ARM_REG_SP,STACK)
                m.cpu.emu_start(0xb6c10,0xb6edc,count=200000)
                self.assertEqual(m.cpu.reg_read(arm.UC_ARM_REG_PC),0xb6edc)
                self.assertEqual(stored,['  Éclair'.encode('utf-16le')])
                self.assertEqual(m.stream_position,len(m.stream_data))
                self.assertEqual(bytes(m.cpu.mem_read(TRACK+0xff,1)),b'E')

    def test_title_cache_preserves_classification_and_adjacent_fields(self):
        m = songs_fixture(self.image, ['A'])
        track = m.read(m.wrappers[0]+0x20)
        for name, expected in [('Alpha','A'), ('  élan','E'), ('123','#'),
                               ('東京','!'), ('🎵 Zebra','Z'), ('','#'),
                               ('\ud800','#')]:
            with self.subTest(name=repr(name)):
                m.cpu.mem_write(track+0xfc, b'XYZ\0')
                m.cpu.mem_write(track+0x11, b'\xa1')
                before = bytes(m.cpu.mem_read(track, 256))
                cache_title(m, track, name)
                after = bytearray(before)
                after[0xff] = ord(expected)
                after[0x11] = 0xa9
                self.assertEqual(bytes(m.cpu.mem_read(track,256)), bytes(after))

    def test_entry_crosses_letter_then_jumps_and_reverses(self):
        m = songs_fixture(self.image)
        virtual(m, 0x130, 15, 0)
        m.wheel(80)
        self.assertFalse(active(m), 'first fast input must scroll normally')
        first = m.read(ROW+0x8c)
        self.assertGreaterEqual(first, 20)
        m.wheel(80, 40)
        self.assertTrue(active(m))
        landed = m.read(ROW+0x8c)
        self.assertGreater(landed, first)
        self.assertEqual(m.read(m.read(LIST+0xe4)+96), ord('ABCD'[landed//20]))
        m.wheel(-80, 10)
        self.assertEqual(m.read(ROW+0x8c), (landed//20-1)*20)
        m.timer()
        self.assertFalse(active(m))

    def test_two_song_list_has_no_minimum_length_gate(self):
        m = songs_fixture(self.image, ['Alpha', 'Beta'])
        m.wheel(80)
        self.assertFalse(active(m))
        self.assertEqual(m.read(ROW+0x8c), 1)
        m.wheel(80, 40)
        self.assertTrue(active(m))
        self.assertEqual(m.read(ROW+0x8c), 1)
        self.assertEqual(m.read(m.read(LIST+0xe4)+96), ord('B'))

    def test_physical_fast_stroke_crosses_letter_and_slow_input_does_not_enter(self):
        for mode in (0, 1):
            m = physical_input(songs_fixture(self.image), mode)
            virtual(m, 0x130, 15, 0)
            for _ in range(7): m.move()
            self.assertFalse(active(m))
            m.move()
            self.assertTrue(active(m))
            self.assertEqual(m.read(ROW+0x8c), 23, 'entry keeps ordinary landing')
            m.move(8, 200)
            self.assertEqual(m.read(ROW+0x8c), 40)
            m.move(-8, 10)
            self.assertEqual(m.read(ROW+0x8c), 20)
            # Release resets the hardware producer but posts no list movement.
            before = m.read(ROW+0x8c)
            m.packet(0x8000001a)
            m.call(0x862dc)
            self.assertEqual(m.read(ROW+0x8c), before)
            m.timer()
            self.assertFalse(active(m))
            m = physical_input(songs_fixture(self.image), mode)
            for _ in range(100): m.move(1, 25)
            self.assertFalse(active(m))

    def test_same_letter_pause_and_reversal_do_not_trigger_entry(self):
        m = physical_input(songs_fixture(self.image, ['A']*100))
        for _ in range(20): m.move()
        self.assertFalse(active(m))
        for pause in (False, True):
            m = physical_input(songs_fixture(self.image))
            virtual(m, 0x130, 18, 0)
            for _ in range(6): m.move()
            if pause: m.move(8, 250)
            for _ in range(6): m.move(8 if pause else -8)
            self.assertFalse(active(m))

    def test_playlist_album_other_resource_and_invalid_queries_stay_ordinary(self):
        cases = ((QUERY+0xa74, 5), (QUERY+0x670, 1), (LIST+0x1c, 0x330b),
                 (QUERY+0xaac, 0), (UI+0x1500, 79), (LIST+0x9c, 1),
                 (LIST+0xa0, 79), (MODEL+0x18, 0), (ROW+0x90, 0))
        for at, value in cases:
            with self.subTest(at=hex(at), value=value):
                m = songs_fixture(self.image)
                m.write(at, value)
                m.wheel(80)
                m.wheel(80, 40)
                self.assertFalse(active(m))

    def test_missing_initials_fall_back_and_repeated_groups_keep_existing_order(self):
        m = songs_fixture(self.image, ['A']*20+['Z']*20+['B']*20+['A']*20)
        virtual(m, 0x130, 15, 0)
        m.wheel(80)
        m.wheel(80, 40)
        self.assertTrue(active(m))
        for delta, row, letter in ((80,60,'A'), (80,60,'A'), (-80,40,'B'),
                                    (-80,20,'Z'), (-80,0,'A'), (-80,0,'A')):
            m.wheel(delta)
            self.assertEqual(m.read(ROW+0x8c),row)
            self.assertEqual(m.read(m.read(LIST+0xe4)+96),ord(letter))
        m = songs_fixture(self.image, ['A']*20)
        for entry in m.wrappers: m.cpu.mem_write(m.read(entry+0x20)+0xff,b'\0')
        for _ in range(4): m.wheel(80,40)
        self.assertFalse(active(m))

    def test_popup_uses_existing_glyph_and_clipping_then_cancels_on_invalidation(self):
        m = songs_fixture(self.image)
        virtual(m,0x130,15,0)
        m.wheel(80)
        m.wheel(80,40)
        letter = m.read(m.read(LIST+0xe4)+96)
        for rect in ((20,0,128,150),(50,70,70,90)):
            frame, clip = m.graphics(rect)
            m.virtual(0x60,MODEL,clip)
            pixels = bytes(m.cpu.mem_read(frame,2560))
            self.assertTrue(any(pixels))
            reference = WheelMachine(self.image)
            reference.wheel()
            reference.write(reference.read(LIST+0xe4)+96,letter)
            ref_frame, ref_clip = reference.graphics(rect)
            reference.virtual(0x60,MODEL,ref_clip)
            self.assertEqual(pixels,bytes(reference.cpu.mem_read(ref_frame,2560)))
        m.call(0x9129c,QUERY)
        m.virtual(0x60,MODEL,clip)
        self.assertFalse(active(m))
        self.assertEqual(m.read(m.read(LIST+0xe4)+120),0)

    def test_timeout_key_hide_and_query_change_cancel_without_moving_selection(self):
        for action in ('timeout','paint','key','hide','query'):
            m = songs_fixture(self.image)
            virtual(m,0x130,15,0)
            m.wheel(80)
            m.wheel(80,40)
            before = m.read(ROW+0x8c)
            if action=='timeout': m.timer()
            elif action=='key':
                # Key handling must still perform the native action after
                # dismissing the popup (this key moves one row backward).
                reference=songs_fixture(self.image)
                virtual(reference,0x130,before,0)
                reference.write(EVENT+0x10,0x57<<16)
                reference.virtual(0x160,EVENT)
                before=reference.read(ROW+0x8c)
                m.write(EVENT+0x10,0x57<<16)
                m.virtual(0x160,EVENT)
            elif action=='hide':
                m.write(LIST+0x68,0x286d8,0,0,0)
                m.virtual(0x94,0)
            else:
                if action=='paint': m.now+=1000
                else: m.write(QUERY+0xa74,5)
                _,clip=m.graphics()
                m.virtual(0x60,MODEL,clip)
            self.assertFalse(active(m))
            self.assertEqual(m.read(ROW+0x8c),before)

    def test_native_viewport_keeps_song_at_top_on_entry_and_both_jump_directions(self):
        from unicorn import UC_HOOK_CODE
        m = songs_fixture(self.image, viewport=True)
        reveals=[]
        m.cpu.hook_add(UC_HOOK_CODE,lambda *a:reveals.append((m.reg(0),m.reg(1))),
                       begin=0xab52c,end=0xab52c)
        m.disc_select(15)
        m.wheel(80)
        m.wheel(80,40)
        state=m.read(m.disc_list+0xe4)
        self.assertTrue(m.read(state+92))
        selected,top=m.disc_position()
        self.assertEqual(selected,top)
        for delta, expected in ((80,60),(80,60),(-80,40),(-80,20),(-80,0)):
            m.wheel(delta)
            self.assertEqual(m.disc_position(),(expected,expected))
        self.assertEqual([row for listing,row in reveals],[selected,60,40,20,0])
        self.assertTrue(all(listing==m.disc_list for listing,row in reveals))

    def test_song_jump_within_visible_page_and_stationary_boundary_do_no_extra_work(self):
        m=songs_fixture(self.image,['A']*20+['B']*20+['C']*10+['D']*30,viewport=True)
        m.disc_select(15)
        m.wheel(80)
        m.wheel(80,40)
        self.assertEqual(m.disc_position(),(47,47))
        m.wheel(80)
        self.assertEqual(m.disc_position(),(50,50),'in-page jump must still align')
        moves=list(m.moves)
        m.events.clear()
        for _ in range(8):m.wheel(5,20)
        self.assertEqual(m.disc_position(),(50,50))
        self.assertEqual(m.moves,moves)
        self.assertFalse(any(e[0] in ('invalidate','click') for e in m.events))

    def test_song_top_reveal_preserves_native_last_page_clamp(self):
        for tail in (1,5):
            with self.subTest(tail=tail):
                m=songs_fixture(self.image,['A']*20+['B']*20+['C']*(40-tail)+['D']*tail,
                                viewport=True)
                m.disc_select(15)
                m.wheel(80)
                m.wheel(80,40)
                m.wheel(80)
                m.wheel(80)
                self.assertEqual(m.disc_position(),(80-tail,74))
                self.assertEqual(m.read(m.disc_list+0x60),128)
                m.wheel(-80)
                self.assertEqual(m.disc_position(),(40,40))

    def test_song_timeout_restores_ordinary_viewport_without_changing_selection(self):
        m=songs_fixture(self.image,viewport=True)
        m.disc_select(15)
        m.wheel(80)
        m.wheel(80,40)
        m.wheel(80)
        self.assertEqual(m.disc_position(),(60,60))
        m.timer()
        self.assertEqual(m.disc_position(),(60,60))
        m.wheel(5,1)
        self.assertEqual(m.disc_position(),(61,60))

    def test_song_reveal_uses_partial_viewport_and_guards_missing_geometry(self):
        from unicorn import UC_HOOK_CODE
        for geometry in ('partial','parent','height'):
            with self.subTest(geometry=geometry):
                m=songs_fixture(self.image,viewport=True)
                if geometry=='partial':
                    m.write(m.disc_parent+0x60,115)
                    m.write(m.disc_list+0x80,115)
                m.disc_select(15)
                m.wheel(80)
                m.wheel(80,40)
                state=m.read(m.disc_list+0xe4)
                self.assertTrue(m.read(state+92))
                selected,top=m.disc_position()
                self.assertEqual(selected,top)
                # At an unchanged endpoint, refresh the caption to exercise
                # the shared guarded redraw without asking native selection
                # to lay out a deliberately incomplete tree first.
                m.wheel(80)
                m.write(state+96,ord('X'))
                if geometry=='parent':m.write(m.disc_list+0x10,0)
                if geometry=='height':
                    m.write(m.disc_row+0x60,m.read(m.disc_row+0x58))
                reveals=[]
                m.cpu.hook_add(UC_HOOK_CODE,lambda *a:reveals.append(m.reg(1)),
                               begin=0xab52c,end=0xab52c)
                m.events.clear()
                m.wheel(80)
                self.assertEqual(reveals,[60] if geometry=='partial' else [])
                self.assertEqual(m.disc_position(),(60,60))
                self.assertEqual(sum(e[0]=='invalidate' for e in m.events),1)

    def test_wheel_and_paint_do_not_allocate_or_fetch_titles(self):
        from unicorn import UC_HOOK_CODE
        m = songs_fixture(self.image,['A']*1000+['B']*1000+['C']*1000)
        virtual(m,0x130,990,0)
        m.wheel(80)
        m.wheel(80,40)
        before=dict(m.allocations),m.storage_reads
        def forbidden(): raise AssertionError('wheel or paint fetched a title')
        m.hook(0x795dc,forbidden)
        m.hook(0x76c90,forbidden)
        count=[0]
        hook=m.cpu.hook_add(UC_HOOK_CODE,lambda *args:count.__setitem__(0,count[0]+1))
        m.wheel(80,200)
        m.cpu.hook_del(hook)
        self.assertEqual(m.read(ROW+0x8c),2000)
        self.assertLess(count[0],30000)
        frame,clip=m.graphics()
        m.virtual(0x60,MODEL,clip)
        self.assertEqual((dict(m.allocations),m.storage_reads),before)

    def test_empty_list_and_failed_accelerator_allocation_fall_back(self):
        m=songs_fixture(self.image,[])
        m.write(ROW+0x90,0)
        m.wheel(80)
        self.assertFalse(active(m))
        m=songs_fixture(self.image)
        m.hook(0xd9a60,lambda:0)
        m.wheel(80)
        self.assertFalse(active(m))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args, rest = parser.parse_known_args()
    SongScroll.image = load_image(args.candidate)
    SongScroll.symbols = json.loads((args.candidate.with_suffix('.artifacts')/'disc-separators-symbols.json').read_text())
    unittest.main(argv=[__file__, *rest], verbosity=2)
