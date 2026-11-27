"""Build an isolated alarm extension and execute original+patched 5.5G ARM.

OS scheduling and hardware light drivers are supplied boundaries. This does
not prove device timing, boot or output reliability. Run with .venv and JIT.
The output directory must be new; failures are retained there.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import unittest
from zipfile import ZipFile

from unicorn import arm_const as arm, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE

from alarm_patch import install, SOURCES
from layout import link, install_elf
from research_alarm import stock, native_classes

REG = [getattr(arm, f'UC_ARM_REG_R{i}') for i in range(13)]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--candidate',type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    original=stock(Path('iPod_25.1.3.ipsw'))
    if args.candidate:
        artifacts=args.candidate.with_suffix('.artifacts')
        manifest=json.loads((artifacts/'build.json').read_text())
        assert hashlib.sha256(args.candidate.read_bytes()).hexdigest()==manifest['archive_sha256']
        with ZipFile(args.candidate) as archive:
            firmware=archive.read('Firmware-25.6.3')
        offset,size=struct.unpack_from('<II',firmware,0x420c)
        image=firmware[offset+0x800:offset+0x800+size]
        assert hashlib.sha256(image).hexdigest()==manifest['osos_sha256']
        symbols=json.loads((artifacts/'extension-symbols.json').read_text())
        receipt={'candidate':str(args.candidate),'candidate_archive_sha256':manifest['archive_sha256']}
    else:
        fixture=args.output/'enabled_fixture.c'
        fixture.write_text('unsigned int fixture_alarm_enabled = 1;\nint ipod55g_alarm_enabled(void) { return fixture_alarm_enabled != 0; }\n')
        source=Path(__file__).resolve().parent
        elf=link([*[source/name for name in SOURCES],fixture],args.output,entry='ipod55g_alarm_signal')
        image,symbols=install_elf(original,elf)
        receipt=install(image,symbols)
        image=bytes(image)
    (args.output/'osos.bin').write_bytes(image)
    Base,_=native_classes(image)

    class Machine(Base):
        def __init__(self):
            super().__init__()
            self.cpu.mem_map(0x10100000,0x700000)
            self.cpu.mem_write(0x10000000,image)
            self.write(0x1081dfb0,0,0,0,0)
            self.cpu.mem_write(0x1081e028,b'\x01')
            self.signals=[]
            self.hooks[0xa67b0]=lambda:self.signals.append(self.reg(0)) or 0
            self.light_calls=[]
            self.hooks[symbols['ipod55g_alarm_original_power']]=lambda:self.light_calls.append(('power',self.reg(0)))
            self.hooks[symbols['ipod55g_alarm_original_brightness']]=lambda:self.light_calls.append(('brightness',self.reg(0)))
            self.lock_calls=[]
            self.hooks[0xa63e0]=lambda:0
            self.hooks[0xa6420]=lambda:7
            self.hooks[0xa6540]=lambda:self.lock_calls.append(('lock',self.reg(0)))
            self.hooks[0xa687c]=lambda:self.lock_calls.append(('unlock',self.reg(0)))
            self.write(0x1081ff40,1,20,20,91)
            self.write(0x1081d8c8,20)

        def s(self,name):return symbols[name]
        def get(self,name):return self.read(self.s(name))
        def set(self,name,value):
            if args.candidate and name=='fixture_alarm_enabled':
                self.write(symbols['alarm_disabled'],0 if value else 0x20000000)
            else:self.write(self.s(name),value)
        def fn(self,name,*args):return self.call(self.s(name),*args)
        def time(self,value):self.write(0x60005010,value)
        def service_light(self,events=(0x188,0xa6)):
            events=iter(events)
            def wait():
                self.assert_list=struct.unpack('<7I',self.cpu.mem_read(self.reg(0),28))
                assert self.assert_list==(0xa6,0xa7,0xa8,0xa9,0xaa,0x188,0)
                return next(events)
            self.hooks[0xa6958]=wait
            return self.fn('ipod55g_alarm_light_wait',0)

        def preempt(self,callback):
            # A different native task has a separate stack. Preserve both the
            # suspended CPU context and the fixture's shared scratch stack.
            context=self.cpu.context_save()
            stack=bytes(self.cpu.mem_read(0x100ed000,0x1000))
            try:callback()
            finally:
                self.cpu.mem_write(0x100ed000,stack)
                self.cpu.context_restore(context)

        def set_brightness(self,percent):
            self.write(0x1081de68,0x000b0011)
            self.write(0x1081d8c0,0x10001000)
            self.cpu.mem_write(0x1000100c,struct.pack('<2h',31,0))
            # Real display method -> percentage setter -> immediate native
            # power/brightness helper. This path does not acquire mutex136.
            return self.call(0x164428,0x10001080,percent)

        def leave_completion_repair_pending(self):
            self.set('ipod55g_alarm_light_phase',2);self.service_light()
            at=self.s('ipod55g_alarm_original_brightness');original=self.hooks[at]
            interrupted=[]
            def physical():
                if not interrupted:
                    interrupted.append(True)
                    def restore():
                        self.set('ipod55g_alarm_light_phase',0);self.service_light()
                    self.preempt(restore)
                return original()
            self.hooks[at]=physical
            self.set_brightness(30)
            self.hooks[at]=original

    class Alarm(unittest.TestCase):
        def test_signal_table_initialization_and_native_coalescence(self):
            m=Machine();table=m.s('ipod55g_alarm_signal_table')
            m.write(table+393*4,0x1234abcd)
            m.write(0x108b777c,0xabcdef12)
            m.call(0x2812dc)
            self.assertEqual(m.read(table),0)
            self.assertEqual(bytes(m.cpu.mem_read(table+4,392*4)),b'\xff'*(392*4))
            self.assertEqual(m.read(table+393*4),0x1234abcd)
            self.assertEqual(m.read(0x108b777c),0xabcdef12)
            m.call(0x286034,0x188)
            m.call(0x286034,0x188)
            self.assertEqual(m.read(table+0x188*4),0)
            request,task=0x10004000,0x10005000
            m.write(request,0x22,0,0,m.s('ipod55g_alarm_light_events'))
            m.write(0x1081daf4+12,task)
            m.write(task+0x20,7)
            m.cpu.reg_write(REG[4],request);m.cpu.reg_write(REG[5],0x1081daf4)
            m.cpu.emu_start(0x27f834,0x280f18,count=10000)
            self.assertEqual(m.read(request+8),0x188)
            for signal in (0xa6,0xa7,0xa8,0xa9,0xaa,0x188):
                self.assertEqual(m.read(table+signal*4),0xffffffff)

        def test_pattern_tags_exact_stock_requests_and_preserves_watchdog(self):
            m=Machine();calls=[]
            m.hooks[0xcd430]=lambda:calls.append((m.reg(0),m.reg(1))) or 1
            m.fn('ipod55g_clock_alarm_signal')
            self.assertEqual(calls,[(180,0x80030d40),(144,0x80061a80)]*4)
            self.assertEqual(image[0x91438:0x914ac],original[0x91438:0x914ac])
            self.assertEqual(image[0x28449c:0x2844a0],original[0x28449c:0x2844a0])

        def test_pomodoro_adapter_only_publishes_tones_after_native_rtc_wake(self):
            m=Machine();calls=[]
            m.hooks[0xa77e8]=lambda:calls.append(('wake',m.reg(0)))
            m.hooks[0xcd430]=lambda:calls.append((m.reg(0),m.reg(1))) or 0
            m.fn('ipod55g_alarm_signal')
            self.assertEqual(calls,[(180,0x80030d40),(144,0x80061a80)]*4)

        def test_four_pulses_keep_full_original_buzzer_waveforms(self):
            m=Machine();m.time(1000000)
            for _ in range(4):
                m.queue(180,0x80030d40);m.queue(144,0x80061a80)
            start=1000000
            for pair in range(4):
                now=start+pair*600000;m.time(now)
                m.fn('ipod55g_alarm_worker')
                self.assertEqual(m.get('ipod55g_alarm_light_phase'),1)
                self.assertEqual(m.read(0x7000a000),0x808000b4)
                self.assertEqual(m.read(0x60005008),0x80030d40)
                m.time(now+200000);m.fn('ipod55g_alarm_worker')
                self.assertEqual(m.read(0x7000a000),0x80800090)
                self.assertEqual(m.read(0x60005008),0x800249f0)
                self.assertEqual(m.read(0x1081dfbc),now+600000)
                before=[x for x in m.writes if x[0]==0x7000a000]
                m.time(now+350000);m.fn('ipod55g_alarm_worker')
                self.assertEqual(m.get('ipod55g_alarm_light_phase'),2)
                self.assertEqual(m.read(0x60005008),0x8003d090)
                self.assertEqual([x for x in m.writes if x[0]==0x7000a000],before)
            m.time(start+2400000);m.fn('ipod55g_alarm_worker')
            self.assertEqual(m.get('ipod55g_alarm_light_phase'),0)
            self.assertEqual(m.read(0x7000a000),0)
            self.assertEqual(m.read(0x1081dfb8),0)
            self.assertEqual(m.lock_calls,[]) # Sound never waits for light mutex.

        def test_disable_during_split_rearms_sound_deadline(self):
            m=Machine();m.time(1000000);m.queue(144,0x80061a80)
            m.fn('ipod55g_alarm_worker')
            m.set('fixture_alarm_enabled',0);m.fn('ipod55g_alarm_setting_changed')
            self.assertEqual(m.get('ipod55g_alarm_light_phase'),0)
            m.time(1150000);m.fn('ipod55g_alarm_worker')
            self.assertEqual(m.read(0x60005008),0x8003d090)
            self.assertEqual(m.read(0x7000a000),0x80800090)
            m.time(1400000);m.fn('ipod55g_alarm_worker')
            self.assertEqual(m.read(0x7000a000),0)

        def test_off_preference_and_suspended_lcd_have_no_extra_wakes(self):
            for pref,lcd in ((0,1),(1,0)):
                with self.subTest(pref=pref,lcd=lcd):
                    m=Machine();m.set('fixture_alarm_enabled',pref)
                    m.cpu.mem_write(0x1081e028,bytes([lcd]))
                    m.time(1);m.queue(144,0x80061a80);m.fn('ipod55g_alarm_worker')
                    self.assertEqual(m.read(0x60005008),0x80061a80)
                    self.assertEqual(m.signals,[])
                    self.assertEqual(m.get('ipod55g_alarm_tone_state'),0)

        def test_delayed_callback_skips_dark_gap_and_preserves_next_tone(self):
            m=Machine();m.time(1000000)
            m.queue(144,0x80061a80);m.queue(180,0x80030d40)
            m.fn('ipod55g_alarm_worker')
            m.time(1450000);m.fn('ipod55g_alarm_worker')
            self.assertEqual(m.get('ipod55g_alarm_light_phase'),1)
            self.assertEqual(m.read(0x7000a000),0x808000b4)
            self.assertEqual(m.read(0x1081dfbc),1650000)

        def test_ordinary_tone_restores_visual_and_keeps_its_native_duration(self):
            m=Machine();m.time(1)
            m.queue(180,0x80030d40);m.queue(55,3000)
            m.fn('ipod55g_alarm_worker');m.time(200001);m.fn('ipod55g_alarm_worker')
            self.assertEqual(m.get('ipod55g_alarm_light_phase'),0)
            self.assertEqual(m.get('ipod55g_alarm_tone_state'),0)
            self.assertEqual(m.read(0x7000a000),0x80800037)
            self.assertEqual(m.read(0x60005008),0x80000bb8)

        def test_rejected_tagged_request_never_starts_visual_cue(self):
            m=Machine();m.time(1)
            for _ in range(15):self.assertEqual(m.queue(55,3000),0)
            self.assertEqual(m.queue(180,0x80030d40),1)
            m.fn('ipod55g_alarm_worker')
            self.assertEqual(m.get('ipod55g_alarm_light_phase'),0)
            self.assertEqual(m.signals,[])

        def test_split_deadline_wrap_preserves_dark_tail_and_drain(self):
            m=Machine();start=0xffff0000;m.time(start)
            m.queue(144,0x80061a80);m.fn('ipod55g_alarm_worker')
            m.time((start+150000)&0xffffffff);m.fn('ipod55g_alarm_worker')
            self.assertEqual(m.get('ipod55g_alarm_light_phase'),2)
            self.assertEqual(m.read(0x60005008),0x8003d090)
            m.time((start+400000)&0xffffffff);m.fn('ipod55g_alarm_worker')
            self.assertEqual(m.get('ipod55g_alarm_light_phase'),0)
            self.assertEqual(m.read(0x7000a000),0)

        def test_cancellation_clears_visual_mailbox_and_keeps_native_sound_behavior(self):
            m=Machine();m.time(1);m.queue(144,0x80061a80)
            m.fn('ipod55g_alarm_worker')
            m.queue(0,0,cancel=True)
            self.assertEqual(m.get('ipod55g_alarm_light_phase'),0)
            self.assertEqual(m.get('ipod55g_alarm_tone_state'),0)
            self.assertEqual(m.read(0x1081dfb8),0)
            self.assertEqual(m.read(0x7000a000),0x80800090)

        def test_private_light_events_project_latest_phase_and_preserve_stock_events(self):
            m=Machine();m.set('ipod55g_alarm_light_phase',1)
            self.assertEqual(m.service_light(),0xa6)
            self.assertEqual(m.light_calls,[('power',1),('brightness',20)])
            self.assertEqual(m.lock_calls,[('lock',0x136),('unlock',0x136)])
            m.light_calls.clear();m.set('ipod55g_alarm_light_phase',2)
            m.service_light(events=(0x188,0xaa))
            self.assertEqual(m.light_calls,[('power',0)])
            # Normal fade ownership is preserved: no recursive lock/unlock.
            m.hooks[0xa63e0]=lambda:7;m.lock_calls.clear()
            m.set('ipod55g_alarm_light_phase',1);m.service_light()
            self.assertEqual(m.lock_calls,[])

        def test_restore_uses_latest_native_policy_and_brightness(self):
            m=Machine();m.set('ipod55g_alarm_light_phase',1);m.service_light()
            m.call(0x26617c,9);m.call(0x266288,0)
            self.assertEqual(m.light_calls[-2:],[('brightness',20),('power',1)])
            m.light_calls.clear();m.set('ipod55g_alarm_light_phase',0);m.service_light()
            self.assertEqual(m.light_calls,[('brightness',9),('power',0)])
            # Following native commands are no longer projected.
            m.call(0x266288,1);m.call(0x26617c,11)
            self.assertEqual(m.light_calls[-2:],[('power',1),('brightness',11)])

        def test_policy_request_before_first_light_delivery_is_not_lost(self):
            m=Machine();m.call(0x26617c,7);m.call(0x266288,0)
            m.set('ipod55g_alarm_light_phase',1);m.service_light()
            m.set('ipod55g_alarm_light_phase',0);m.light_calls.clear();m.service_light()
            self.assertEqual(m.light_calls,[('brightness',7),('power',0)])

        def test_restore_retries_if_native_request_changes_during_restore(self):
            m=Machine();m.set('ipod55g_alarm_light_phase',1);m.service_light()
            m.set('ipod55g_alarm_light_phase',0);m.light_calls.clear()
            changed=[False]
            def power():
                m.light_calls.append(('power',m.reg(0)))
                if not changed[0]:
                    changed[0]=True
                    def request():
                        m.call(0x26617c,6);m.call(0x266288,0)
                    m.preempt(request)
            m.hooks[symbols['ipod55g_alarm_original_power']]=power
            m.service_light()
            self.assertEqual(m.light_calls[-2:],[('brightness',6),('power',0)])

        def test_inflight_native_outputs_repair_after_owner_already_restored(self):
            for output in ('power','brightness'):
                with self.subTest(output=output):
                    m=Machine();m.set('ipod55g_alarm_light_phase',2);m.service_light()
                    original=m.hooks[m.s('ipod55g_alarm_original_'+output)]
                    interrupted=[];signals_after_restore=[]
                    def physical():
                        if not interrupted:
                            interrupted.append(True)
                            def restore():
                                m.set('ipod55g_alarm_light_phase',0);m.service_light()
                                self.assertEqual(m.get('projected'),0)
                                signals_after_restore.append(len(m.signals))
                            m.preempt(restore)
                        return original()
                    m.hooks[m.s('ipod55g_alarm_original_'+output)]=physical
                    m.set_brightness(30)  # Native 0..31 scale becomes 9.
                    self.assertGreater(len(m.signals),signals_after_restore[0])
                    self.assertEqual(m.signals[-1],0x188)
                    self.assertEqual(m.get('ipod55g_alarm_light_phase'),0)
                    m.service_light()
                    self.assertEqual(m.light_calls[-2:],[('power',1),('brightness',9)])
                    self.assertEqual(m.get('projected'),0)

        def test_inflight_completion_repair_keeps_newer_phase_and_policy(self):
            for newer_phase in (0,1):
                with self.subTest(newer_phase=newer_phase):
                    m=Machine();m.set('ipod55g_alarm_light_phase',2);m.service_light()
                    original=m.hooks[m.s('ipod55g_alarm_original_brightness')]
                    interrupted=[];signals_after_change=[]
                    def physical():
                        if not interrupted:
                            interrupted.append(True)
                            def change():
                                m.set('ipod55g_alarm_light_phase',newer_phase);m.service_light()
                                m.set_brightness(60)  # Newer native request is 18.
                                signals_after_change.append(len(m.signals))
                            m.preempt(change)
                        return original()
                    m.hooks[m.s('ipod55g_alarm_original_brightness')]=physical
                    m.set_brightness(30)
                    self.assertGreater(len(m.signals),signals_after_change[0])
                    self.assertEqual(m.get('ipod55g_alarm_light_phase'),newer_phase)
                    m.service_light()
                    self.assertEqual(m.light_calls[-2:],[('power',1),('brightness',18)])

        def test_inflight_output_detects_a_complete_intervening_projection(self):
            m=Machine();original=m.hooks[m.s('ipod55g_alarm_original_brightness')]
            interrupted=[];signals_after_restore=[]
            def physical():
                if not interrupted:
                    interrupted.append(True)
                    self.assertEqual(m.get('projected'),0)
                    def cue():
                        m.set('ipod55g_alarm_light_phase',1);m.service_light()
                        m.set_brightness(60)  # Newer stock policy, brightness18.
                        m.set('ipod55g_alarm_light_phase',0);m.service_light()
                        self.assertEqual(m.get('projected'),0)
                        signals_after_restore.append(len(m.signals))
                    m.preempt(cue)
                return original()
            m.hooks[m.s('ipod55g_alarm_original_brightness')]=physical
            m.set_brightness(30)
            self.assertGreater(len(m.signals),signals_after_restore[0])
            self.assertEqual(m.get('ipod55g_alarm_light_phase'),0)
            m.service_light()
            self.assertEqual(m.light_calls[-2:],[('power',1),('brightness',18)])

        def test_repair_retries_after_three_concurrent_policy_changes(self):
            m=Machine();m.leave_completion_repair_pending();m.signals.clear()
            at=m.s('ipod55g_alarm_original_brightness');original=m.hooks[at]
            changes=[];inside=[False]
            def physical():
                if not inside[0] and len(changes)<3:
                    inside[0]=True;changes.append(5+len(changes)+1)
                    m.preempt(lambda:m.call(0x26617c,changes[-1]))
                    inside[0]=False
                return original()  # Older output finishes after the newer one.
            m.hooks[at]=physical
            m.service_light()
            self.assertEqual(changes,[6,7,8])
            self.assertEqual(m.light_calls[-1],('brightness',7))
            self.assertIn(0x188,m.signals)
            m.service_light()
            self.assertEqual(m.light_calls[-2:],[('power',1),('brightness',8)])

        def test_inflight_output_detects_a_complete_repair_only_interval(self):
            m=Machine();m.leave_completion_repair_pending()
            at=m.s('ipod55g_alarm_original_brightness');original=m.hooks[at]
            interrupted=[];signals_after_repair=[]
            def physical():
                if not interrupted:
                    interrupted.append(True)
                    def repair():
                        m.service_light()
                        m.set_brightness(90)  # Newest native brightness is27.
                        signals_after_repair.append(len(m.signals))
                    m.preempt(repair)
                return original()
            m.hooks[at]=physical
            m.set_brightness(60)
            self.assertGreater(len(m.signals),signals_after_repair[0])
            self.assertEqual(m.get('ipod55g_alarm_light_phase'),0)
            m.service_light()
            self.assertEqual(m.light_calls[-2:],[('power',1),('brightness',27)])

        def test_first_capture_cannot_overwrite_a_native_policy_request(self):
            for output,percent,value in (('power',0,0),('brightness',30,9)):
                with self.subTest(output=output):
                    m=Machine();pending=[False];interrupted=[]
                    def seen_read(cpu,access,address,size,read_value,context):
                        if not interrupted:pending[0]=True
                    def next_instruction(cpu,pc,size,context):
                        if pending[0] and not interrupted:
                            interrupted.append(True);pending[0]=False
                            m.preempt(lambda:m.set_brightness(percent))
                    at=m.s(output+'_seen')
                    m.cpu.hook_add(UC_HOOK_MEM_READ,seen_read,begin=at,end=at)
                    m.cpu.hook_add(UC_HOOK_CODE,next_instruction)
                    m.set('ipod55g_alarm_light_phase',1);m.service_light()
                    m.set('ipod55g_alarm_light_phase',0);m.service_light()
                    self.assertEqual(interrupted,[True])
                    m.service_light()  # Any completion repair must retain policy.
                    self.assertEqual(m.get('stock_'+output),value)
                    latest=dict(m.light_calls)
                    self.assertEqual(latest[output],value)

        def test_writer_starting_at_owner_epoch_publication_gets_completion_repair(self):
            m=Machine();m.leave_completion_repair_pending()
            def save_task():
                return m.cpu.context_save(),bytes(m.cpu.mem_read(0x100ed000,0x1000))
            def resume(task):
                context,stack=task
                m.cpu.mem_write(0x100ed000,stack);m.cpu.context_restore(context)
                m.cpu.emu_start(m.cpu.reg_read(arm.UC_ARM_REG_PC),0x100ff000,count=200000)
                self.assertEqual(m.cpu.reg_read(arm.UC_ARM_REG_PC),0x100ff000)
            def start(at,*values):
                for reg in REG:m.cpu.reg_write(reg,0)
                for n,value in enumerate(values):m.cpu.reg_write(REG[n],value)
                m.cpu.reg_write(arm.UC_ARM_REG_SP,0x100ee000)
                m.cpu.reg_write(arm.UC_ARM_REG_LR,0x100ff000)
                m.cpu.emu_start(at,0x100ff000,count=200000)
            # Stop the owner immediately after publishing its new epoch.
            # A writer beginning here must already observe active ownership.
            pending=[False]
            def epoch_write(cpu,access,address,size,value,context):pending[0]=True
            def after_epoch(cpu,pc,size,context):
                if pending[0]:cpu.emu_stop()
            at=m.s('projection_epoch')
            read_hook=m.cpu.hook_add(UC_HOOK_MEM_WRITE,epoch_write,begin=at,end=at)
            code_hook=m.cpu.hook_add(UC_HOOK_CODE,after_epoch)
            events=iter((0x188,0xa6));m.hooks[0xa6958]=lambda:next(events)
            start(m.s('ipod55g_alarm_light_wait'),0)
            self.assertTrue(pending[0]);owner=save_task()
            m.cpu.hook_del(read_hook);m.cpu.hook_del(code_hook)
            # Park a real native display setter before its brightness output.
            at=m.s('ipod55g_alarm_original_brightness');original=m.hooks[at]
            writer=[]
            def park_writer():writer.append(save_task());m.cpu.emu_stop()
            m.hooks[at]=park_writer
            start(0x164428,0x10001080,60)
            self.assertEqual(len(writer),1);m.hooks[at]=original
            resume(owner)
            m.set_brightness(90)  # Owner is done; newer native policy is27.
            signals_before_resume=len(m.signals)
            resume(writer[0])  # Older native output writes18 after that policy.
            self.assertGreater(len(m.signals),signals_before_resume)
            self.assertEqual(m.get('ipod55g_alarm_light_phase'),0)
            m.service_light()
            self.assertEqual(m.light_calls[-2:],[('power',1),('brightness',27)])

        def test_coalesced_end_before_light_task_runs_has_no_stale_flash(self):
            m=Machine();m.time(1);m.queue(180,0x80030d40)
            m.fn('ipod55g_alarm_worker');m.time(200001);m.fn('ipod55g_alarm_worker')
            self.assertEqual(m.get('ipod55g_alarm_light_phase'),0)
            m.service_light();self.assertEqual(m.light_calls,[])

        def test_suspend_before_light_delivery_does_not_force_on(self):
            m=Machine();m.set('ipod55g_alarm_light_phase',1)
            m.cpu.mem_write(0x1081e028,b'\x00');m.service_light()
            self.assertEqual(m.light_calls,[])

    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Alarm))
    receipt.update(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),passed=result.wasSuccessful(),
                   osos_sha256=hashlib.sha256(image).hexdigest(),symbols={k:hex(v) for k,v in symbols.items() if k.startswith('ipod55g_alarm') or k.startswith('ipod55g_clock')})
    (args.output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    if not result.wasSuccessful():raise SystemExit(1)


if __name__=='__main__':main()
