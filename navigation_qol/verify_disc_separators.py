"""Execute 4G album disc grouping, native list selection and song mapping."""
import argparse
import struct
import unittest
from pathlib import Path
from native_fixture import Library, QUERY, DB, OUT, PLAYLIST, HEADER, load_image

LIST, ROW, MODEL, UI = 0x10011000, 0x10012000, 0x10013000, 0x10014000


def fixture(image, discs=(1,1,1,2,2,2), totals=None, album=True, fail_alloc=False):
    m=Library(image,[])
    m.cpu.mem_map(0x11000000,0x400000)
    m.cpu.mem_write(HEADER+0x2e,struct.pack('<H',len(discs)))
    for i in range(len(discs)):
        entry,track,string=0x11000000+64*i,0x11100000+256*i,0x11300000+64*i
        name=f'Track {i+1}'
        raw=name.encode('utf-16le')
        m.strings[i+1]=(string,raw,name)
        m.cpu.mem_write(string,raw)
        m.write(entry,PLAYLIST)
        m.write(entry+0x20,track)
        m.write(track,DB)
        m.wrappers.append(entry)
    m.write(HEADER+0x20,m.wrappers[0] if m.wrappers else 0)
    m.instruction_limit=2000000
    for i,entry in enumerate(m.wrappers):
        track=m.read(entry+0x20)
        m.write(track+0x24,i+1)
        m.write(track+0x30,1)
        m.cpu.mem_write(track+0x88,struct.pack('<HH',discs[i],totals[i] if totals else 2))
    if album:
        raw='Track 1'.encode('utf-16le')
        m.cpu.mem_write(QUERY+0x670,struct.pack('<H',7)+raw)
    def copy_string():
        assert m.reg(0)-DB in (0xb0,0xdc,0x134,0x18c)
        _,data,_=m.strings[m.reg(1)]
        m.cpu.mem_write(m.reg(2),struct.pack('<H',len(data)//2)+data)
        return 0
    m.hook(0x76c90,copy_string)
    m.hook(0x6e400,lambda: m.wrappers[m.reg(3)])
    m.write(UI,0x26294)
    m.write(UI+0x1c,QUERY)
    m.write(UI+0x1500,0xffffffff)
    m.hook(0x39b2c,lambda: None)
    if fail_alloc: m.hook(0x9b8e0,lambda:0)
    m.call(0xe2d40,UI)
    m.write(MODEL,0x22fd4)
    m.write(MODEL+0x18,UI)
    m.write(MODEL+0x1c,LIST)
    m.write(LIST,0x29030)
    m.write(LIST+0x1c,0x3301)
    m.write(LIST+0x20,0x02000800)
    m.write(LIST+0x8c,MODEL,ROW,0)
    m.write(LIST+0x9c,0,visible_count(m))
    m.write(ROW,0x22eac)
    m.write(ROW+0x10,LIST)
    m.write(ROW+0x8c,0,0)
    m.hook(0xc0634,lambda:0x10030000)
    m.hook(0xd13a8,lambda:0)
    m.hook(0xe00e4,lambda:None)
    m.hook(0xbfdd4,lambda:None)
    m.notifications=[]
    m.hook(0xaba1c,lambda:m.notifications.append((m.read(ROW+0x8c),m.read(MODEL+0x14))))
    return m


def visible_count(m):
    assert m.call(0x7492c,UI,0x564d6178,0x3301,OUT)==1
    return m.read(OUT)


def virtual(m,slot,*args):
    table=m.read(LIST)
    return m.call((table+m.read(table+slot))&0xffffffff,LIST,*args)


def label(m,row):
    m.write(MODEL+0x14,row)
    table=m.read(MODEL)
    assert m.call((table+m.read(table+0x50))&0xffffffff,MODEL,0x53747220,0x3367,OUT)==1
    at=m.read(OUT)
    return bytes(m.cpu.mem_read(at,128)).split(b'\0')[0].decode() if at else ''


def constructed_viewport(image, discs=(1,1,1,2,2,2), totals=None, album=True):
    """Construct Apple's Songs tree; execute actual selection and viewport code.

    Native fonts/initial child layout and final subtree translation/raster are
    supplied. The native row-visibility request, viewport clamp and child
    iterator execute ARM. This catches headings clipped above the first song.
    """
    from verify_actions_widgets import ActionsDisplayFixture
    from verify_resources import directory, records, word
    m=fixture(image,discs,totals,album)
    m.hook(0x9f9a8,lambda:0x10018000)
    m.hook(0xd9a60,m.allocate)
    m.hook(0xd97b8,m.allocate)
    m.hook(0xdda9c,lambda:ActionsDisplayFixture.reallocate(m))
    m.hook(0xec,lambda:m.cpu.mem_write(m.reg(0),bytes(m.cpu.mem_read(m.reg(1),m.reg(2)))))
    m.hook(0x6c760,lambda:0)
    m.hook(0x64a48,lambda:UI if m.reg(0)==0x3300 else 0)
    m.hook(0x3be44,lambda:None)
    m.hook(0x65f34,lambda:None)  # Font/layout boundary, supplied below.
    resources=directory(image)
    addresses={(int.from_bytes(tag.encode(),'big'),key&0xffffffff):image.find(raw)
        for tag,(_,entries) in resources.items() for key,raw in entries.items()}
    m.hook(0x72a88,lambda:addresses.get((m.reg(0),m.reg(1)),0))
    m.call(0xba5f0)
    record=next(r for r in records(resources['View'][1][0x250d]) if word(r,12)==0x260e)
    m.cpu.mem_write(0x1001a000,record[4:])
    root=m.call(0x4c454,UI,0,0x1001a000,0x707a8)
    def children(obj):
        return [m.read(m.read(obj+0x70)+4*i) for i in range(m.read(obj+0x6c))]
    _,parent=children(root)
    lst,=children(parent)
    row=m.read(lst+0x90)
    for obj,height in ((root,108),(parent,108),(lst,108),(row,18)):
        m.write(obj+0x58,20,0,20+height,150)
        m.write(obj+0x78,20,0,128,150)
    m.call(0x660f0,root)
    m.write(lst+0x58,20,0,20+m.read(lst+0xa0)*18,150)
    for at in (0x9100c,0xaba04,0x9107c): m.hook(at,lambda:None)
    del m.hooks[0xbfdd4]
    m.moves=[]
    def signed(v): return v if v<0x80000000 else v-0x100000000
    def translate():
        dx,dy=signed(m.reg(1)),signed(m.reg(2))
        m.moves.append((dx,dy))
        for obj in (lst,*children(lst)):
            values=[signed(m.read(obj+0x58+4*i)) for i in range(4)]
            m.write(obj+0x58,*(v+(dy if i%2==0 else dx) for i,v in enumerate(values)))
    m.hook(0xc4260,translate)
    m.disc_list,m.disc_row,m.disc_parent=lst,row,parent
    def select(index):
        table=m.read(lst)
        m.call((table+m.read(table+0x130))&0xffffffff,lst,index,0)
    def position():
        return m.call(0x96fc4,lst),(20-signed(m.read(lst+0x58)))//18
    m.disc_select,m.disc_position=select,position
    return m


class DiscSeparators(unittest.TestCase):
    def test_album_headers_expand_display_only(self):
        m=fixture(self.image)
        self.assertEqual(visible_count(m),8)
        self.assertEqual(m.read(UI+0x1500),6)
        self.assertEqual(m.read(QUERY+0xab0),6)
        index=m.read(QUERY+0xaac)
        self.assertEqual([m.read(index+4*i) for i in range(6)],m.wrappers)
        self.assertEqual([label(m,i) for i in range(8)],['','Track 1','Track 2','Track 3','','Track 4','Track 5','Track 6'])

    def test_single_disc_missing_tags_and_unfiltered_songs_stay_native(self):
        for discs,totals,album in (((1,1,1),(1,1,1),True),((0,0,0),None,True),
                                   ((1,0,2),None,True),((1,1,2),None,False)):
            with self.subTest(discs=discs,album=album):
                m=fixture(self.image,discs,totals,album)
                self.assertEqual(visible_count(m),3)
                self.assertEqual([label(m,i) for i in range(3)],['Track 1','Track 2','Track 3'])

    def test_other_track_lists_with_live_album_filter_keep_native_count_and_names(self):
        m=fixture(self.image)
        self.assertEqual(m.call(0x7492c,UI,0x564d6178,0x330b,OUT),1)
        self.assertEqual(m.read(OUT),6)
        m.write(LIST+0x1c,0x330b)
        self.assertEqual(label(m,1),'Track 2')

    def test_actual_selection_skips_headers_both_directions_and_initially(self):
        m=fixture(self.image)
        for requested,expected in ((0,1),(2,2),(3,3),(4,5),(4,3),(7,7),(0,1)):
            virtual(m,0x130,requested,1)
            self.assertEqual(m.read(ROW+0x8c),expected)
            self.assertEqual(m.read(MODEL+0x14),expected)
            self.assertEqual(m.notifications[-1],(expected,expected))

    def test_native_index_lifetime_owns_header_metadata(self):
        m=fixture(self.image)
        index=m.read(QUERY+0xaac)
        self.assertEqual(visible_count(m),8)
        m.call(0x9129c,QUERY)
        self.assertIn(index,m.freed)
        self.assertEqual((m.read(QUERY+0xaac),m.read(QUERY+0xab0)),(0,0))
        m.cpu.mem_write(QUERY+0x670,b'\0\0')
        m.call(0xe2d40,UI)
        self.assertEqual(visible_count(m),6)

    def test_disc_gaps_nonmonotonic_runs_partial_albums_and_capacity_fallback(self):
        for discs,totals,expected in (((1,1,3,3),(3,)*4,6),((2,2),(0,0),3),
                                      ((1,),(2,),2),((2,1,2),(2,)*3,6),
                                      (tuple(range(1,257)),None,512),
                                      (tuple(range(1,258)),None,257)):
            with self.subTest(count=len(discs),first=discs[0]):
                m=fixture(self.image,discs,totals)
                self.assertEqual(visible_count(m),expected)
                self.assertEqual(m.read(QUERY+0xab0),len(discs))
                if len(discs)<=256:
                    for i in range(expected): label(m,i)

    def test_empty_and_allocation_failure_leave_native_empty_list(self):
        for discs,fail in (((),False),((1,2),True)):
            m=fixture(self.image,discs,fail_alloc=fail)
            self.assertEqual(visible_count(m),0)
            self.assertEqual(m.read(QUERY+0xab0),0)

    def test_renderer_selected_index_is_native_song_index(self):
        m=fixture(self.image)
        renderer=0x10018000
        m.write(renderer,0x23d7c)
        m.write(renderer+0x24,LIST)
        before=m.storage_reads
        for visual,song in ((1,0),(2,1),(3,2),(5,3),(6,4),(7,5)):
            virtual(m,0x130,visual,1)
            self.assertEqual(m.call(0x76374,renderer),song)
            self.assertEqual(m.call(0x96fc4,LIST),visual)
        self.assertEqual(m.storage_reads,before)

    def test_hold_center_feedback_flashes_the_selected_song_after_headers(self):
        m=fixture(self.image)
        renderer=0x10018000
        m.write(renderer,0x23d7c)
        m.write(renderer+0x24,LIST)
        # Only OS timer scheduling is supplied; native blink state executes.
        m.hook(0xd3aa0,lambda:None)
        m.hook(0xd2bd0,lambda:None)
        for song,visual in ((0,1),(1,2),(2,3),(3,5),(4,6),(5,7)):
            m.call(0x1b698,renderer,song)
            self.assertEqual(m.read(LIST+0xdc),visual)
            self.assertEqual(bytes(m.cpu.mem_read(LIST+0xe0,1)),b'\x06')

    def test_native_next_previous_and_wrapping_skip_headings(self):
        m=fixture(self.image)
        virtual(m,0x130,0,1)
        for want in (2,3,5,6,7,1):
            m.call(0x9c904,LIST,1)
            self.assertEqual(m.read(ROW+0x8c),want)
        for want in (7,6,5,3,2,1):
            m.call(0x9ca10,LIST,1)
            self.assertEqual(m.read(ROW+0x8c),want)
        m.write(LIST+0x20,m.read(LIST+0x20)|0x04000000)
        m.call(0x9ca10,LIST,1)
        self.assertEqual(m.read(ROW+0x8c),1)

    def test_previous_at_first_song_keeps_native_boundary_result_and_notifications(self):
        # Native previous must see the first selectable song as its lower
        # bound before deciding movement, wrapping and LstF notification.
        cases=(('multi', (1,1,2), None, True, 0x3301, 1, 4),
               ('partial', (2,), (2,), True, 0x3301, 1, 1),
               ('single', (1,1,1), (1,1,1), True, 0x3301, 0, 2),
               ('unfiltered', (1,1,2), None, False, 0x3301, 0, 2),
               ('alternate', (1,1,2), None, True, 0x330b, 0, 2))
        for name,discs,totals,album,resource,first,last in cases:
            for clamp in (False,True):
                for step in (1,2,100):
                    with self.subTest(case=name,clamp=clamp,step=step):
                        m=fixture(self.image,discs,totals,album)
                        m.write(LIST+0x1c,resource)
                        if resource==0x330b: m.write(LIST+0xa0,len(discs))
                        if clamp: m.write(LIST+0x20,m.read(LIST+0x20)|0x04000000)
                        virtual(m,0x130,first,0)
                        notifications=[]
                        m.hook(0xaba1c,lambda:notifications.append(m.reg(1)))
                        moved=m.call(0x9ca10,LIST,step)
                        self.assertEqual(m.read(ROW+0x8c),first if clamp else last)
                        self.assertEqual(moved,0 if clamp or first==last else 1)
                        self.assertEqual(notifications,[0x4c737453,0x4c737446] if clamp else [0x4c737453])
                        # Geometry still includes the heading row above song 1.
                        self.assertEqual(m.read(LIST+0x9c),0)

    def test_wheel_at_first_song_does_not_click_and_resumes_normal_movement(self):
        m=fixture(self.image)
        m.write(LIST+0x20,m.read(LIST+0x20)|0x04000000)
        virtual(m,0x130,0,0)
        events=[]
        m.hook(0xaba1c,lambda:events.append(m.reg(1)))
        # Only acceleration, OS gating and click output are supplied. The
        # wheel dispatcher, previous/next methods and selection execute ARM.
        m.write(LIST+0xe4,0x1001d000)
        m.hook(0xe507c,lambda:100)
        m.hook(0x8f35c,lambda:-1)
        m.hook(0x86394,lambda:-1)
        m.hook(0x92b10,lambda:0)
        m.hook(0x625f4,lambda:events.append(('click',m.reg(0))))
        self.assertEqual(m.call(0x86670,LIST,0x1001e000),1)
        self.assertEqual(m.read(ROW+0x8c),1)
        self.assertEqual(events,[0x4c737453,0x4c737446])
        self.assertEqual(m.call(0x9c904,LIST,1),1)
        self.assertEqual(m.read(ROW+0x8c),2)
        events.clear()
        self.assertEqual(m.call(0x86670,LIST,0x1001e000),1)
        self.assertEqual(m.read(ROW+0x8c),1)
        self.assertEqual(events,[0x4c737453,('click',1)])

    def test_song_center_uses_real_selected_row_through_native_script(self):
        from verify_actions_playback import playback_machine,NativePlaybackEntry
        from native_fixture import RENDERER
        for visual,song in ((1,0),(3,1),(4,2)):
            m=playback_machine(self.image,2075)
            for i,entry in enumerate(m.wrappers):
                track=m.read(entry+0x20)
                m.write(track+0x30,1)
                m.cpu.mem_write(track+0x88,struct.pack('<HH',1 if i==0 else 2,2))
            m.cpu.mem_write(QUERY+0x670,struct.pack('<HH',1,ord('A')))
            m.call(0xe2d40,UI)
            # Actual Renderer-AA -> native list selection -> display/song mapping.
            del m.hooks[m.method_address(3,0xaa)]
            list_at,row_at,model_at=0x10050000,0x10051000,0x10052000
            m.write(RENDERER,0x23d7c)
            m.write(RENDERER+0x24,list_at)
            m.write(list_at,0x29030)
            m.write(list_at+0x1c,0x3301)
            m.write(list_at+0x8c,model_at,row_at,0)
            m.write(list_at+0x9c,0,5)
            m.write(row_at,0x22eac)
            m.write(row_at+0x8c,visual,1)
            m.write(model_at,0x22fd4)
            m.write(model_at+0x14,visual,UI)
            m.write(model_at+0x1c,list_at)
            self.assertEqual(m.run(2075),0xc0000002)
            self.assertEqual(m.run(2076,m.event),0xc0000002)
            checker=NativePlaybackEntry()
            checker.image=self.image
            checker.check_now_playing(m,[0,1,2],song,song)

    def test_constructed_viewport_keeps_first_heading_on_open_and_return(self):
        m=constructed_viewport(self.image)
        m.disc_select(0)
        self.assertEqual(m.disc_position(),(1,0))
        for row in (2,3,5,6,7,6,5,3,2,1): m.disc_select(row)
        self.assertEqual(m.disc_position(),(1,0),
            'the first heading must remain above the selected first song')
        # Reopening a retained list/history at the first song must also repair
        # the old top=1 state, without making a heading selectable.
        m.write(m.disc_list+0x58,2,0,146,150)
        m.write(m.disc_row+0x58,2,0,20,150)
        m.disc_select(1)
        self.assertEqual(m.disc_position(),(1,0))
        self.assertEqual(m.read(m.disc_list+0x9c),0)

    def test_constructed_viewport_reveals_disc_header_and_first_song_together(self):
        m=constructed_viewport(self.image,(1,)*6+(2,)*6+(3,)*6)
        for row in (20,15,14,13,9,8): m.disc_select(row)
        self.assertEqual(m.disc_position(),(8,7))
        m.disc_select(15)
        self.assertLessEqual(m.disc_position()[1],14)
        self.assertGreaterEqual(m.disc_position()[1]+5,15)
        # Ordinary single-disc and unfiltered song lists retain native reveal.
        for discs,totals,album in (((1,)*12,(1,)*12,True),((1,)*6+(2,)*6,None,False)):
            m=constructed_viewport(self.image,discs,totals,album)
            m.disc_select(11)
            m.disc_select(0)
            self.assertEqual(m.disc_position(),(0,0))

    def test_full_native_list_draw_renders_headers_and_preserves_selected_model(self):
        from verify_letters import WheelMachine
        from verify_resources import directory
        m=fixture(self.image)
        m.hook(0x9f9bc,lambda:0)
        m.hook(0xec,lambda:m.cpu.mem_write(m.reg(0),bytes(m.cpu.mem_read(m.reg(1),m.reg(2)))))
        resources=directory(self.image)
        addresses={(int.from_bytes(tag.encode(),'big'),key):self.image.find(raw)
                   for tag,(_,entries) in resources.items() for key,raw in entries.items()}
        m.hook(0x72a88,lambda:addresses.get((m.reg(0),m.reg(1)),0))
        m.call(0x1cb84)
        framebuffer,clip=WheelMachine.graphics(m,(20,0,128,150))
        del m.hooks[0x5cdc4]
        m.write(LIST+0x58,20,0,164,150)
        m.write(LIST+0x78,20,0,128,150)
        m.write(ROW+0x58,20,0,38,150)
        m.write(ROW+0x8c,3,1)
        m.write(MODEL+0x14,3)
        m.hook(0xe3ddc,lambda:1)
        m.hook(0xc9c2c,lambda:None)
        m.hook(0x5ad60,lambda:None)
        m.hook(0x87824,lambda:None)
        m.hook(0xc98b0,lambda:m.cpu.mem_write(m.reg(0)+0x58,bytes(m.cpu.mem_read(m.reg(1),16))))
        drawn=[]
        m.hook(0x5c8b0,lambda:drawn.append(m.read(MODEL+0x14)))
        m.call(0x5cdc4,LIST,MODEL,clip)
        self.assertEqual(drawn,[1,2,3,5])
        self.assertEqual(m.read(MODEL+0x14),3)
        pixels=bytes(m.cpu.mem_read(framebuffer,2560))
        rows={y for y in range(128) if any(pixels[y*20:(y+1)*20])}
        self.assertTrue(rows)
        self.assertTrue(all(20<=y<38 or 92<=y<110 for y in rows),rows)
        self.assertEqual(bytes(m.cpu.mem_read(framebuffer-16,16)),b'\xa5'*16)
        self.assertEqual(bytes(m.cpu.mem_read(framebuffer+2560,16)),b'\xa5'*16)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate',type=Path)
    args,rest=p.parse_known_args()
    DiscSeparators.image=load_image(args.candidate)
    unittest.main(argv=[__file__,*rest],verbosity=2)
