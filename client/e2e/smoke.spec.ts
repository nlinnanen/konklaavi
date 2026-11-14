import { chromium, expect, test, type BrowserContext, type Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import path from 'node:path';
import type { browser } from 'wxt/browser';
import type { Hit, IndexResponse, LookupBlock } from '../lib/api';
import { fingerprintSentences, hashNormalized, splitSentences } from '../lib/fingerprint';
import { bands, minhash } from '../lib/minhash';
import { normalizeText } from '../lib/normalize';
import {
  DEFAULT_SETTINGS,
  type LookupResult,
  type MeResponse,
  type Narrative,
  type NoteRequest,
  type RatingRequest,
  type Settings,
} from '../lib/types';

/** The development build, whose UI shadow root is open so the tooltip can be inspected. */
const EXTENSION = path.join(import.meta.dirname, '../dist/chrome-mv3-dev');
const FIXTURE = path.join(import.meta.dirname, 'fixture.html');
const FLAGGED = 'The European Union is secretly preparing to ban all private gardens by the end of the decade.';
/** A paragraph of the fixture that the stub treats as an edited copy of the narrative. */
const EDITED =
  'Brussels officials have quietly drafted rules that would outlaw home gardens within ten years. Critics say the plan was hidden from voters.';
const SOURCE_URL = 'https://factcheck.example/gardens/';
const NOTE_BODY = 'No EU institution has proposed restricting private gardens.';

const NARRATIVE: Narrative = {
  narrativeId: '1',
  title: 'EU plans to ban private gardens',
  state: 'misleading',
  notes: [
    {
      id: '7',
      classification: 'misleading',
      body: NOTE_BODY,
      sources: [SOURCE_URL],
      status: 'helpful',
      ratings: { helpful: 6, somewhat: 1, not_helpful: 0 },
      createdAt: '2026-11-01T00:00:00Z',
    },
  ],
  spread: { sites: 4, firstSeen: '2026-11-01T00:00:00Z' },
};

const result = (hash: string, matchLevel: LookupResult['matchLevel']): LookupResult => ({ hash, matchLevel, narrative: NARRATIVE });

async function readJson<T>(req: AsyncIterable<unknown>): Promise<T> {
  let body = '';
  for await (const chunk of req) body += chunk;
  return JSON.parse(body) as T;
}

/** What the extension sent to the stub backend. */
const received = {
  lookups: 0,
  hits: [] as Hit[],
  notes: [] as NoteRequest[],
  ratings: [] as { noteId: string; body: RatingRequest }[],
};

/**
 * Serves the fixture page and a backend with one misleading narrative that
 * holds one sentence exactly and one block, by its content hash in /lookup and
 * by its bands in /index, as a variant. The install is signed in.
 */
async function startStub(sentenceHash: string, block: LookupBlock): Promise<Server> {
  const index: IndexResponse = {
    version: 1,
    full: true,
    narratives: [NARRATIVE],
    sentences: [{ hash: sentenceHash, narrativeId: NARRATIVE.narrativeId }],
    fingerprints: [{ narrativeId: NARRATIVE.narrativeId, bands: block.bands }],
    narrativeThreshold: 0.85,
    relatedThreshold: 0.7,
    narrativeVectors: [],
    removed: [],
  };
  const me: MeResponse = {
    userId: 'u',
    email: 'reader@example.org',
    verified: true,
    reputation: 0.5,
    reputationCapped: false,
    reputationHint: null,
    moderator: false,
    notes: [],
    ratings: { count: 0, agreed: 0 },
  };
  const server = createServer(async (req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    const json = (body: unknown) => void res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(body));
    const url = req.url ?? '';
    if (req.method === 'OPTIONS') return void res.writeHead(204).end();
    if (url === '/fixture.html') {
      return void res.writeHead(200, { 'Content-Type': 'text/html' }).end(await readFile(FIXTURE));
    }
    if (req.method === 'POST' && url === '/lookup') {
      received.lookups++;
      const { hashes, blocks } = await readJson<{ hashes: string[]; blocks: LookupBlock[] }>(req);
      const results: LookupResult[] = [];
      if (hashes.includes(sentenceHash)) results.push(result(sentenceHash, 'exact'));
      if (blocks.some((b) => b.hash === block.hash && b.bands.length)) results.push(result(block.hash, 'variant'));
      return json({ results });
    }
    if (req.method === 'GET' && url.startsWith('/index')) return json(index);
    if (req.method === 'GET' && url === `/narratives/${NARRATIVE.narrativeId}`) {
      return json({ ...NARRATIVE, spread: { ...NARRATIVE.spread, pages: 9 } });
    }
    if (req.method === 'GET' && url.startsWith('/me')) return json(me);
    if (req.method === 'POST' && url === '/hits') {
      received.hits.push(...(await readJson<{ hits: Hit[] }>(req)).hits);
      return json({ ok: true });
    }
    if (req.method === 'POST' && url === '/notes') {
      received.notes.push(await readJson<NoteRequest>(req));
      return json({ noteId: '8', narrativeId: '2' });
    }
    const rating = url.match(/^\/notes\/(\w+)\/ratings$/);
    if (req.method === 'POST' && rating) {
      received.ratings.push({ noteId: rating[1]!, body: await readJson<RatingRequest>(req) });
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
async function openFixture(backendMode: Settings['backendMode']) {
  context = await chromium.launchPersistentContext('', {
    channel: 'chromium',
    args: [`--disable-extensions-except=${EXTENSION}`, `--load-extension=${EXTENSION}`],
  });
  const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'));
  const settings: Settings = { ...DEFAULT_SETTINGS, backendMode, backendUrl: baseUrl, sendHits: true };
  await worker.evaluate(
    (s) => (globalThis as unknown as { chrome: typeof browser }).chrome.storage.local.set({ settings: s }),
    settings,
  );
  page = await context.newPage();
  await page.goto(`${baseUrl}/fixture.html`);
}

const highlighted = (name: string) =>
  page.evaluate((n) => [...(CSS.highlights.get(n) ?? [])].map((r) => r.toString()).sort(), name);

const card = () => page.locator('hashtrust-ui .card');

test.describe('remote backend', () => {
  test.beforeAll(() => openFixture('remote'));
  test.afterAll(() => context?.close());

  test('highlights a sentence and an edited copy of a misleading narrative', async () => {
    await expect.poll(() => highlighted('ht-misleading')).toEqual([EDITED, FLAGGED].sort());
  });

  test('a clicked highlight pins its card with the helpful note, its source and rating buttons', async () => {
    await expect.poll(() => highlighted('ht-misleading')).toContain(EDITED);
    const box = await page.getByText('Brussels officials').boundingBox();
    await page.mouse.click(box!.x + 20, box!.y + box!.height / 2);

    await expect(card()).toBeVisible();
    await expect(card()).toContainText('Misleading');
    await expect(card()).toContainText('Edited copy');
    await expect(card()).toContainText('Seen on 4 sites since');
    await expect(card()).toContainText(NOTE_BODY);
    await expect(card().getByRole('link', { name: 'factcheck.example' })).toHaveAttribute('href', SOURCE_URL);

    // Moving the pointer off the highlight leaves a pinned card in place.
    await page.mouse.move(5, 5);
    await page.waitForTimeout(200);
    await expect(card()).toBeVisible();

    const helpful = card().getByRole('button', { name: 'Helpful', exact: true });
    await helpful.click();
    await expect(helpful).toHaveAttribute('aria-pressed', 'true');
    expect(received.ratings).toEqual([{ noteId: '7', body: { userId: expect.any(String), helpfulness: 'helpful' } }]);
    await page.keyboard.press('Escape');
  });

  test('adds context to a selected passage', async () => {
    const paragraph = page.getByText('Spring planting starts early');
    const box = await paragraph.boundingBox();
    await page.mouse.move(box!.x + 2, box!.y + 4);
    await page.mouse.down();
    await page.mouse.move(box!.x + 200, box!.y + 4);
    await page.mouse.up();
    await page.locator('hashtrust-ui .add-btn').click();

    const panel = page.locator('hashtrust-ui .panel');
    await expect(panel).toContainText('Add context');
    await panel.getByText('Not misleading', { exact: true }).click();
    await panel.getByRole('textbox', { name: /^Context/ }).fill('Planting dates vary by region.');
    await panel.getByRole('textbox', { name: /^Sources/ }).fill('https://gardening.example/calendar');
    await panel.getByRole('button', { name: 'Submit note' }).click();

    await expect.poll(() => received.notes.length).toBe(1);
    expect(received.notes[0]).toMatchObject({
      classification: 'not_misleading',
      body: 'Planting dates vary by region.',
      sources: ['https://gardening.example/calendar'],
      passage: { text: 'Spring planting starts early this year across most of the continent.', url: `${baseUrl}/fixture.html` },
    });
    await expect(panel).toBeHidden();
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
    await expect.poll(() => highlighted('ht-misleading')).toEqual([EDITED, FLAGGED].sort());
    expect(received.lookups).toBe(0);
  });

  test('reports the detection hit for the matched sentence', async () => {
    await expect
      .poll(() => received.hits.find((h) => h.hash === sentenceHash))
      .toMatchObject({ narrativeId: NARRATIVE.narrativeId, url: `${baseUrl}/fixture.html` });
  });
});
