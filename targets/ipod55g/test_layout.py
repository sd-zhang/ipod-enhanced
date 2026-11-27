"""Build/layout proof using a tiny ARM fixture, not a firmware feature."""
from pathlib import Path
import struct
import tempfile
import unittest

from image import load_stock, resources
import layout

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = '''
volatile unsigned counter;
unsigned next_value(void) { return ++counter; }
'''


class ExtensionTests(unittest.TestCase):
    def test_linked_state_is_loaded_zero_and_reserved_before_native_data(self):
        stock = load_stock(ROOT/'iPod_25.1.3.ipsw')
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source = directory/'fixture.c'; source.write_text(FIXTURE)
            elf = layout.link([source],directory)
            working,symbols = layout.install_elf(stock.osos,elf)
            self.assertGreaterEqual(symbols['next_value'],0x7358c0)
            self.assertGreaterEqual(symbols['counter'],0x107358c0)
            self.assertLess(symbols['counter'],0x10800000)
            offset=symbols['counter']-0x10000000
            self.assertEqual(working[offset:offset+4],bytes(4))
            result=layout.finish(working)
            end=struct.unpack_from('<I',result,0xe8)[0]
            self.assertEqual(result[end-4:end],bytes.fromhex('55aa55aa'))
            self.assertEqual(struct.unpack_from('<I',result,0xdec)[0],0x04000000+end-4)
            self.assertEqual(struct.unpack_from('<I',result,0x308)[0],0x04000000+end-4)
            self.assertEqual(len(result)%0x800,0)

    def test_resource_extension_preserves_original_payloads(self):
        stock=load_stock(ROOT/'iPod_25.1.3.ipsw')
        working=bytearray(stock.osos[:0x7358c0])
        original=resources(stock.osos)
        original_clock=original['Menu'][0x6181]
        layout.extend_resources(working,{'Str ':{0x7100:b'Pomodoro\0'}})
        result=layout.finish(working)
        updated=resources(result)
        at,size=updated['Str '][0x7100]
        self.assertEqual(result[at:at+size],b'Pomodoro\0')
        self.assertEqual(updated['Menu'],original['Menu'])
        at,size=original_clock
        self.assertEqual(result[at:at+size],stock.osos[at:at+size])
        for key,record in original['Str '].items():
            self.assertEqual(updated['Str '][key],record)

    def test_refuses_unproved_memory_range(self):
        with self.assertRaisesRegex(ValueError,'native data'):
            layout.finish(bytearray(0x800000))


if __name__=='__main__':unittest.main()
