"""Execute the 5.5G wheel click through its stock PCM and piezo boundaries.

Unicorn needs JIT permission. These checks exercise native mixing into supplied
PCM, not the device scheduler, DMA, headphone jack or audible physical output.
Settings-to-flags conversion belongs to the separate clicker settings suite.
"""
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest

from unicorn import UC_HOOK_CODE

from image import load_stock
from research_actions import Machine


ROOT = Path(__file__).resolve().parents[2]
MIXER, SOUND = 0x1088342c, 0x10882440
OWNER, PLAYER, VIDEO = 0x10012000, 0x10013000, 0x10014000
FILTER, STREAM, PCM = 0x10015000, 0x10016000, 0x10018000


class ClickerAudioMachine(Machine):
    def __init__(self, image, video_active=False):
        super().__init__(image)
        # Supply the active output rate and allocation; native mixer and all
        # four voice constructors run. Local static registration is pre-done.
        self.cpu.mem_map(0x14938000, 0x1000)
        self.write(0x14938960, 44100)
        self.hook(0x25a974, self.allocate)
        self.write(0x1081e0dc, 1)
        assert self.call(0x217e04, MIXER) == 1
        self.call(0x1f0168, SOUND)
        self.write(0x1081e034, 1)
        self.write(0x1081d9fc, 1)
        self.cpu.mem_write(0x1081d9f8, b'\x01')

        # The sound service is already awake. Supply its timer refresh and
        # unused stack-message lifetime; cue setup and mixer activation run.
        self.hook(0xc15cc, lambda: 0)
        self.hook(0x1449b8, lambda: self.reg(0))
        self.hook(0x216078, lambda: 0)

        # Supply only owner lookup and object state. Both native virtual calls
        # execute: 246158 -> 1f700c -> 1f91b0 reads video-control byte +28.
        self.hook(0x24672c, lambda: OWNER)
        self.write(OWNER + 0x24, PLAYER)
        self.write(PLAYER, 0x6717f4)
        self.write(PLAYER + 0x34, VIDEO)
        self.write(VIDEO, 0x671ddc)
        self.cpu.mem_write(VIDEO + 0x28, bytes([video_active]))
        self.write(FILTER + 0xc, STREAM)

        self.piezo_requests = []
        self.voice_starts = []
        self.video_queries = []
        # Stop at the stock piezo OS request boundary; no buzzer/MMIO emulation.
        self.hook(0xcd430, self.piezo)
        self.cpu.hook_add(UC_HOOK_CODE, self.observe)

    def piezo(self):
        self.piezo_requests.append((self.reg(0), self.reg(1)))
        return 0

    def observe(self, cpu, pc, size, context):
        if pc == 0x146258:
            self.voice_starts.append(self.reg(0))
        elif pc == 0x1f91b0:
            self.video_queries.append(self.reg(0))

    def click(self, flags):
        self.write(0x1081d9c4, flags)
        # Public wheel-click wrapper and its dispatcher both execute natively.
        self.call(0x1c2428)

    def mix(self, samples, bypass=False):
        self.cpu.mem_write(STREAM + 0x25, bytes([bypass]))
        self.cpu.mem_write(PCM, struct.pack(f'<{len(samples)}h', *samples))
        # Stock audio-filter callback, including its native bypass gate.
        self.call(0x1d603c, FILTER, PCM, len(samples) * 2)
        return list(struct.unpack(f'<{len(samples)}h',
                                  self.cpu.mem_read(PCM, len(samples) * 2)))


class ClickerAudioNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from build import compose
        stock = load_stock(ROOT / 'iPod_25.1.3.ipsw')
        with TemporaryDirectory() as directory:
            cls.image, _, _ = compose(stock, Path(directory))

    def test_four_flags_reach_the_matching_output_boundaries(self):
        music = [1000, -1000] * 1024
        for flags in range(4):
            with self.subTest(flags=flags):
                m = ClickerAudioMachine(self.image)
                m.click(flags)
                self.assertEqual(m.piezo_requests,
                                 [(85, 3000)] if flags & 1 else [])
                self.assertEqual(len(m.voice_starts), int(bool(flags & 2)))
                self.assertEqual(m.video_queries, [VIDEO] if flags == 3 else [])
                self.assertEqual(m.mix(music) != music, bool(flags & 2))

    def test_pcm_click_is_added_to_existing_music_samples(self):
        silent = ClickerAudioMachine(self.image)
        silent.click(2)
        cue = silent.mix([0] * 2048)
        self.assertTrue(any(cue))
        # Native table lookup replaces the sound constructor's default rate:
        # 166 mono signed16 frames at11025Hz; +1c=519 is gain, not a sound ID.
        self.assertEqual(tuple(silent.read(SOUND + offset) for offset in
                               (4, 8, 0x10, 0x14, 0x18, 0x1c)),
                         (0x4fc2cc, 332, 11025, 1, 16, 519))
        music = [1000, -1000] * 1024
        active = ClickerAudioMachine(self.image)
        active.click(2)
        self.assertEqual(active.mix(music),
                         [sample + click for sample, click in zip(music, cue)])

    def test_both_keeps_stock_video_guard(self):
        music = [1000, -1000] * 1024
        for flags in range(4):
            with self.subTest(flags=flags):
                m = ClickerAudioMachine(self.image, video_active=True)
                m.click(flags)
                self.assertEqual(m.piezo_requests,
                                 [(85, 3000)] if flags & 1 else [])
                self.assertEqual(m.video_queries, [VIDEO] if flags == 3 else [])
                self.assertEqual(len(m.voice_starts), int(flags == 2))
                self.assertEqual(m.mix(music) != music, flags == 2)

    def test_native_filter_bypass_preserves_pcm_with_a_queued_click(self):
        m = ClickerAudioMachine(self.image)
        m.click(2)
        self.assertEqual(len(m.voice_starts), 1)
        music = [1000, -1000] * 1024
        self.assertEqual(m.mix(music, bypass=True), music)
        self.assertNotEqual(m.mix(music), music)


if __name__ == '__main__':
    unittest.main(verbosity=2)
