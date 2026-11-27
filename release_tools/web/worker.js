'use strict';
onmessage = async event => {
  try {
    const { stock, patch } = event.data;
    const result = await IpodPatcher.apply(new Uint8Array(stock), new Uint8Array(patch),
      progress => postMessage({ type: 'progress', ...progress }));
    postMessage({ type: 'complete', ...result, bytes: result.bytes.buffer }, [result.bytes.buffer]);
  } catch (error) {
    postMessage({ type: 'error', message: error instanceof Error ? error.message : String(error) });
  }
};
