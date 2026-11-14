import { chromium, expect, test, type BrowserContext, type Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import path from 'node:path';
import type { browser } from 'wxt/browser';
import type { Hit, IndexResponse, LookupBlock } from '../lib/api';
import type { ExtensionSettings } from '../lib/extensionSettings';
import { fingerprintSentences, hashNormalized, splitSentences } from '../lib/fingerprint';
import { bands, minhash } from '../lib/minhash';
import { normalizeText } from '../lib/normalize';
import { DEFAULT_SETTINGS, type LookupResult, type Signature } from '../lib/types';

/** The development build, whose UI shadow root is open so the tooltip can be inspected. */
const EXTENSION = path.join(import.meta.dirname, '../dist/chrome-mv3-dev');
const FIXTURE = path.join(import.meta.dirname, 'fixture.html');
const FLAGGED = 'The European Union is secretly preparing to ban all private gardens by the end of the decade.';
/** A paragraph of the fixture that the stub treats as an edited copy of the narrative. */
const EDITED =
  'Brussels officials have quietly drafted rules that would outlaw home gardens within ten years. Critics say the plan was hidden from voters.';
const SOURCE_URL = 'https://euvsdisinfo.eu/report/gardens/';

const SIGNATURE: Signature = {
  narrativeId: '1',
  title: 'EU plans to ban private gardens',
  verdict: 'misinformation',
  techniques: [],
  explanation: null,
  source: 'euvsdisinfo',
  sourceUrl: SOURCE_URL,
  state: 'active',
  matchLevel: 'exact',
};

function result(hash: string, matchLevel: Signature['matchLevel']): LookupResult {
  return {
    hash,
    counts: { misinformation: 0, manipulative: 0, misleading: 0, trustworthy: 0 },
    total: 0,
    flagged: true,
    topVerdict: 'misinformation',
    signature: { ...SIGNATURE, matchLevel },
  };
}

async function readJson<T>(req: AsyncIterable<unknown>): Promise<T> {
  let body = '';
  for await (const chunk of req) body += chunk;
  return JSON.parse(body) as T;
}

/** What the extension sent to the stub backend. */
const received = { lookups: 0, hits: [] as Hit[] };

/**
 * Serves the fixture page and a backend that flags one sentence exactly and
 * one block, by its content hash in /lookup and by its bands in /index, as a
 * variant.
 */
async function startStub(sentenceHash: string, block: LookupBlock): Promise<Server> {
  const index: IndexResponse = {
    version: 1,
    full: true,
    signatures: [SIGNATURE],
    sentences: [{ hash: sentenceHash, narrativeId: SIGNATURE.narrativeId }],
    fingerprints: [{ narrativeId: SIGNATURE.narrativeId, bands: block.bands }],
    removed: [],
  };
  const server = createServer(async (req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    const json = (body: unknown) => void res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(body));
    if (req.method === 'OPTIONS') return void res.writeHead(204).end();
    if (req.url === '/fixture.html') {
      return void res.writeHead(200, { 'Content-Type': 'text/html' }).end(await readFile(FIXTURE));
    }
    if (req.method === 'POST' && req.url === '/lookup') {
      received.lookups++;
      const { hashes, blocks } = await readJson<{ hashes: string[]; blocks: LookupBlock[] }>(req);
      const results: LookupResult[] = [];
      if (hashes.includes(sentenceHash)) results.push(result(sentenceHash, 'exact'));
      if (blocks.some((b) => b.hash === block.hash && b.bands.length)) results.push(result(block.hash, 'variant'));
      return json({ results });
    }
    if (req.method === 'GET' && req.url?.startsWith('/index')) return json(index);
    if (req.method === 'POST' && req.url === '/hits') {
      received.hits.push(...(await readJson<{ hits: Hit[] }>(req)).hits);
      return json({ ok: true });
    }
    res.writeHead(404).end();
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return server;
}

let server: Server;
let baseUrl: string;
let sentenceHash: string;

test.beforeAll(async () => {
  const [sentence] = splitSentences(FLAGGED);
  sentenceHash = await hashNormalized(sentence!.normalized);
  const { contentHash } = await fingerprintSentences(splitSentences(EDITED));
  server = await startStub(sentenceHash, { hash: contentHash, bands: bands(minhash(normalizeText(EDITED))) });
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

test.afterAll(() => {
  server?.close();
});

let context: BrowserContext;
let page: Page;

/** Loads the extension with the given backend mode and opens the fixture page. */
async function openFixture(backendMode: ExtensionSettings['backendMode']) {
  context = await chromium.launchPersistentContext('', {
    channel: 'chromium',
    args: [`--disable-extensions-except=${EXTENSION}`, `--load-extension=${EXTENSION}`],
  });
  const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'));
  const settings: ExtensionSettings = { ...DEFAULT_SETTINGS, backendMode, backendUrl: baseUrl, sendHits: true };
  await worker.evaluate(
    (s) => (globalThis as unknown as { chrome: typeof browser }).chrome.storage.local.set({ settings: s }),
    settings,
  );
  page = await context.newPage();
  await page.goto(`${baseUrl}/fixture.html`);
}

const highlighted = (name: string) =>
  page.evaluate((n) => [...(CSS.highlights.get(n) ?? [])].map((r) => r.toString()), name);

test.describe('remote backend', () => {
  test.beforeAll(() => openFixture('remote'));
  test.afterAll(() => context?.close());

  test('highlights a sentence the remote backend flags', async () => {
    await expect.poll(() => highlighted('tv-misinformation')).toEqual([FLAGGED]);
  });

  test('highlights a whole block the backend matches as an edited copy', async () => {
    await expect.poll(() => highlighted('tv-misinformation-variant')).toEqual([EDITED]);
  });

  test('a clicked highlight pins its tooltip with the source link', async () => {
    await expect.poll(() => highlighted('tv-misinformation-variant')).toEqual([EDITED]);
    const box = await page.getByText('Brussels officials').boundingBox();
    await page.mouse.click(box!.x + 20, box!.y + box!.height / 2);

    const tooltip = page.locator('trust-verifier-ui .tooltip');
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toContainText('Edited copy of a known narrative');
    await expect(tooltip.getByRole('link', { name: 'EUvsDisinfo' })).toHaveAttribute('href', SOURCE_URL);

    // Moving the pointer off the highlight leaves a pinned tooltip in place.
    await page.mouse.move(5, 5);
    await page.waitForTimeout(200);
    await expect(tooltip).toBeVisible();
  });
});

test.describe('local index', () => {
  test.beforeAll(async () => {
    received.lookups = 0;
    received.hits = [];
    await openFixture('local');
  });
  test.afterAll(() => context?.close());

  test('highlights from the downloaded index without sending page hashes', async () => {
    await expect.poll(() => highlighted('tv-misinformation')).toEqual([FLAGGED]);
    await expect.poll(() => highlighted('tv-misinformation-variant')).toEqual([EDITED]);
    expect(received.lookups).toBe(0);
  });

  test('reports the detection hit for the flagged sentence', async () => {
    await expect
      .poll(() => received.hits.find((h) => h.hash === sentenceHash))
      .toMatchObject({ narrativeId: SIGNATURE.narrativeId, url: `${baseUrl}/fixture.html` });
  });
});
