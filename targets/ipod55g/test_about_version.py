"""Native About Version text and independent packaged-resource corruption checks."""
from pathlib import Path
from tempfile import TemporaryDirectory
import struct
import unittest
import hashlib
import json
from io import BytesIO
from PIL import ImageFont

from actions import records
from build import build, compose
from image import load_stock, resources, word
import verify_package
from research_actions import Machine, Widgets, UI, OUT


def about_font():
    # Exact stock RSRC font, including its embedded 18px bitmap strike.
    font = load_stock(Path('iPod_25.1.3.ipsw')).firmware[0xace800:0xaed63c]
    assert hashlib.sha256(font).hexdigest() == 'e4759a70e7b07cac1840b6908de575e14fd23021d84881844b44458de01ccd18'
    return ImageFont.truetype(BytesIO(font), 18)


class AboutWidgets(Widgets):
    """Native factory/layout; disk fonts and glyph measurement are supplied.

    FreeType measures the exact stock 18px font. This is a text-fit check, not
    execution of Apple's glyph rasterizer or a physical display observation.
    """
    def __init__(self, image):
        super().__init__(image)
        self.font = about_font()
        for address in (0xa09b0, 0xa0bcc, 0xa0c00, 0x25aa40, 0x226000):
            self.hook(address, lambda: 0)
        self.call(0x25e844)
        self.hook(0x21f418, lambda: self.addresses.get(
            (0x53747220, self.reg(1)), self.addresses.get((0x53747220, self.reg(1)|0xffff0000), 0)))
        self.hook(0x21aad0, lambda: 0)  # No raster surface is used by this layout fixture.
        self.hook(0x2108d0, lambda: round(self.font.getlength(self.string(self.reg(1)))))

    def string(self, address):
        return bytes(self.cpu.mem_read(address, 256)).split(b'\0', 1)[0].decode('utf-8')

    def tree(self):
        groups = resources(self.image)
        at, size = groups['View'][0x5e15]
        # Construct the text content in the native container template, with its
        # About scroll viewport supplied by the enclosing screen boundary.
        root = bytearray(next(row for row in records(self.image[at:at+size]) if word(row, 12) == 0x5fd9))
        struct.pack_into('<I', root, 12, 0x5fdc)
        root = self.construct(bytes(root))
        self.write(root+0x58, 0, 0, 216, 320)
        children = [self.read(self.read(root+0x80)+4*i) for i in range(self.read(root+0x7c))]
        for child in children:
            self.call(0x24402c, child)  # Stock intrinsic text-size update.
            self.call(0x21a5e4, child, OUT)
            self.cpu.mem_write(child+0x58, bytes(self.cpu.mem_read(OUT, 16)))
        return root, children

EVIDENCE = Path('build/ipod55g-about-tests')


