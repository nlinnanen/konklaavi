import { browser } from 'wxt/browser';
import type { EmbedRequest, Envelope } from '@/lib/messages';
import { createDeviceEmbedder } from './embedder';

const embed = createDeviceEmbedder();

browser.runtime.onMessage.addListener((msg: any, _sender, sendResponse: (r: Envelope<number[][]>) => void) => {
  const req = msg as EmbedRequest;
  if (req?.target !== 'offscreen' || req.type !== 'embed') return false;
  embed(req.texts).then(
    (data) => sendResponse({ ok: true, data }),
    (err) => sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) }),
  );
  return true;
});
