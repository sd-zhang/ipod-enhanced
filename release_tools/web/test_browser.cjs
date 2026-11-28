/* Optional integration test: supply Playwright and the local stock IPSWs. */
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { createHash } = require('node:crypto');
const assert = require('node:assert/strict');
const playwright = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const [site, report] = process.argv.slice(2).map(item => path.resolve(item));
if (!site || !report) throw new Error('Usage: node test_browser.cjs SITE_HTML NEW_REPORT_DIRECTORY');
fs.mkdirSync(report);
const checks = [];
const errors = [];
const requests = [];
const engine = process.env.IPOD_WEB_BROWSER || 'chromium';
let browser;

(async () => {
  browser = await playwright[engine].launch({ headless: true,
    ...(engine === 'chromium' ? (process.env.IPOD_WEB_BROWSER_EXECUTABLE ?
      { executablePath: process.env.IPOD_WEB_BROWSER_EXECUTABLE } :
      { channel: process.env.IPOD_WEB_BROWSER_CHANNEL || 'msedge' }) : {}) });
  const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1000, height: 1080 } });
  const page = await context.newPage();
  // Playwright's WebKit screenshot helper injects an unapproved "body {}"
  // stylesheet, which correctly violates this page's CSP. Keep CSP enforced
  // and collect screenshots in Chromium; run all behavioral checks in WebKit.
  const screenshot = async name => {
    if (engine !== 'webkit') await page.screenshot({ path: path.join(report, name), fullPage: true });
  };
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await context.route(/^https?:/, async route => { requests.push(route.request().url()); await route.abort(); });
  await page.goto(pathToFileURL(site).href);
  assert.match(await page.title(), /iPod Enhanced/);
  assert.equal(await page.locator('#patch').isDisabled(), true);
  assert.equal(await page.locator('#status').textContent(), '');
  await screenshot('desktop.png');

  const file = page.getByLabel('Stock IPSW', { exact: true });
  const patchFile = page.getByLabel('Patch (.ipepatch)', { exact: true });
  const patch4g = path.join(root, 'build/release-prep-01/patches-01/ipod4g-r50.ipepatch');
  const patch55g = path.join(root, 'build/release-prep-01/patches-01/ipod55g-r19.ipepatch');
  const patch = page.getByRole('button', { name: 'Apply patch', exact: true });
  const result = page.locator('#result');
  const status = page.locator('#status');
  const waitStatus = async pattern => {
    await page.waitForFunction(source => new RegExp(source).test(document.getElementById('status').textContent), pattern,
      { timeout: 30000 });
  };

  await patchFile.setInputFiles(patch4g);
  assert.equal(await patch.isDisabled(), true);
  checks.push('both files required before patching');

  await file.setInputFiles({ name: 'not-stock.ipsw', mimeType: 'application/octet-stream', buffer: Buffer.alloc(100, 65) });
  await patch.click();
  await waitStatus('mismatch');
  assert.equal(await result.isVisible(), false);
  checks.push('modified/unknown input rejected without a download');

  await file.setInputFiles(path.join(root, 'iPod_4.3.1.1.ipsw'));
  await patchFile.setInputFiles(patch55g);
  await patch.click();
  await waitStatus('mismatch');
  assert.equal(await result.isVisible(), false);
  checks.push('wrong IPSW and patch pairing rejected');

  await patchFile.setInputFiles(patch4g);
  await patch.click();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  assert.match(await status.innerText(), /Cancelled/);
  await page.waitForTimeout(1200);
  assert.equal(await result.isVisible(), false);
  assert.match(await status.innerText(), /Cancelled/);
  checks.push('cancellation leaves no stale download');

  for (const [name, filename, patchPath, expected] of [
    ['4G r50', 'iPod_4.3.1.1.ipsw', patch4g, '6fc37ae7747831b543b57a2b08ea0bd9dc7a4431db003d308e7c3bf8a3f26a28'],
    ['5.5G r19', 'iPod_25.1.3.ipsw', patch55g, 'dabe75b6eccbb460b34f7e6080c3915eccae1f1879e2f2d66dddc61457fe334b'],
  ]) {
    await patchFile.setInputFiles(patchPath);
    assert.equal(await result.isVisible(), false);
    assert.equal(await page.locator('#download').getAttribute('href'), null);
    await file.setInputFiles(path.join(root, filename));
    assert.equal(await result.isVisible(), false);
    assert.equal(await page.locator('#download').getAttribute('href'), null);
    await patch.click();
    await waitStatus('^Done\\.$');
    await result.waitFor({ state: 'visible' });
    assert.equal(await page.locator('#output-hash').textContent(), expected);
    const pending = page.waitForEvent('download');
    await page.getByRole('link', { name: 'Download IPSW', exact: true }).click();
    const download = await pending;
    const output = path.join(report, download.suggestedFilename());
    await download.saveAs(output);
    assert.equal(createHash('sha256').update(fs.readFileSync(output)).digest('hex'), expected);
    checks.push(`${name} browser download matches frozen release SHA-256`);
  }
  await screenshot('complete.png');
  await page.setViewportSize({ width: 375, height: 812 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await screenshot('mobile.png');
  checks.push('375px viewport fits without horizontal scrolling');

  await page.evaluate(() => {
    dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
    dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
  });
  assert.equal(await result.isVisible(), false);
  assert.equal(await status.textContent(), '');
  checks.push('restoring page lifecycle resets stale success and download');

  await patchFile.setInputFiles({ name: 'broken.ipepatch', mimeType: 'application/zip', buffer: Buffer.alloc(50, 65) });
  assert.equal(await result.isVisible(), false);
  await patch.click();
  await waitStatus('ZIP directory');
  assert.equal(await result.isVisible(), false);
  checks.push('malformed uploaded patch rejected without a download');
  await patchFile.setInputFiles(patch55g);

  await file.setInputFiles({ name: 'too-large.ipsw', mimeType: 'application/octet-stream', buffer: Buffer.alloc(32 * 1024 * 1024 + 1) });
  await patch.click();
  assert.match(await status.innerText(), /Invalid IPSW file size/);
  assert.equal(await result.isVisible(), false);
  checks.push('oversized input refused and previous output invalidated');
  assert.deepEqual(requests, []);
  assert.deepEqual(errors, []);
  checks.push('no HTTP requests, console errors or page errors');
  // These deliberate attacks should produce CSP diagnostics. Keep them separate
  // from the error-free normal workflow above and still reject page exceptions.
  page.removeAllListeners('console');
  const security = await require('./browser_security.cjs')(page, checks, requests);
  assert.deepEqual(errors, []);
  const receipt = { status: 'pass', engine, browser: browser.version(), platform: process.platform,
    standalone_file_url: true, screenshots: engine !== 'webkit', checks, errors, requests, security };
  fs.writeFileSync(path.join(report, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n', { flag: 'wx' });
  console.log(JSON.stringify(receipt, null, 2));
})().catch(error => {
  const receipt = { status: 'fail', checks, error: error.stack, errors, requests };
  fs.writeFileSync(path.join(report, 'failure.json'), JSON.stringify(receipt, null, 2) + '\n', { flag: 'wx' });
  console.error(error.stack);
  process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
