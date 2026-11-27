from pathlib import Path
import struct
import unittest

from image import load_stock, script_functions, resources
from layout import extend_resources, finish
from scripts import merge_scripts, make_record

ROOT=Path(__file__).resolve().parents[2]


class ScriptMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.stock=load_stock(ROOT/'iPod_25.1.3.ipsw').osos

    def test_no_changes_preserve_exact_resource(self):
        at,size=resources(self.stock)['TCmd'][0x2380]
        self.assertEqual(merge_scripts(self.stock,{}),self.stock[at:at+size])

    def test_replace_and_append_preserve_all_other_functions_and_global_tail(self):
        original=script_functions(self.stock)
        replacement=make_record(4326,b'\x43\x05')
        added=make_record(5111,b'\x43\x05')
        changed=merge_scripts(self.stock,{4326:replacement,5111:added})
        data=bytearray(self.stock[:0x7358c0])
        extend_resources(data,{'TCmd':{0x2380:changed}})
        result=script_functions(finish(data))
        self.assertEqual(len(result),1065)
        self.assertEqual(result[4326],replacement)
        self.assertEqual(result[5111],added)
        for atom,record in original.items():
            if atom!=4326:self.assertEqual(result[atom],record)
        # The original footer follows the final record and contains globals.
        at,size=resources(self.stock)['TCmd'][0x2380]
        old=self.stock[at:at+size]
        # Stock's last physical record is explicit atom 714, not its max ID.
        # Header/directory inspection places the global tail at +0x15e1a.
        footer=old[0x15e1a:]
        self.assertEqual(len(footer),2036)
        self.assertTrue(changed.endswith(footer))
        self.assertEqual(struct.unpack_from('<H',changed)[0],5112)

    def test_record_alignment_and_reject_bad_identity(self):
        record=make_record(5111,b'\x05')
        self.assertEqual(len(record)%2,0)
        self.assertEqual(struct.unpack_from('<H',record,8)[0]%2,0)
        with self.assertRaises(ValueError):merge_scripts(self.stock,{5112:record})


if __name__=='__main__':unittest.main()
