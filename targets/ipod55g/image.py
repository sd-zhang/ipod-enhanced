"""Pinned stock 5.5G 1.3 package and resource access. No device operations.

AUPD is opaque: preserve it byte-for-byte, including its stored checksum.
The OSOS directory length includes 2048-byte disk padding; OSOS +0xe8 is
the separate logical image endpoint. Callers must update startup endpoints
when extending executable contents. This module alone makes no boot claim.
"""
from dataclasses import dataclass
import hashlib
from io import BytesIO
from pathlib import Path
import struct
from zipfile import ZipFile

MEMBER = 'Firmware-25.6.3'
IPSW_SHA256 = '840b2480ad5b692c098cb772c3d2bf8f58bf37557788782b596ac01c27ecdc32'
FIRMWARE_SHA256 = 'c32abc5ffcd68df22df5c90f13c1df272456baa703648e8ce48c9e856ae0eeb9'
OSOS_SHA256 = '784ae3d5540fd2f89e8c947b93629e97f62a06dc311d9745aab143e4db6bb251'
ROOT = 0x331da0
SECTOR = 0x800
OSOS_START = 0x5000
OSOS_SIZE = 0x736000
DIRECTORY = (0x4200, 0x4228, 0x4250)


def word(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


@dataclass(frozen=True)
class StockImage:
    archive: bytes
    firmware: bytes
    manifest: bytes
    osos: bytes


@dataclass(frozen=True)
class Edit:
    offset: int
    expected: bytes
    replacement: bytes


def load_stock(path: Path) -> StockImage:
    archive = Path(path).read_bytes()
    if hashlib.sha256(archive).hexdigest() != IPSW_SHA256:
        raise ValueError('requires exact stock 5.5G iPod_25.1.3.ipsw')
    with ZipFile(BytesIO(archive)) as z:
        if z.namelist() != [MEMBER, 'manifest.plist'] or z.testzip() is not None:
            raise ValueError('unexpected stock 5.5G ZIP members or CRC')
        firmware, manifest = z.read(MEMBER), z.read('manifest.plist')
    if hashlib.sha256(firmware).hexdigest() != FIRMWARE_SHA256:
        raise ValueError('unexpected stock 5.5G firmware member')
    osos = firmware[OSOS_START:OSOS_START+OSOS_SIZE]
    if hashlib.sha256(osos).hexdigest() != OSOS_SHA256:
        raise ValueError('unexpected stock 5.5G OSOS')
    return StockImage(archive, firmware, manifest, osos)


def repack(stock: StockImage, osos: bytes) -> bytes:
    """Replace OSOS and relocate the complete original suffix as one unit."""
    if len(osos) < OSOS_SIZE or len(osos) % 4:
        raise ValueError('OSOS must retain stock length and be word-aligned')
    padded_size = (len(osos)+SECTOR-1) & -SECTOR
    shift = padded_size-OSOS_SIZE
    padded = osos + bytes(padded_size-len(osos))
    result = bytearray(stock.firmware[:OSOS_START])
    result.extend(padded)
    result.extend(stock.firmware[OSOS_START+OSOS_SIZE:])
    struct.pack_into('<I', result, 0x4210, padded_size)
    struct.pack_into('<I', result, 0x421c, sum(padded)&0xffffffff)
    for entry in DIRECTORY[1:]:
        struct.pack_into('<I', result, entry+12, word(stock.firmware, entry+12)+shift)
    return bytes(result)


def write_archive(stock: StockImage, firmware: bytes, destination: Path):
    """Preserve original ZIP member metadata and manifest; never overwrite."""
    with Path(destination).open('xb') as target:
        with ZipFile(BytesIO(stock.archive)) as original, ZipFile(target, 'w') as output:
            output.comment = original.comment
            for member in original.infolist():
                output.writestr(member, firmware if member.filename == MEMBER else original.read(member))


def apply_edits(data: bytes, edits: list[Edit]) -> bytes:
    result = bytearray(data)
    previous_end = -1
    for edit in sorted(edits, key=lambda e: e.offset):
        end = edit.offset+len(edit.expected)
        if edit.offset < previous_end:
            raise ValueError('overlapping edits')
        if edit.offset < 0 or end > len(data) or not edit.expected:
            raise ValueError('edit outside image')
        if len(edit.expected) != len(edit.replacement):
            raise ValueError('edits must preserve span length')
        if data[edit.offset:end] != edit.expected:
            raise ValueError(f'expected bytes differ at {edit.offset:#x}')
        result[edit.offset:end] = edit.replacement
        previous_end = end
    return bytes(result)


def resources(image: bytes) -> dict[str, dict[int, tuple[int, int]]]:
    """Read built-in records; the duplicate stock BMap key is not resolved here.

    Its value is omitted rather than assuming the native duplicate lookup rule.
    No other stock resource section has duplicate keys.
    """
    if word(image, ROOT) != 3 or word(image, ROOT+8) != 8:
        raise ValueError('unexpected 5.5G resource directory')
    base = ROOT+word(image, ROOT+4)
    result = {}
    for i in range(8):
        tag, count, _, relative = struct.unpack_from('<4sIII',image,ROOT+12+16*i)
        tag = tag[::-1].decode('ascii')
        at = ROOT+relative
        if at+count*12 > len(image):
            raise ValueError('resource directory outside image')
        rows, duplicates = {}, set()
        for key, offset, size in struct.iter_unpack('<III',image[at:at+count*12]):
            if base+offset+size > len(image):
                raise ValueError('resource payload outside image')
            if key in rows:
                if tag != 'BMap' or key != 0x5f28:
                    raise ValueError('unexpected duplicate resource key')
                duplicates.add(key)
            rows[key] = (base+offset,size)
        for key in duplicates:
            del rows[key]
        result[tag] = rows
    return result


def script_functions(image: bytes) -> dict[int, bytes]:
    """Return complete TCmd records, preserving header, bytecode and trailer."""
    at, size = resources(image)['TCmd'][0x2380]
    data = image[at:at+size]
    head = 8+4*struct.unpack_from('<H',data,6)[0]
    count, explicit = struct.unpack_from('<HH',data,head)
    directory = (head+4+2*explicit+3)&~3
    body = directory+4*(count+1)
    offsets = struct.unpack_from(f'<{count+1}I',data,directory)
    if offsets[0] != 0 or body+offsets[-1] > len(data):
        raise ValueError('invalid script body bounds')
    result = {}
    for start,end in zip(offsets,offsets[1:]):
        if end-start < 12:
            raise ValueError('invalid script record size')
        record = data[body+start:body+end]
        atom,_,_,_,length = struct.unpack_from('<5H',record)
        if atom in result or 10+length > len(record):
            raise ValueError('invalid script record')
        result[atom] = record
    return result
