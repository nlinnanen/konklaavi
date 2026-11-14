import { chromium, expect, test, type BrowserContext } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import path from 'node:path';
import type { browser } from 'wxt/browser';
import { hashNormalized, splitSentences } from '../lib/fingerprint';
import { DEFAULT_SETTINGS, type LookupResult, type Settings } from '../lib/types';

const EXTENSION = path.join(import.meta.dirname, '../dist/chrome-mv3');
const FIXTURE = path.join(import.meta.dirname, 'fixture.html');
const FLAGGED = 'The European Union is secretly preparing to ban all private gardens by the end of the decade.';

/** Serves the fixture page and a backend whose /lookup flags one sentence. */
async function startStub(flaggedHash: string): Promise<Server> {
  const result: LookupResult = {
    hash: flaggedHash,
    counts: { misinformation: 0, manipulative: 0, misleading: 0, trustworthy: 0 },
    total: 0,
    flagged: true,
    topVerdict: 'misinformation',
    signature: {
      narrativeId: '1',
      title: 'EU plans to ban private gardens',
      verdict: 'misinformation',
      techniques: [],
      explanation: null,
      source: 'euvsdisinfo',
      sourceUrl: 'https://euvsdisinfo.eu/',
      state: 'active',
      matchLevel: 'exact',
    },
  };
  const server = createServer(async (req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    if (req.method === 'OPTIONS') return void res.writeHead(204).end();
    if (req.url === '/fixture.html') {
      return void res.writeHead(200, { 'Content-Type': 'text/html' }).end(await readFile(FIXTURE));
    }
    if (req.method === 'POST' && req.url === '/lookup') {
      let body = '';
      for await (const chunk of req) body += chunk;
      const { hashes } = JSON.parse(body) as { hashes: string[] };
      const results = hashes.includes(flaggedHash) ? [result] : [];
      return void res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({ results }));
    }
    res.writeHead(404).end();
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return server;
}

let context: BrowserContext;
let server: Server;
let baseUrl: string;

test.beforeAll(async () => {
  const [sentence] = splitSentences(FLAGGED);
  server = await startStub(await hashNormalized(sentence!.normalized));
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  context = await chromium.launchPersistentContext('', {
    channel: 'chromium',
    args: [`--disable-extensions-except=${EXTENSION}`, `--load-extension=${EXTENSION}`],
  });
});

test.afterAll(async () => {
  await context?.close();
  server?.close();
});

test('highlights a sentence the remote backend flags', async () => {
  const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'));
  const settings: Settings = { ...DEFAULT_SETTINGS, backendMode: 'remote', backendUrl: baseUrl };
  await worker.evaluate(
    (s) => (globalThis as unknown as { chrome: typeof browser }).chrome.storage.local.set({ settings: s }),
    settings,
  );

  const page = await context.newPage();
  await page.goto(`${baseUrl}/fixture.html`);

  const highlighted = () =>
    page.evaluate(() => [...(CSS.highlights.get('tv-misinformation') ?? [])].map((r) => r.toString()));
  await expect.poll(highlighted).toEqual([FLAGGED]);
});
