import { browser } from 'wxt/browser';
import { MockApi, RemoteApi, type KeyValueStore, type TrustApi } from '@/lib/api';
import type { BackgroundRequest, Envelope } from '@/lib/messages';
import { getSettings } from '@/lib/settings';

const localStore: KeyValueStore = {
  async get<T>(key: string) {
    return (await browser.storage.local.get(key))[key] as T | undefined;
  },
  async set(key, value) {
    await browser.storage.local.set({ [key]: value });
  },
};

const mockApi = new MockApi(localStore);

async function getApi(): Promise<TrustApi> {
  const settings = await getSettings();
  return settings.backendMode === 'remote' ? new RemoteApi(settings.backendUrl) : mockApi;
}

async function getUserId(): Promise<string> {
  const stored = (await browser.storage.local.get('userId')).userId as string | undefined;
  if (stored) return stored;
  const id = crypto.randomUUID();
  await browser.storage.local.set({ userId: id });
  return id;
}

async function handle(msg: BackgroundRequest): Promise<unknown> {
  const api = await getApi();
  switch (msg.type) {
    case 'vote': {
      const settings = await getSettings();
      await api.submitVote({
        ...msg.payload,
        excerpt: settings.sendExcerpt ? msg.payload.excerpt : undefined,
        userId: await getUserId(),
        createdAt: new Date().toISOString(),
      });
      return null;
    }
    case 'lookup':
      return api.lookup(msg.payload.hashes, msg.payload.blocks);
    case 'domainStats':
      return api.domainStats(msg.payload);
    case 'clearMock':
      await mockApi.clear();
      return null;
  }
}

export default defineBackground(() => {
  browser.runtime.onInstalled.addListener(() => void getUserId());

  browser.runtime.onMessage.addListener((msg: any, _sender, sendResponse: (r: Envelope<unknown>) => void) => {
    if (msg?.target !== 'background') return false;
    handle(msg as BackgroundRequest).then(
      (data) => sendResponse({ ok: true, data }),
      (err) => sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) }),
    );
    return true; // keep the channel open for the async response
  });
});
