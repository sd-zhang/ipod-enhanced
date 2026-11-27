const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const zlib = require('node:zlib');
const { webcrypto } = require('node:crypto');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'test-fixture.json')));
const files = ['vendor/pako.min.js', 'vendor/BinFile.js', 'vendor/RomPatcher.format.bdf.js', 'core.js'];
function runtime() {
  const context = vm.createContext({ Uint8Array, Uint32Array, ArrayBuffer, DataView, TextDecoder, crypto: webcrypto, atob, console });
  for (const file of files) vm.runInContext(fs.readFileSync(path.join(__dirname, file), 'utf8'), context, { filename: file });
  return context;
}
const bytes = data => new Uint8Array(Buffer.from(data, 'base64'));
const stock = () => bytes(fixture.stock);
const manifest = () => structuredClone(fixture.catalog.releases[0].manifest);
function crc(data) {
  let value = -1;
  for (const byte of data) {
    value ^= byte;
    for (let i = 0; i < 8; i++) value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0);
  }
  return (value ^ -1) >>> 0;
}
// Independent ZIP fixture writer, checked against the Python-generated fixture.
function zip(entries) {
  const local = [], central = []; let offset = 0;
  for (const entry of entries) {
    const name = Buffer.from(entry.name), data = Buffer.from(entry.data);
    const method = entry.method ?? 8, compressed = method === 0 ? data : zlib.deflateRawSync(data);
    const header = Buffer.alloc(30), directory = Buffer.alloc(46);
    header.writeUInt32LE(0x04034b50); header.writeUInt16LE(20, 4);
    header.writeUInt16LE(method, 8); header.writeUInt32LE(crc(data), 14);
    header.writeUInt32LE(compressed.length, 18); header.writeUInt32LE(entry.size ?? data.length, 22);
    header.writeUInt16LE(name.length, 26);
    directory.writeUInt32LE(0x02014b50); directory.writeUInt16LE(20, 4);
    header.copy(directory, 6, 4, 26); directory.writeUInt16LE(name.length, 28); directory.writeUInt32LE(offset, 42);
    local.push(header, name, compressed); central.push(directory, name);
    offset += header.length + name.length + compressed.length;
  }
  const cd = Buffer.concat(central), end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50); end.writeUInt16LE(entries.length, 8); end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(cd.length, 12); end.writeUInt32LE(offset, 16);
  return new Uint8Array(Buffer.concat([...local, cd, end]));
}
function bundle(m = manifest(), delta = bytes(fixture.catalog.releases[0].delta), extras = []) {
  return zip([{ name: 'manifest.json', data: JSON.stringify(m) }, { name: 'firmware.bsdiff', data: delta }, ...extras]);
}
const apply = (...args) => runtime().IpodPatcher.apply(...args);

