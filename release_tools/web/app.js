(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const workerCode = JSON.parse($('worker-code').textContent);
  const fileInput = $('firmware');
  const patchInput = $('patch-file');
  const submit = $('patch');
  const cancel = $('cancel');
  const status = $('status');
  const result = $('result');
  const download = $('download');
  const maxSize = 32 * 1024 * 1024;
  const maxPatchSize = 16 * 1024 * 1024 + 65536;
  let active = null;
  let downloadURL = null;
  let generation = 0;
  let supported = Boolean(globalThis.Worker && globalThis.Blob && globalThis.crypto?.subtle &&
    globalThis.File?.prototype.arrayBuffer && URL.createObjectURL);

  function message(text, error = false) {
    status.textContent = text;
    status.classList.toggle('error', error);
  }
  function clearResult() {
    if (downloadURL) URL.revokeObjectURL(downloadURL);
    downloadURL = null;
    download.removeAttribute('href');
    download.removeAttribute('download');
    result.hidden = true;
  }
  function busy(value) {
    fileInput.disabled = value;
    patchInput.disabled = value;
    submit.disabled = value || !supported || !fileInput.files.length || !patchInput.files.length;
    cancel.hidden = !value;
    $('progress').hidden = !value;
    $('form').setAttribute('aria-busy', String(value));
  }
  function stop() {
    generation++;
    if (active) {
      clearTimeout(active.timeout);
      active.worker.terminate();
      URL.revokeObjectURL(active.url);
      active = null;
    }
    busy(false);
  }
  function changed() {
    stop();
    clearResult();
    message('');
  }
  fileInput.addEventListener('change', changed);
  patchInput.addEventListener('change', changed);
  cancel.addEventListener('click', () => {
    stop(); clearResult(); message('Cancelled.');
  });

  $('form').addEventListener('submit', async event => {
    event.preventDefault();
    const file = fileInput.files[0];
    const patchFile = patchInput.files[0];
    if (!file || !patchFile || !supported || active) return;
    stop(); clearResult();
    if (file.size < 22 || file.size > maxSize) {
      message('Invalid IPSW file size.', true);
      return;
    }
    const job = generation;
    if (patchFile.size < 22 || patchFile.size > maxPatchSize) {
      message('Invalid patch file size.', true);
      return;
    }
    busy(true);
    message('Reading files…');
    try {
      const [stock, patch] = await Promise.all([file.arrayBuffer(), patchFile.arrayBuffer()]);
      if (job !== generation) return;
      const url = URL.createObjectURL(new Blob([workerCode], { type: 'text/javascript' }));
      let worker;
      try { worker = new Worker(url); } catch (error) { URL.revokeObjectURL(url); throw error; }
      active = { worker, url, timeout: setTimeout(() => {
        if (job === generation) { stop(); message('Patching timed out (120s).', true); }
      }, 120000) };
      worker.onmessage = event => {
        if (job !== generation) return;
        const data = event.data;
        if (data.type === 'progress') {
          message(data.message);
        } else if (data.type === 'complete') {
          stop();
          downloadURL = URL.createObjectURL(new Blob([data.bytes], { type: 'application/octet-stream' }));
          download.href = downloadURL;
          download.download = data.release.output_name;
          $('result-title').textContent = `${data.release.model} · ${data.release.revision}`;
          $('result-size').textContent = `${(data.bytes.byteLength / 1024 / 1024).toFixed(2)} MiB · verified`;
          $('source-hash').textContent = data.release.source_sha256;
          $('output-hash').textContent = data.sha256;
          result.hidden = false;
          message('Done.');
          download.focus();
        } else if (data.type === 'error') {
          stop(); clearResult(); message(data.message, true);
        }
      };
      worker.onerror = event => {
        event.preventDefault();
        if (job === generation) {
          stop(); clearResult(); message('Patch worker failed.', true);
        }
      };
      worker.postMessage({ stock, patch }, [stock, patch]);
    } catch (error) {
      if (job === generation) { stop(); clearResult(); message(`Patching failed: ${error.message}`, true); }
    }
  });
  window.addEventListener('pagehide', () => { stop(); clearResult(); });
  window.addEventListener('pageshow', event => { if (event.persisted) changed(); });
  busy(false);
  if (!supported) message('Requires Web Crypto, Web Workers and file APIs. Use HTTPS or local HTML.', true);
}());
