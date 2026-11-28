/* iPod Enhanced browser archive adapter. Delta application is Rom Patcher JS. */
(function () {
  'use strict';
  const MAX_IMAGE = 32 * 1024 * 1024;
  const MAX_PATCH = 16 * 1024 * 1024;
  const MAX_PATCH_FILE = MAX_PATCH + 65536;
  const decoder = new TextDecoder('utf-8', { fatal: true });
  const fail = message => { throw new Error(message); };
  const view = bytes => new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const hash = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
    value => value.toString(16).padStart(2, '0')).join('');

  function keys(value, expected) {
    return value && typeof value === 'object' && !Array.isArray(value) &&
      Object.keys(value).sort().join(',') === expected.slice().sort().join(',');
  }

  function validateManifest(m) {
    if (!keys(m, ['format', 'model', 'revision', 'member', 'source_archive', 'source_firmware',
      'target_archive', 'target_firmware', 'patch']) || m.format !== 'ipod-enhanced-bsdiff-v1') {
      fail('Unsupported patch manifest format.');
    }
    for (const field of ['model', 'revision']) {
      if (typeof m[field] !== 'string' || !/^[A-Za-z0-9 ._-]{1,80}$/.test(m[field])) fail('Invalid manifest text.');
    }
    if (typeof m.member !== 'string' || !/^Firmware-[A-Za-z0-9._-]{1,80}$/.test(m.member)) fail('Invalid firmware member in manifest.');
    for (const field of ['source_archive', 'source_firmware', 'target_archive', 'target_firmware', 'patch']) {
      const item = m[field];
      if (!keys(item, ['size', 'sha256']) || !Number.isSafeInteger(item.size) || item.size <= 0 ||
          item.size > (field === 'patch' ? MAX_PATCH : MAX_IMAGE) ||
          typeof item.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(item.sha256)) fail('Invalid size or hash in release identity.');
    }
    return m;
  }

  async function check(bytes, expected, label) {
    if (bytes.length !== expected.size || await hash(bytes) !== expected.sha256) {
      fail(`${label} size or SHA-256 mismatch. No output was created.`);
    }
  }

  const crcTable = Uint32Array.from({ length: 256 }, (_, value) => {
    for (let bit = 0; bit < 8; bit++) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    return value >>> 0;
  });
  function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes) crc = crcTable[(crc ^ byte) & 255] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }

  function inflateBounded(input, size) {
    const chunks = []; let total = 0;
    const inflater = new pako.Inflate({ raw: true, chunkSize: 16384 });
    inflater.onData = chunk => {
      total += chunk.length;
      if (total > size) fail('ZIP decompressed member exceeds its declared size.');
      chunks.push(chunk);
    };
    inflater.push(input, true);
    if (inflater.err || !inflater.ended || inflater.strm.avail_in || total !== size) fail('Invalid ZIP decompression size or stream.');
    const result = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
    return result;
  }

  function readZip(bytes, limits) {
    const zip = view(bytes); let end = -1;
    for (let at = bytes.length - 22; at >= Math.max(0, bytes.length - 65557); at--) {
      if (zip.getUint32(at, true) === 0x06054b50 && at + 22 + zip.getUint16(at + 20, true) === bytes.length) { end = at; break; }
    }
    const names = Object.keys(limits);
    if (end < 0 || zip.getUint16(end + 4, true) || zip.getUint16(end + 6, true) ||
        zip.getUint16(end + 8, true) !== names.length || zip.getUint16(end + 10, true) !== names.length) fail('Unsupported ZIP directory.');
    const directoryStart = zip.getUint32(end + 16, true);
    if (directoryStart + zip.getUint32(end + 12, true) !== end) fail('Invalid ZIP directory size.');
    const entries = [], seen = new Set(); let at = directoryStart, localEnd = 0;
    // Parse all member headers before decompressing any untrusted patch data.
    for (let index = 0; index < names.length; index++) {
      if (at + 46 > end || zip.getUint32(at, true) !== 0x02014b50) fail('Invalid ZIP entry.');
      const nameSize = zip.getUint16(at + 28, true), extraSize = zip.getUint16(at + 30, true);
      const stop = at + 46 + nameSize + extraSize + zip.getUint16(at + 32, true);
      if (stop > end) fail('Invalid ZIP member header.');
      const name = decoder.decode(bytes.subarray(at + 46, at + 46 + nameSize));
      const flags = zip.getUint16(at + 8, true), method = zip.getUint16(at + 10, true);
      const packedSize = zip.getUint32(at + 20, true), size = zip.getUint32(at + 24, true);
      const crc = zip.getUint32(at + 16, true), local = zip.getUint32(at + 42, true);
      if (!Object.hasOwn(limits, name) || seen.has(name) || flags !== 0 || ![0, 8].includes(method) ||
          zip.getUint16(at + 34, true) || size > limits[name] || local !== localEnd ||
          local + 30 > directoryStart || zip.getUint32(local, true) !== 0x04034b50) fail('Unsupported ZIP member layout or size.');
      seen.add(name);
      const localNameSize = zip.getUint16(local + 26, true);
      const start = local + 30 + localNameSize + zip.getUint16(local + 28, true);
      localEnd = start + packedSize;
      if (localEnd > directoryStart || zip.getUint16(local + 6, true) !== flags ||
          zip.getUint16(local + 8, true) !== method || zip.getUint32(local + 14, true) !== crc ||
          zip.getUint32(local + 18, true) !== packedSize || zip.getUint32(local + 22, true) !== size ||
          decoder.decode(bytes.subarray(local + 30, local + 30 + localNameSize)) !== name) fail('Invalid ZIP local member header.');
      entries.push({ name, start, stop: localEnd, size, crc, method, central: bytes.slice(at, stop), nameSize, extraSize });
      at = stop;
    }
    if (at !== end || localEnd !== directoryStart) fail('Unexpected bytes in ZIP directory.');
    for (const entry of entries) {
      const compressed = bytes.subarray(entry.start, entry.stop);
      entry.data = entry.method === 8 ? inflateBounded(compressed, entry.size) : compressed;
      if (entry.data.length !== entry.size || crc32(entry.data) !== entry.crc) fail('ZIP member checksum mismatch.');
    }
    return { entries, footer: bytes.slice(end) };
  }

  function rebuildZip(archive) {
    const locals = [];
    const centrals = [];
    let offset = 0;
    for (const entry of archive.entries) {
      const compressed = entry.method === 0 ? entry.data : pako.deflateRaw(entry.data, { level: 6 });
      const central = entry.central.slice();
      const cv = view(central);
      const crc = crc32(entry.data);
      cv.setUint32(16, crc, true);
      cv.setUint32(20, compressed.length, true);
      cv.setUint32(24, entry.data.length, true);
      cv.setUint32(42, offset, true);
      const local = new Uint8Array(30 + entry.nameSize + entry.extraSize);
      const lv = view(local);
      lv.setUint32(0, 0x04034b50, true);
      local.set(central.subarray(6, 28), 4);
      lv.setUint16(26, entry.nameSize, true);
      lv.setUint16(28, entry.extraSize, true);
      local.set(central.subarray(46, 46 + entry.nameSize + entry.extraSize), 30);
      locals.push(local, compressed);
      centrals.push(central);
      offset += local.length + compressed.length;
    }
    const directorySize = centrals.reduce((total, data) => total + data.length, 0);
    const footer = archive.footer.slice();
    view(footer).setUint32(12, directorySize, true);
    view(footer).setUint32(16, offset, true);
    const size = offset + directorySize + footer.length;
    if (size > MAX_IMAGE) fail('Rebuilt IPSW is too large.');
    const result = new Uint8Array(size);
    offset = 0;
    for (const data of [...locals, ...centrals, footer]) { result.set(data, offset); offset += data.length; }
    return result;
  }

  async function apply(stock, patchFile, progress = () => {}) {
    if (!(stock instanceof Uint8Array) || stock.length < 22 || stock.length > MAX_IMAGE) fail('Unsupported IPSW file size.');
    if (!(patchFile instanceof Uint8Array) || patchFile.length < 22 || patchFile.length > MAX_PATCH_FILE) fail('Unsupported .ipepatch file size.');
    progress({ phase: 'check', message: 'Checking patch…' });
    const bundle = readZip(patchFile, { 'manifest.json': 16384, 'firmware.bsdiff': MAX_PATCH });
    const m = validateManifest(JSON.parse(decoder.decode(bundle.entries.find(item => item.name === 'manifest.json').data)));
    const delta = bundle.entries.find(item => item.name === 'firmware.bsdiff').data;
    await check(delta, m.patch, 'Patch');
    progress({ phase: 'check', message: 'Checking stock IPSW…' });
    await check(stock, m.source_archive, 'Original Apple IPSW (model/version must match)');
    const archive = readZip(stock, { [m.member]: MAX_IMAGE, 'manifest.plist': 16384 });
    const member = archive.entries.find(item => item.name === m.member);
    await check(member.data, m.source_firmware, 'Stock firmware member');
    progress({ phase: 'patch', message: `Applying ${m.model} ${m.revision}…` });
    const patch = BDF.fromFile(new BinFile(delta));
    if (patch.patchedSize !== m.target_firmware.size) fail('Patch output size mismatch.');
    const firmware = patch.apply(new BinFile(member.data))._u8array;
    await check(firmware, m.target_firmware, 'Patched firmware');
    progress({ phase: 'rebuild', message: 'Rebuilding IPSW…' });
    member.data = firmware;
    const bytes = rebuildZip(archive);
    progress({ phase: 'verify', message: 'Verifying output…' });
    await check(bytes, m.target_archive, 'Rebuilt output release');
    const output_name = `${m.model.replace(/ +/g, '-')}-${m.revision.replace(/ +/g, '-')}.ipsw`;
    return { bytes, release: { model: m.model, revision: m.revision, output_name,
      source_sha256: m.source_archive.sha256 }, sha256: m.target_archive.sha256 };
  }

  globalThis.IpodPatcher = Object.freeze({ apply, MAX_IMAGE, MAX_PATCH_FILE });
}());
