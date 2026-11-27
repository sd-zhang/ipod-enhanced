"""Exercise native wheel delivery under queue pressure, with actual ARM queues.

Packets, time, consumer scheduling and OS locks/wakeup are supplied. Native
packet decoding, movement coalescing, ring capacity/copy, event-pool ownership,
UI queue insertion/rejection and lazy wheel consumption execute unchanged.
This is not a complete scheduler/device emulator. Run with Unicorn JIT allowed.
"""
import argparse
import unittest
from pathlib import Path

from native_fixture import NativeMachine, load_image

WHEEL = 0x5765656c
KEY = 0x6b44776e
PENDING, PRODUCED, CONSUMED = 0x10419c60, 0x10419c58, 0x10419c5c
RECEIVER, BUFFER, EVENT = 0x10030000, 0x10031000, 0x10032000
RING = RECEIVER + 12
UI, PUBLISHER, NODE, MESSAGE = 0x10033000, 0x10034000, 0x10035000, 0x10036000
EVENT_POOL = 0x103c9f3c


class Input(NativeMachine):
    def __init__(self, image):
        super().__init__(image)
        self.cpu.mem_map(0x60005000, 0x1000)
        self.cpu.mem_map(0x7000c000, 0x1000)
        # Only kernel locks/notifications are supplied, never queue outcomes.
        for address in (0x9f5d8, 0x9fce4, 0x9fcdc, 0x87b88, 0x87d60):
            self.hook(address, lambda: 0)
        self.write(0x103c9f2c, RECEIVER)
        self.cpu.mem_write(0x1039da19, b'\x01')  # normal UI input enabled
        self.write(RING + 4, BUFFER, 0, 0, 0, 255)
        self.write(EVENT_POOL, MESSAGE, MESSAGE)
        self.make_ui_room()
        self.position, self.now = 0, 100000
        self.packet(0x8000001a)  # release initializes the motion threshold
        self.packet(0xc000001a)

    def packet(self, value):
        self.write(0x7000c104, 0x04000000)
        self.write(0x7000c140, value)
        self.call(0x149638)
        self.call(0x862dc)

    def move(self, distance=8):
        self.position = (self.position + distance) % 96
        self.now = (self.now + 25000) & 0xffffffff
        self.write(0x60005010, self.now)
        self.packet(0xc000001a | self.position << 16)

    def full_ring(self):
        self.write(RING + 12, 0, 248)  # seven bytes free, event needs 28

    def drain_ring(self):
        # Supply consumer progress, without pretending the lost event exists.
        self.write(RING + 12, self.read(RING + 16))

    def queued_bytes(self):
        return (self.read(RING + 16) - self.read(RING + 12)) & 255

    def raw_event(self):
        assert self.queued_bytes() >= 28
        at = self.read(RING + 12)
        data = bytes(self.cpu.mem_read(BUFFER + at, min(28, 256 - at)))
        if len(data) < 28:
            data += bytes(self.cpu.mem_read(BUFFER, 28 - len(data)))
        self.write(RING + 12, (at + 28) & 255)
        self.cpu.mem_write(EVENT, data)
        return EVENT

    def publish(self, event):
        return self.call(0x8f5dc, PUBLISHER, UI, event, 0, 0)

    def make_ui_room(self):
        self.write(PUBLISHER + 0x14, NODE, NODE)
        self.write(NODE, 0)

    def consume(self):
        node = self.call(0x9f850, UI + 0x40)
        assert node == NODE
        message = self.read(node + 12)
        assert message == MESSAGE
        assert self.read(message + 4) == WHEEL
        distance = self.call(0x8f35c, message)
        self.call(0xb1774, message)
        self.make_ui_room()
        return distance if distance < 0x80000000 else distance - 0x100000000


class WheelRecovery(unittest.TestCase):
    def machine(self):
        return Input(self.image)

    def test_raw_ring_rejection_does_not_disable_later_wheel_events(self):
        for distance in (8, -8):
            with self.subTest(direction=distance):
                m = self.machine()
                m.full_ring()
                for _ in range(20):
                    m.move(distance)
                m.drain_ring()
                m.move(distance)
                self.assertEqual(m.queued_bytes(), 28, 'lost event left wheel pending forever')
                self.assertEqual(m.publish(m.raw_event()), 1)
                self.assertEqual(m.consume(), distance, 'rejected backlog must not jump selection')
                self.assertEqual(m.read(PENDING), 0)

    def test_ui_queue_rejection_does_not_disable_later_wheel_events(self):
        m = self.machine()
        m.move()
        m.write(PUBLISHER + 0x14, 0, 0)  # no free UI queue nodes
        self.assertEqual(m.publish(m.raw_event()), 0)
        self.assertEqual(m.read(EVENT_POOL), MESSAGE, 'rejected event must return to pool')
        self.assertEqual(m.read(UI + 0x40), 0)
        m.make_ui_room()
        m.move()
        self.assertEqual(m.queued_bytes(), 28, 'UI rejection orphaned the pending wheel')
        self.assertEqual(m.publish(m.raw_event()), 1)
        self.assertEqual(m.consume(), 8)

    def test_empty_event_pool_keeps_existing_native_recovery(self):
        m = self.machine()
        m.move()
        m.write(EVENT_POOL, 0, 0)
        self.assertEqual(m.publish(m.raw_event()), 0)
        m.write(EVENT_POOL, MESSAGE, MESSAGE)
        m.move()
        self.assertEqual(m.queued_bytes(), 28)
        self.assertEqual(m.publish(m.raw_event()), 1)
        self.assertEqual(m.consume(), 8)

    def test_accepted_event_coalesces_motion_until_lazy_consumption(self):
        m = self.machine()
        m.move()
        m.move()
        self.assertEqual(m.queued_bytes(), 28)
        self.assertEqual(m.publish(m.raw_event()), 1)
        m.move()
        self.assertEqual(m.queued_bytes(), 0, 'accepted wheel notification duplicated')
        self.assertEqual(m.read(PENDING), 1)
        self.assertEqual(m.consume(), 24)
        m.move(-8)
        self.assertEqual(m.publish(m.raw_event()), 1)
        self.assertEqual(m.consume(), -8)

    def test_unrelated_rejected_event_does_not_clear_a_live_wheel(self):
        for kind in (KEY, 0x74696d65, 0x6d4d6f76):
            with self.subTest(kind=hex(kind)):
                m = self.machine()
                m.move()
                m.write(EVENT, kind, 0, 0, 0, 0, 0, 0)
                m.write(PUBLISHER + 0x14, 0, 0)
                self.assertEqual(m.publish(EVENT), 0)
                self.assertEqual(m.read(PENDING), 1)
                self.assertEqual(m.read(CONSUMED), 0)
                m.make_ui_room()
                self.assertEqual(m.publish(m.raw_event()), 1)
                self.assertEqual(m.consume(), 8)

    def test_buttons_still_enqueue_after_wheel_rejection(self):
        m = self.machine()
        m.full_ring()
        m.move()
        m.drain_ring()
        m.write(EVENT, KEY, 0, 0, 0, 0x00570000, 0, 0)
        self.assertEqual(m.call(0xabbcc, EVENT, 0), 1)
        event = m.raw_event()
        self.assertEqual(m.read(event), KEY)
        self.assertEqual(m.read(event + 16), 0x00570000)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    WheelRecovery.image = load_image(parser.parse_args().candidate)
    unittest.main(argv=[__file__], verbosity=2)
