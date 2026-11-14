import { browser, type Browser } from 'wxt/browser';
import { HitReporter, LocalIndexApi, MockApi, RemoteApi, type KeyValueStore, type TrustApi } from '@/lib/api';
import type { Embedder } from '@/lib/embed';
import { getExtensionSettings, type ExtensionSettings } from '@/lib/extensionSettings';
import type { BackgroundRequest, EmbedRequest, Envelope } from '@/lib/messages';
import type { LookupResult } from '@/lib/types';

const localStore: KeyValueStore = {
  async get<T>(key: string) {
    return (await browser.storage.local.get(key))[key] as T | undefined;
  },
  async set(key, value) {
    await browser.storage.local.set({ [key]: value });
  },
};

/** IndexedDB, which holds an index larger than storage.local's quota. */
function indexedDbStore(name: string): KeyValueStore {
  const STORE = 'kv';
  const db = new Promise<IDBDatabase>((resolve, reject) => {
    const req = indexedDB.open(name, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  const run = async <T>(mode: IDBTransactionMode, op: (s: IDBObjectStore) => IDBRequest): Promise<T> => {
    const store = (await db).transaction(STORE, mode).objectStore(STORE);
    return new Promise((resolve, reject) => {
      const req = op(store);
      req.onsuccess = () => resolve(req.result as T);
      req.onerror = () => reject(req.error);
    });
  };
  return {
    get: (key) => run('readonly', (s) => s.get(key)),
    set: (key, value) => run('readwrite', (s) => s.put(value, key)),
  };
}

/**
 * Embeds texts on the device, off any page's main thread: in an offscreen
 * document in Chrome, whose service worker can't load the ONNX runtime, and in
 * the background page in Firefox.
 */
function deviceEmbedder(): Embedder {
  if (import.meta.env.FIREFOX) {
    let embedder: Promise<Embedder> | undefined;
    return async (texts) => (await (embedder ??= import('./offscreen/embedder').then((m) => m.createDeviceEmbedder())))(texts);
  }
  let offscreen: Promise<void> | undefined;
  const ensureOffscreen = () =>
    (offscreen ??= (async () => {
      if (await browser.offscreen.hasDocument()) return;
      await browser.offscreen.createDocument({
        url: '/offscreen.html',
        reasons: ['WORKERS'],
        justification: 'Embeds page text on the device to match it against known narratives.',
      });
    })().catch((e) => {
      offscreen = undefined;
      throw e;
    }));
  return async (texts) => {
    await ensureOffscreen();
    const req: EmbedRequest = { target: 'offscreen', type: 'embed', texts };
    const res = (await browser.runtime.sendMessage(req)) as Envelope<number[][]> | undefined;
    if (!res) throw new Error('No response from the offscreen embedder');
    if (!res.ok) throw new Error(res.error);
    return res.data;
  };
}

const mockApi = new MockApi(localStore);
const embedder = deviceEmbedder();
const indexStore = indexedDbStore('hashtrust');
let localApi: LocalIndexApi | undefined;

function getApi(settings: ExtensionSettings): TrustApi {
  switch (settings.backendMode) {
    case 'remote':
      return new RemoteApi(settings.backendUrl);
    case 'local':
      if (localApi?.baseUrl !== settings.backendUrl) {
        localApi = new LocalIndexApi(settings.backendUrl, indexStore, Date.now, embedder);
      }
      return localApi;
    case 'mock':
      return mockApi;
  }
}

const hitReporters = new Map<string, HitReporter>();

/** Reports a local lookup's flagged matches on the sender's page, unless detection reports are off. */
function reportHits(settings: ExtensionSettings, pageUrl: string | undefined, results: LookupResult[]): void {
  if (settings.backendMode !== 'local' || !settings.sendHits || !pageUrl) return;
  let reporter = hitReporters.get(settings.backendUrl);
  if (!reporter) {
    const remote = new RemoteApi(settings.backendUrl);
    reporter = new HitReporter((hits) => remote.submitHits(hits));
    hitReporters.set(settings.backendUrl, reporter);
  }
  reporter.report(pageUrl, results).catch((e) => console.warn('[hashtrust] sending detection hits failed', e));
}

async function getUserId(): Promise<string> {
  const stored = (await browser.storage.local.get('userId')).userId as string | undefined;
  if (stored) return stored;
  const id = crypto.randomUUID();
  await browser.storage.local.set({ userId: id });
  return id;
}

async function handle(msg: BackgroundRequest, sender: Browser.runtime.MessageSender): Promise<unknown> {
  const settings = await getExtensionSettings();
  const api = getApi(settings);
  switch (msg.type) {
    case 'vote': {
      await api.submitVote({
        ...msg.payload,
        excerpt: settings.sendExcerpt ? msg.payload.excerpt : undefined,
        userId: await getUserId(),
        createdAt: new Date().toISOString(),
      });
      return null;
    }
    case 'lookup': {
      const results = await api.lookup(msg.payload.hashes, msg.payload.blocks);
      reportHits(settings, sender.url, results);
      return results;
    }
    case 'matchNarratives': {
      const results = (await api.matchNarratives?.(msg.payload)) ?? [];
      reportHits(settings, sender.url, results);
      return results;
    }
    case 'domainStats':
      return api.domainStats(msg.payload);
    case 'clearMock':
      await mockApi.clear();
      return null;
  }
}

export default defineBackground(() => {
  browser.runtime.onInstalled.addListener(() => void getUserId());

  browser.runtime.onMessage.addListener((msg: any, sender, sendResponse: (r: Envelope<unknown>) => void) => {
    if (msg?.target !== 'background') return false;
    handle(msg as BackgroundRequest, sender).then(
      (data) => sendResponse({ ok: true, data }),
      (err) => sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) }),
    );
    return true; // keep the channel open for the async response
  });
});
