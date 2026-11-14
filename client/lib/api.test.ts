import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  applyIndexUpdate,
  HitReporter,
  INDEX_REFRESH_MS,
  LocalIndexApi,
  MAX_LOOKUP_BATCH,
  NarrativeIndex,
  noteProblem,
  RemoteApi,
  safeHref,
  type Hit,
  type IndexResponse,
  type KeyValueStore,
} from './api';
import { EMBEDDING_DIM } from './embed';
import type { LookupResult, Narrative, NarrativeState } from './types';

function memoryStore(): KeyValueStore {
  const m = new Map<string, unknown>();
  return {
    async get<T>(k: string) {
      return structuredClone(m.get(k)) as T | undefined;
    },
    async set(k, v) {
      m.set(k, structuredClone(v));
    },
  };
}

describe('safeHref', () => {
  it('links to http and https sources', () => {
    expect(safeHref('https://example.org/report/x/')).toBe('https://example.org/report/x/');
  });
  it('refuses other schemes and malformed URLs, so a note cannot carry a script link', () => {
    expect(safeHref('javascript:alert(1)')).toBeNull();
    expect(safeHref('not a url')).toBeNull();
    expect(noteProblem({ body: 'Context.', sources: ['javascript:alert(1)'] })).toMatch(/not an http/);
    expect(noteProblem({ body: 'Context.', sources: ['https://example.org/'] })).toBeNull();
    expect(noteProblem({ body: 'Context.', sources: [] })).toBeNull();
  });
});

describe('RemoteApi.lookup', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('sends sentence hashes and blocks in batches the backend accepts', async () => {
    const bodies: { hashes: string[]; blocks: { hash: string; bands: string[] }[] }[] = [];
    vi.stubGlobal('fetch', async (_url: string, init: RequestInit) => {
      const body = JSON.parse(init.body as string);
      bodies.push(body);
      return new Response(JSON.stringify({ results: [] }));
    });
    const hashes = Array.from({ length: MAX_LOOKUP_BATCH + 1 }, (_, i) => `s${i}`);
    const blocks = [{ hash: 'b0', bands: ['00000000'] }];
    await new RemoteApi('http://backend').lookup(hashes, blocks);
    expect(bodies.flatMap((b) => b.hashes)).toEqual(hashes);
    expect(bodies.flatMap((b) => b.blocks)).toEqual(blocks);
    expect(bodies.every((b) => b.hashes.length <= MAX_LOOKUP_BATCH && b.blocks.length <= MAX_LOOKUP_BATCH)).toBe(true);
  });
});

function narrative(narrativeId: string, state: NarrativeState = 'misleading'): Narrative {
  return { narrativeId, title: `Narrative ${narrativeId}`, state, notes: [], spread: { sites: 1, firstSeen: '2026-11-01T00:00:00Z' } };
}

function index(over: Partial<IndexResponse> = {}): IndexResponse {
  return {
    version: 1,
    full: true,
    narratives: [],
    sentences: [],
    fingerprints: [],
    narrativeThreshold: 0.85,
    relatedThreshold: 0.7,
    narrativeVectors: [],
    removed: [],
    ...over,
  };
}

/** The index encoding of a vector: 384 int8 values round(v * 127), base64. */
function encodeVector(v: number[]): string {
  return btoa(String.fromCharCode(...v.map((x) => Math.round(x * 127) & 0xff)));
}

/** A 384-d unit vector whose first two components are `a` and `b`. */
function unit(a: number, b: number): number[] {
  return [a, b, ...Array<number>(EMBEDDING_DIM - 2).fill(0)];
}

/** An index fingerprint of `narrativeId` with only the bands in `set`. */
function fingerprint(narrativeId: string, set: Record<number, string>) {
  return { narrativeId, bands: Array.from({ length: 32 }, (_, i) => set[i] ?? null) };
}

/** 32 bands that share nothing with the index, with `set` overriding some indexes. */
function blockBands(set: Record<number, string> = {}): string[] {
  return Array.from({ length: 32 }, (_, i) => set[i] ?? `none${i}`);
}