test('binary reader respects a bounded typed-array view', () => {
  const { BinFile } = runtime(); const reader = new BinFile(new Uint8Array([9, 65, 66, 9]).subarray(1, 3));
  assert.deepEqual(Array.from(reader.readBytes(2)), [65, 66]); assert.equal(reader.isEOF(), true);
});
test('uploaded Python ZIP reconstructs the exact IPSW with extra fields and comments', async () => {
  const result = await apply(stock(), bytes(fixture.ipepatch));
  assert.deepEqual(Buffer.from(result.bytes), Buffer.from(fixture.target, 'base64'));
  assert.equal(result.sha256, '4308e346bc2cd89c946c8b84fe2ca6fb6f0e22d9f4305f0d109efcf082669735');
});
test('future revision works without an embedded release catalog', async () => {
  const m = manifest(); m.revision = 'r999';
  const result = await apply(stock(), bundle(m));
  assert.equal(result.release.revision, 'r999'); assert.match(result.release.output_name, /r999.*\.ipsw$/);
  assert.deepEqual(Buffer.from(result.bytes), Buffer.from(fixture.target, 'base64'));
});
test('wrong stock is rejected before firmware decompression', async () => {
  const input = stock(); input[40] ^= 1; const progress = [];
  await assert.rejects(apply(input, bundle(), event => progress.push(event)), /original|Stock|match/i);
  assert.ok(!progress.some(event => event.phase === 'patch'));
});
test('modified patch and output identities prevent downloads', async () => {
  const delta = bytes(fixture.catalog.releases[0].delta); delta[40] ^= 1;
  await assert.rejects(apply(stock(), bundle(manifest(), delta)), /Patch.*(SHA|match)/i);
  const m = manifest(); m.target_archive.sha256 = '0'.repeat(64);
  await assert.rejects(apply(stock(), bundle(m)), /output.*(SHA|match)/i);
});
test('unknown format and invalid metadata are rejected', async () => {
  for (const change of [m => m.format = 'unknown-v2', m => m.model = '<script>', m => m.target_firmware.size = 2 ** 40]) {
    const m = manifest(); change(m);
    await assert.rejects(apply(stock(), bundle(m)), /format|manifest|size|identity/i);
  }
});
test('extra, duplicate, missing and traversal ZIP entries are rejected', async () => {
  for (const entries of [
    [{ name: 'private.py', data: 'do not execute' }], [{ name: 'manifest.json', data: '{}' }],
    [{ name: '../manifest.json', data: '{}' }],
  ]) await assert.rejects(apply(stock(), bundle(manifest(), undefined, entries)), /directory|entry|member/i);
  await assert.rejects(apply(stock(), zip([{ name: 'manifest.json', data: '{}' }])), /directory|entry|member/i);
  await assert.rejects(apply(stock(), zip([{ name: 'manifest.json', data: '{}' }, { name: 'manifest.json', data: '{}' }])), /entry|member/i);
});
test('truncated archives and unsupported ZIP methods fail cleanly', async () => {
  for (const cut of [0, 10, 21, 40]) await assert.rejects(apply(stock(), bundle().subarray(0, cut)), /file|size|directory|entry/i);
  const input = bundle(); input[8] = 99;
  await assert.rejects(apply(stock(), input), /header|layout|method/i);
});
test('stored entries and reversed file order work', async () => {
  const input = zip([{ name: 'firmware.bsdiff', data: bytes(fixture.catalog.releases[0].delta), method: 0 },
    { name: 'manifest.json', data: JSON.stringify(manifest()), method: 0 }]);
  assert.deepEqual(Buffer.from((await apply(stock(), input)).bytes), Buffer.from(fixture.target, 'base64'));
});
test('patch ZIP decompression stops at the declared member size', async () => {
  const input = zip([{ name: 'manifest.json', data: Buffer.alloc(1000000, 32), size: 100 },
    { name: 'firmware.bsdiff', data: bytes(fixture.catalog.releases[0].delta) }]);
  await assert.rejects(apply(stock(), input), /decompress|size|large/i);
});
test('oversized inputs are rejected', async () => {
  await assert.rejects(apply(new Uint8Array(32 * 1024 * 1024 + 1), bundle()), /size|large/i);
  await assert.rejects(apply(stock(), new Uint8Array(17 * 1024 * 1024 + 1)), /size|large/i);
});
for (const [name, want] of [['negative_seek', [65, 66, 65, 66]], ['outside_source', [5, 66, 68]], ['large_signed_seek', [7]]]) {
  test(`BSDIFF ${name} uses signed offsets and zero outside source`, () => {
    const { BDF, BinFile } = runtime(); const patch = BDF.fromFile(new BinFile(bytes(fixture.bdf_cases[name])));
    assert.deepEqual(Array.from(patch.apply(new BinFile(new Uint8Array([65, 66])))._u8array), want);
  });
}
for (const name of ['excess_diff', 'truncated_controls', 'diff_bomb']) {
  test(`BSDIFF rejects ${name} before allocating an output`, () => {
    const { BDF, BinFile } = runtime();
    assert.throws(() => BDF.fromFile(new BinFile(bytes(fixture.bdf_cases[name]))), /size|control|limit|excess|bound/i);
  });
}
test('BSDIFF keeps symbol 255 in every valid bzip alphabet', () => {
  for (const [name, expected] of [
    ['all_ff_extra', Array(10000).fill(255)],
    ['descending_extra', Array.from({ length: 256 }, (_, index) => 255 - index)],
  ]) {
    const { BDF, BinFile } = runtime();
    const patch = BDF.fromFile(new BinFile(bytes(fixture.bdf_cases[name])));
    assert.deepEqual(Array.from(patch.apply(new BinFile(new Uint8Array([65, 66])))._u8array), expected);
  }
});
test('BSDIFF rejects unsafe intermediate source movement before a compensating skip', () => {
  const { BDF, BinFile } = runtime();
  assert.throws(() => BDF.fromFile(new BinFile(bytes(fixture.bdf_cases.intermediate_overflow))), /source offset.*bounds/i);
});
test('BSDIFF validates a complete multi-block bzip stream', () => {
  const { BDF, BinFile } = runtime();
  const patch = BDF.fromFile(new BinFile(bytes(fixture.bzip_security.valid_multiblock)));
  const output = patch.apply(new BinFile(new Uint8Array([0])))._u8array;
  assert.deepEqual(Buffer.from(output), Buffer.from(Array.from({ length: 256000 }, (_, i) => i & 255)));
});
for (const name of ['wrong_combined_crc', 'nonzero_padding', 'trailing_bytes']) {
  test(`BSDIFF rejects ${name} even when individual block checksums match`, () => {
    const { BDF, BinFile } = runtime();
    assert.throws(() => BDF.fromFile(new BinFile(bytes(fixture.bzip_security[name]))), /CRC|padding|trailing|stream/i);
  });
}
for (const [name, error] of [['initial_zero', /Huffman.*length/i],
  ['adjusted_zero', /Huffman.*length/i], ['adjusted_21', /Huffman.*length/i],
  ['missing_code', /Huffman.*code/i]]) {
  test(`BSDIFF refuses ${name} at the Huffman decoding boundary`, () => {
    const { BDF, BinFile } = runtime();
    assert.throws(() => BDF.fromFile(new BinFile(bytes(fixture.huffman_security[name]))), error);
  });
}
if (process.env.IPOD_WEB_REAL === '1') {
  for (const [id, name, expected] of [
    ['ipod4g-r50', 'iPod_4.3.1.1.ipsw', '6fc37ae7747831b543b57a2b08ea0bd9dc7a4431db003d308e7c3bf8a3f26a28'],
    ['ipod55g-r19', 'iPod_25.1.3.ipsw', 'dabe75b6eccbb460b34f7e6080c3915eccae1f1879e2f2d66dddc61457fe334b'],
  ]) test(`uploaded ${id} produces exact frozen IPSW`, async () => {
    const root = path.resolve(__dirname, '../..');
    const result = await apply(new Uint8Array(fs.readFileSync(path.join(root, name))),
      new Uint8Array(fs.readFileSync(path.join(root, `build/release-prep-01/patches-01/${id}.ipepatch`))));
    assert.equal(result.sha256, expected);
  });
}
