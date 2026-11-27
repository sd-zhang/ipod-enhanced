"""Native ScriptVM equivalence, lookup cost and rejected unsafe patch profiles.

Allocator/OS boundaries are supplied; the resolver and script VM execute ARM.
The release verifier overrides image/symbols with the exact packaged candidate.
"""
import struct
import tempfile
import unittest
from pathlib import Path

from unicorn import UC_HOOK_CODE
from build import compose
from image import load_stock, resources, word
from research_actions import ScriptVM, OUT, APP


class ScriptGlobals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='ipod55g-script-globals-')
        cls.stock = load_stock(Path('iPod_25.1.3.ipsw')).osos
        cls.image, cls.symbols, _ = compose(load_stock(Path('iPod_25.1.3.ipsw')),
                                           Path(cls.directory.name))

    @classmethod
    def tearDownClass(cls): cls.directory.cleanup()

    def original(self):
        original = bytearray(self.image)
        struct.pack_into('<I', original, 0xd62f0, 0xe59f210c)
        return bytes(original)

    def test_global_lookup_avoids_extended_function_scan(self):
        counts = []
        for image in (self.original(), self.image):
            m = ScriptVM(image)
            m.write(m.globals+4*(0x1392-5010), 0xc0000002)
            instructions = []
            handle = m.cpu.hook_add(UC_HOOK_CODE,
                                   lambda cpu, pc, size, context: instructions.append(pc))
            self.assertEqual(m.call(0xd6220, 0x1392*4+2, OUT, OUT+4), 1)
            m.cpu.hook_del(handle)
            self.assertEqual((m.read(OUT), m.read(OUT+4)), (0xc0000002, 1))
            counts.append(len(instructions))
        # At least 60% of this hot lookup must disappear; no timing assertion.
        self.assertLess(counts[1], counts[0]*0.4, counts)

    def test_all_atom_ids_and_malformed_tags_match_native_resolution(self):
        old, new = ScriptVM(self.original()), ScriptVM(self.image)
        for atom in range(65536):
            value = atom*4+2
            before = old.call(0xd6220, value, OUT, OUT+4)
            after = new.call(0xd6220, value, OUT, OUT+4)
            self.assertEqual((before, bytes(old.cpu.mem_read(OUT, 8))),
                             (after, bytes(new.cpu.mem_read(OUT, 8))), atom)
        for value in (0, 1, 3, 4, 0xffffffff, 0xfffffffe, 0x80000002,
                      0x7ffffffe, 0xfffc0012):
            self.assertEqual(old.call(0xd6220, value, OUT, OUT+4),
                             new.call(0xd6220, value, OUT, OUT+4), hex(value))
            self.assertEqual(bytes(old.cpu.mem_read(OUT, 8)),
                             bytes(new.cpu.mem_read(OUT, 8)), hex(value))
        self.assertEqual(old.allocations, new.allocations)
        self.assertEqual(old.freed, new.freed)

    def test_actual_bound_and_unbound_globals_and_commands(self):
        old, new = ScriptVM(self.original()), ScriptVM(self.image)
        for m in (old, new):
            m.write(APP, 0x66fe10)
            m.bind(0x1392, 2, APP)
            for atom, value in ((5092, 0xc0000000), (0x1394, 37*4+1)):
                m.write(m.globals+4*(atom-5010), value)
        # Both interval boundaries, bound App, untouched/unbound native globals,
        # implicit commands and original/new explicit functions retain meaning.
        for atom in (5009, 5010, 5011, 0x1392, 0x1393, 0x1394, 5092, 5093,
                     152, 714, 3969, 4014, 5094, 5095, 5111, 65535):
            for m in (old, new): self.assertEqual(m.call(0xd6220, atom*4+2, OUT, OUT+4), 1)
            self.assertEqual(bytes(old.cpu.mem_read(OUT, 8)), bytes(new.cpu.mem_read(OUT, 8)), atom)
        self.assertEqual(new.run(4014), 0xc0000002)

    def test_patch_rejects_changed_native_instructions_and_literals(self):
        import script_globals
        for at in (0xd62f0, 0xd62f4, 0xd6348, 0xd634c, 0xd6350,
                   0xd6400, 0xd6404, 0xd6410):
            image = bytearray(self.original())
            image[at] ^= 1
            with self.subTest(address=hex(at)), self.assertRaises(ValueError):
                script_globals.apply(image, self.symbols)

    def test_patch_rejects_changed_function_global_profile(self):
        import script_globals
        image = self.original()
        at, size = resources(image)['TCmd'][0x2380]
        blob = image[at:at+size]
        head = 8+4*struct.unpack_from('<H', blob, 6)[0]
        count, explicit = struct.unpack_from('<HH', blob, head)
        directory = (head+4+2*explicit+3)&~3
        body = directory+4*(count+1)
        footer = body+word(blob, directory+4*count)
        changes = ((head, count-1), (head+2, explicit-1),
                   (head+4, 5010), (footer, 82), (footer+2, 1))
        for offset, value in changes:
            damaged = bytearray(image)
            struct.pack_into('<H', damaged, at+offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                script_globals.apply(damaged, self.symbols)
        damaged = bytearray(image)
        struct.pack_into('<H', damaged, 0x4fd746, 3960)
        with self.assertRaises(ValueError): script_globals.apply(damaged, self.symbols)

    def test_independent_package_contract_rejects_unsafe_resolver(self):
        from verify_package import audit_script_globals
        self.assertEqual(audit_script_globals(self.image, self.symbols)['implicit_globals'], 83)
        for at in (self.symbols['ipod55g_script_globals'],
                   self.symbols['ipod55g_script_globals']+28, 0xd62f0, 0xd62f4, 0xd6404):
            image = bytearray(self.image)
            image[at] ^= 1
            with self.subTest(address=hex(at)), self.assertRaises(AssertionError):
                audit_script_globals(image, self.symbols)
        image = bytearray(self.image)
        at, _ = resources(image)['TCmd'][0x2380]
        head = at+8+4*struct.unpack_from('<H', image, at+6)[0]
        struct.pack_into('<H', image, head+4, 5010)
        with self.assertRaisesRegex(AssertionError, 'collision'):
            audit_script_globals(image, self.symbols)


if __name__ == '__main__': unittest.main(verbosity=2)