describe('NarrativeIndex', () => {
  const snapshot = index({
    narratives: [narrative('n1'), narrative('n2', 'tracked')],
    sentences: [
      { hash: 's1', narrativeId: 'n1' },
      { hash: 's2', narrativeId: 'n2' },
      { hash: 'shared', narrativeId: 'n2' },
      { hash: 'shared', narrativeId: 'n1' },
    ],
    fingerprints: [fingerprint('n1', { 0: 'a', 5: 'b' }), fingerprint('n1', { 9: 'd' }), fingerprint('n2', { 7: 'c' })],
  });
  const lookup = (hashes: string[], blocks: { hash: string; bands: string[] }[] = []) =>
    Object.fromEntries(new NarrativeIndex(snapshot).lookup(hashes, blocks).map((r) => [r.hash, r]));

  it('matches sentence hashes exactly', () => {
    const r = lookup(['s1', 's2', 'unknown']);
    expect(Object.keys(r).sort()).toEqual(['s1', 's2']);
    expect(r.s1).toMatchObject({ matchLevel: 'exact', narrative: { narrativeId: 'n1' } });
    expect(r.s2?.narrative.narrativeId).toBe('n2');
  });

  it('reports a hash in several narratives with the one in the more urgent state', () => {
    expect(lookup(['shared']).shared?.narrative.narrativeId).toBe('n1');
  });

  it('matches a block as a variant when it shares 2 bands at the same index with one fingerprint', () => {
    const r = lookup(
      [],
      [
        { hash: 'variant', bands: blockBands({ 0: 'a', 5: 'b' }) },
        { hash: 'one-each', bands: blockBands({ 0: 'a', 7: 'c' }) },
        { hash: 'wrong-index', bands: blockBands({ 1: 'a', 5: 'b' }) },
        { hash: 'two-fingerprints', bands: blockBands({ 0: 'a', 9: 'd' }) },
      ],
    );
    expect(Object.keys(r)).toEqual(['variant']);
    expect(r.variant).toMatchObject({ matchLevel: 'variant', narrative: { narrativeId: 'n1' } });
  });

  it('gives a hash that is both a sentence and a block the exact match', () => {
    const r = lookup(['s1'], [{ hash: 's1', bands: blockBands({ 0: 'a', 5: 'b' }) }]);
    expect(r.s1?.matchLevel).toBe('exact');
  });
});

describe('applyIndexUpdate', () => {
  const base = applyIndexUpdate(
    undefined,
    index({
      narratives: [narrative('n1'), narrative('n2', 'tracked')],
      sentences: [
        { hash: 's1', narrativeId: 'n1' },
        { hash: 's2', narrativeId: 'n2' },
      ],
      fingerprints: [fingerprint('n1', { 0: 'a' }), fingerprint('n2', { 0: 'b' })],
      narrativeVectors: [
        { narrativeId: 'n1', vectors: ['v1'] },
        { narrativeId: 'n2', vectors: ['v2'] },
      ],
    }),
  );

  it('drops removed narratives and replaces the entries of changed ones from a delta', () => {
    const next = applyIndexUpdate(
      base,
      index({
        version: 2,
        full: false,
        narratives: [narrative('n2'), narrative('n3')],
        sentences: [
          { hash: 's3', narrativeId: 'n2' },
          { hash: 's4', narrativeId: 'n3' },
        ],
        fingerprints: [fingerprint('n3', { 1: 'c' })],
        narrativeVectors: [{ narrativeId: 'n2', vectors: ['v2b'] }],
        removed: ['n1'],
      }),
    );
    expect(next.version).toBe(2);
    expect(next.narratives.map((n) => [n.narrativeId, n.state])).toEqual([
      ['n2', 'misleading'],
      ['n3', 'misleading'],
    ]);
    expect(next.sentences.map((s) => s.hash).sort()).toEqual(['s3', 's4']);
    expect(next.fingerprints.map((f) => f.narrativeId)).toEqual(['n3']);
    expect(next.narrativeVectors).toEqual([{ narrativeId: 'n2', vectors: ['v2b'] }]);
  });

  it('replaces everything with a full index', () => {
    const next = applyIndexUpdate(base, index({ version: 9, narratives: [narrative('n3')] }));
    expect(next).toEqual({
      version: 9,
      narratives: [narrative('n3')],
      sentences: [],
      fingerprints: [],
      narrativeThreshold: 0.85,
      relatedThreshold: 0.7,
      narrativeVectors: [],
    });
  });
});