class AboutVersion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock = load_stock(Path('iPod_25.1.3.ipsw')).osos
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=EVIDENCE) as directory:
            cls.image, cls.symbols, _ = compose(load_stock(Path('iPod_25.1.3.ipsw')), Path(directory))

    def test_build_contains_revision_in_the_native_version_value(self):
        at, size = resources(self.image)['View'][0x5fdc]
        rows = records(self.image[at:at+size])
        value = rows[11]
        self.assertEqual((word(value, 12), word(value, 16), struct.unpack_from('<H', value, 84)[0]),
                         (0, 0xffffffff, 0x7141))
        at, size = resources(self.image)['Str '][0x7141]
        self.assertEqual(self.image[at:at+size], b'1.3 / r19\0')

    def test_independent_package_audit_accepts_exact_display_contract(self):
        self.assertTrue(hasattr(verify_package, 'audit_about_version'), 'missing independent About audit')
        result = verify_package.audit_about_version(self.stock, self.image, required=True)
        self.assertEqual(result, {'revision': 19, 'text': '1.3 / r19', 'native_view': '0x5fdc',
                                  'stock_version_getter_preserved': True})

    def test_native_factory_resolves_revision_with_stock_layout_and_font(self):
        machine = AboutWidgets(self.image)
        for _ in range(2):
            _, children = machine.tree()
            self.assertEqual(len(children), 21)
            value, label = children[11:13]
            self.assertEqual(machine.string(machine.read(value+0xc4)), '1.3 / r19')
            self.assertEqual(machine.string(machine.read(label+0xc4)), 'Version')
            self.assertEqual(struct.unpack('<4i', machine.cpu.mem_read(value+0x58, 16)), (144, 249, 168, 313))
            self.assertEqual(struct.unpack('<4i', machine.cpu.mem_read(label+0x58, 16)), (144, 6, 168, 248))
            self.assertEqual(machine.font.getlength('1.3 / r19'), 64)
            self.assertLess(6+machine.font.getlength('Version'), 249)
        self.assertEqual(machine.debug, [])

    def test_native_apple_version_getter_remains_unchanged(self):
        for image in (self.stock, self.image):
            machine = Machine(image)
            machine.write(0x1081da28, 1)  # Native string-lock initialization completed at boot.
            machine.hook(0x193090, lambda: 0)  # Settings service is unrelated to this property.
            self.assertEqual(machine.call(0x1e804c, UI, 0x53747220, 0x63f8, OUT), 1)
            self.assertEqual(bytes(machine.cpu.mem_read(machine.read(OUT), 4)), b'1.3\0')

    def test_installer_rejects_modified_view_getter_or_collision_without_mutation(self):
        from about_version import resource_updates
        from layout import extend_resources
        at, _ = resources(self.stock)['View'][0x5fdc]
        for offset in (at+4+11*96+12, at+4+12*96+84, 0x9ee94):
            image = bytearray(self.stock); image[offset] ^= 1
            before = bytes(image)
            with self.subTest(offset=hex(offset)), self.assertRaises(ValueError): resource_updates(image)
            self.assertEqual(image, before)
        image = bytearray(self.stock)
        extend_resources(image, {'Str ': {0x7141: b'occupied\0'}})
        before = bytes(image)
        with self.assertRaisesRegex(ValueError, 'collision'): resource_updates(image)
        self.assertEqual(image, before)

    def test_audit_rejects_changed_revision_binding_layout_and_native_getter(self):
        groups = resources(self.image)
        at, _ = groups['View'][0x5fdc]
        text, _ = groups['Str '][0x7141]
        for offset in (at+1072, at+1076, at+1144, at+1060+44, at+4+12*96+84, text+8, 0x9ee94):
            image = bytearray(self.image); image[offset] ^= 1
            with self.subTest(offset=hex(offset)), self.assertRaisesRegex(AssertionError, 'About'):
                verify_package.audit_about_version(self.stock, image, required=True)

    def test_legacy_images_are_optional_and_required_revision_rejects_missing_patch(self):
        self.assertIsNone(verify_package.audit_about_version(self.stock, self.stock))
        with self.assertRaisesRegex(AssertionError, 'missing About revision'):
            verify_package.audit_about_version(self.stock, self.stock, required=True)


class AboutManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        cls.directory = TemporaryDirectory(dir=EVIDENCE)
        cls.candidate = Path(cls.directory.name)/'candidate.ipsw'
        cls.manifest = build(Path('iPod_25.1.3.ipsw'), cls.candidate)

    @classmethod
    def tearDownClass(cls): cls.directory.cleanup()

    def test_manifest_revision_matches_the_exact_packaged_display(self):
        result = verify_package.verify(self.candidate, Path('iPod_25.1.3.ipsw'))
        self.assertEqual(self.manifest['enhanced_revision'], result['about_version']['revision'])
        self.assertEqual(self.manifest['about_version'], result['about_version']['text'])

    def test_release_suite_selects_about_tests_only_for_patched_packages(self):
        from verify_release import about_test_cases
        legacy = verify_package.verify(
            Path('build/ipod55g-oemplus-r18-attempt-01/iPod_25.1.3-oemplus-r18.ipsw'),
            Path('iPod_25.1.3.ipsw'))
        current = verify_package.verify(self.candidate, Path('iPod_25.1.3.ipsw'))
        self.assertEqual(about_test_cases(legacy), ())
        self.assertEqual([case.__name__ for case in about_test_cases(current)], ['AboutVersion'])

    def test_wrong_or_missing_manifest_revision_is_rejected(self):
        path = self.candidate.with_suffix('.artifacts')/'build.json'
        original = path.read_bytes()
        for key, value in (('enhanced_revision', 18), ('enhanced_revision', '19'),
                           ('enhanced_revision', None), ('about_version', '1.3 / r18'),
                           ('about_version', None)):
            try:
                manifest = dict(self.manifest)
                if value is None: del manifest[key]
                else: manifest[key] = value
                path.write_text(json.dumps(manifest))
                with self.subTest(key=key, value=value), self.assertRaisesRegex(AssertionError, 'About'):
                    verify_package.verify(self.candidate, Path('iPod_25.1.3.ipsw'))
            finally:
                path.write_bytes(original)


if __name__ == '__main__': unittest.main(verbosity=2)
