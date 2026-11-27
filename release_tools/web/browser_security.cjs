/* Attack probes for the built page, with its real CSP and Blob workers. */
const assert = require('node:assert/strict');
const fixture = require('./test-fixture.json');

module.exports = async function checkSecurity(page, checks, requests) {
  await page.getByLabel('Original Apple firmware', { exact: true }).setInputFiles({
    name: 'fixture.ipsw', mimeType: 'application/zip', buffer: Buffer.from(fixture.stock, 'base64'),
  });
  for (const [name, data] of Object.entries(fixture.hostile_patches)) {
    await page.getByLabel('Enhanced patch', { exact: true }).setInputFiles({
      name: `${name}.ipepatch`, mimeType: 'application/zip', buffer: Buffer.from(data, 'base64'),
    });
    await page.getByRole('button', { name: 'Create patched IPSW', exact: true }).click();
    await page.waitForFunction(() => document.getElementById('status').classList.contains('error'));
    assert.equal(await page.locator('#result').isVisible(), false);
    assert.equal(await page.locator('#download').getAttribute('href'), null);
    assert.equal(await page.evaluate(() => globalThis.patchInjected), undefined);
    checks.push(`${name} patch rejected without execution or download`);
  }

  const result = await page.evaluate(async () => {
    const violations = [];
    document.addEventListener('securitypolicyviolation', event => violations.push(event.effectiveDirective));
    const script = document.createElement('script');
    script.textContent = 'globalThis.inlineInjected = true';
    document.body.append(script);
    const button = document.createElement('button');
    button.setAttribute('onclick', 'globalThis.handlerInjected = true');
    document.body.append(button); button.click();
    const network = await fetch('https://example.invalid/patcher-security-probe').then(() => false, () => true);

    // A real Blob worker must inherit the page policy. DevTools evaluation can
    // bypass eval restrictions, so perform dynamic-code probes inside the worker.
    const workerCode = `onmessage = async event => {
      const blocked = {};
      for (const [name, run] of [
        ['eval', () => eval('1 + 1')],
        ['function', () => new Function('return 2')()],
        ['importScripts', () => importScripts(event.data)],
      ]) {
        try { run(); blocked[name] = false; } catch (error) { blocked[name] = true; }
      }
      blocked.network = await fetch('https://example.invalid/patcher-worker-probe').then(() => false, () => true);
      blocked.importDidNotRun = !self.importInjected;
      postMessage(blocked);
    };`;
    const workerURL = URL.createObjectURL(new Blob([workerCode], { type: 'text/javascript' }));
    const importURL = URL.createObjectURL(new Blob(['self.importInjected = true'], { type: 'text/javascript' }));
    const worker = new Worker(workerURL);
    let timer;
    const workerResult = await new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(Error('Security probe worker did not respond')), 10000);
      worker.onmessage = event => resolve(event.data);
      worker.onerror = event => reject(Error(event.message));
      worker.postMessage(importURL);
    }).finally(() => {
      clearTimeout(timer); worker.terminate();
      URL.revokeObjectURL(workerURL); URL.revokeObjectURL(importURL);
    });

    const frame = document.createElement('iframe');
    const frameURL = URL.createObjectURL(new Blob(['<p>unexpected-frame-content</p>'], { type: 'text/html' }));
    await new Promise(resolve => {
      const timeout = setTimeout(resolve, 1000);
      frame.onload = () => { clearTimeout(timeout); resolve(); };
      frame.src = frameURL; document.body.append(frame);
    });
    // CSP violation delivery is queued separately from the frame load event.
    await new Promise(resolve => setTimeout(resolve, 50));
    frame.remove(); URL.revokeObjectURL(frameURL); script.remove(); button.remove();
    return { network, worker: workerResult, inlineExecuted: Boolean(globalThis.inlineInjected),
      handlerExecuted: Boolean(globalThis.handlerInjected), violations };
  });
  assert.equal(result.inlineExecuted, false, 'unapproved inline script executed');
  assert.equal(result.handlerExecuted, false, 'inline event handler executed');
  assert.equal(result.network, true, 'page network access was allowed');
  assert.deepEqual(result.worker, { eval: true, function: true, importScripts: true, network: true, importDidNotRun: true });
  assert.ok(result.violations.includes('frame-src'), 'embedded Blob frame was allowed by the page policy');
  assert.deepEqual(requests, [], 'a forbidden HTTP request escaped CSP');
  checks.push('CSP blocks inline scripts, event handlers and embedded Blob frames');
  checks.push('Blob worker inherits bans on eval, Function, script imports and network requests');
  checks.push('page and worker network probes blocked before any HTTP request');
  return result;
};