describe('LocalIndexApi', () => {
  afterEach(() => vi.unstubAllGlobals());

  function backend(responses: IndexResponse[]) {
    const requests: string[] = [];
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      requests.push(`${init?.method ?? 'GET'} ${url}`);
      return new Response(JSON.stringify(responses.shift()));
    });
    return requests;
  }

  it('answers lookups from the downloaded index and applies deltas once it is stale', async () => {
    const requests = backend([
      index({ version: 1, narratives: [narrative('n1')], sentences: [{ hash: 's1', narrativeId: 'n1' }] }),
      index({ version: 2, full: false, narratives: [narrative('n2')], sentences: [{ hash: 's2', narrativeId: 'n2' }] }),
    ]);
    let now = 0;
    const api = new LocalIndexApi('http://backend', memoryStore(), () => now);

    expect((await api.lookup(['s1', 's2'])).map((r) => r.hash)).toEqual(['s1']);
    now = INDEX_REFRESH_MS;
    await api.lookup([]);
    await api.refresh();
    expect((await api.lookup(['s1', 's2'])).map((r) => r.hash)).toEqual(['s1', 's2']);
    expect(requests).toEqual(['GET http://backend/index', 'GET http://backend/index?since=1']);
  });

  it('matches units to narratives at the narrative level and as related, embedding each once', async () => {
    backend([
      index({
        narratives: [narrative('n1'), narrative('n2'), narrative('n3', 'controversial')],
        narrativeVectors: [
          { narrativeId: 'n1', vectors: [encodeVector(unit(1, 0))] },
          { narrativeId: 'n2', vectors: [encodeVector(unit(0, 1))] },
          { narrativeId: 'n3', vectors: [encodeVector(unit(-1, 0))] },
        ],
      }),
    ]);
    const vectors: Record<string, number[]> = {
      close: unit(0.9, Math.sqrt(1 - 0.81)),
      between: unit(0.6, 0.8),
      far: unit(0.6, -0.8),
      opposite: unit(-0.8, 0.6),
    };
    const embedded: string[] = [];
    const embed = async (texts: string[]) => texts.map((t) => (embedded.push(t), vectors[t]!));
    const api = new LocalIndexApi('http://backend', memoryStore(), () => 0, embed);
    const units = Object.keys(vectors).map((text) => ({ hash: text, text }));

    const results = await api.matchNarratives(units);
    expect(
      results.map((r) => [r.hash, r.narrative.narrativeId, r.matchLevel]),
    ).toEqual([
      ['close', 'n1', 'narrative'],
      ['between', 'n2', 'related'],
      ['opposite', 'n3', 'related'],
    ]);
    expect(results[1]?.similarity).toBeCloseTo(0.8, 2);
    await api.matchNarratives(units.slice(0, 1));
    expect(embedded).toEqual(Object.keys(vectors));
  });

  it('keeps the index across restarts of the background worker', async () => {
    const requests = backend([index({ narratives: [narrative('n1')], sentences: [{ hash: 's1', narrativeId: 'n1' }] })]);
    const store = memoryStore();
    await new LocalIndexApi('http://backend', store, () => 0).lookup([]);
    const restarted = new LocalIndexApi('http://backend', store, () => 1);
    expect((await restarted.lookup(['s1'])).map((r) => r.hash)).toEqual(['s1']);
    expect(requests).toHaveLength(1);
  });
});

describe('HitReporter', () => {
  const flagged = (hash: string, narrativeId = 'n1'): LookupResult => ({
    hash,
    matchLevel: 'exact',
    narrative: narrative(narrativeId),
  });
  const seenAt = new Date('2026-11-14T12:00:00Z');

  function reporter() {
    const batches: Hit[][] = [];
    return { batches, reporter: new HitReporter(async (hits) => void batches.push(hits)) };
  }

  it('sends each narrative match but related ones once per page in one batch per lookup', async () => {
    const { batches, reporter: r } = reporter();
    const related: LookupResult = { ...flagged('s3', 'n2'), matchLevel: 'related' };
    await r.report('https://news.example/a#top', [flagged('s1'), flagged('s2'), related], seenAt);
    await r.report('https://news.example/a', [flagged('s1')], seenAt);
    await r.report('https://news.example/b', [flagged('s1')], seenAt);
    expect(batches).toEqual([
      [
        { hash: 's1', narrativeId: 'n1', url: 'https://news.example/a', seenAt: seenAt.toISOString() },
        { hash: 's2', narrativeId: 'n1', url: 'https://news.example/a', seenAt: seenAt.toISOString() },
      ],
      [{ hash: 's1', narrativeId: 'n1', url: 'https://news.example/b', seenAt: seenAt.toISOString() }],
    ]);
  });

  it('reports nothing for pages that are not http(s)', async () => {
    const { batches, reporter: r } = reporter();
    await r.report('file:///home/user/page.html', [flagged('s1')]);
    await r.report('chrome-extension://abc/popup.html', [flagged('s1')]);
    expect(batches).toEqual([]);
  });
});
