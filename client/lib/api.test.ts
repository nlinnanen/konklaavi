import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { summarize } from './aggregate';
import {
  applyIndexUpdate,
  HitReporter,
  INDEX_REFRESH_MS,
  LocalIndexApi,
  MAX_LOOKUP_BATCH,
  MOCK_NARRATIVES,
  MockApi,
  RemoteApi,
  SignatureIndex,
  type Hit,
  type IndexResponse,
  type KeyValueStore,
} from './api';
import { EMBEDDING_DIM } from './embed';
import { hashNormalized } from './fingerprint';
import { normalizeText } from './normalize';
import { tierOf } from './tier';
import type { LookupResult, Signature, VoteRequest } from './types';

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

function vote(over: Partial<VoteRequest> = {}): VoteRequest {
  return {
    userId: 'u1',
    contentHash: 'c1',
    sentenceHashes: ['s1', 's2'],
    verdict: 'misinformation',
    url: 'https://example.com/a',
    domain: 'example.com',
    createdAt: new Date().toISOString(),
    ...over,
  };
}

describe('summarize', () => {
  it('flags when negative share >= 50% and picks the top negative verdict', () => {
    const r = summarize('h', { misinformation: 1, manipulative: 2, misleading: 0, trustworthy: 3 }, 1);
    expect(r).toMatchObject({ total: 6, flagged: true, topVerdict: 'manipulative' });
  });
  it('does not flag mostly-trustworthy content', () => {
    const r = summarize('h', { misinformation: 1, manipulative: 0, misleading: 0, trustworthy: 3 }, 1);
    expect(r).toMatchObject({ flagged: false, topVerdict: 'trustworthy' });
  });
  it('respects minVotes', () => {
    const r = summarize('h', { misinformation: 2, manipulative: 0, misleading: 0, trustworthy: 0 }, 3);
    expect(r.flagged).toBe(false);
  });
});

describe('MockApi', () => {
  let api: MockApi;
  beforeEach(() => {
    api = new MockApi(memoryStore());
  });

  it('returns aggregates only for hashes with votes', async () => {
    await api.submitVote(vote());
    await api.submitVote(vote({ userId: 'u2', verdict: 'trustworthy', sentenceHashes: ['s2'], contentHash: 'c2' }));
    const res = await api.lookup(['s1', 's2', 'unknown']);
    const byHash = Object.fromEntries(res.map((r) => [r.hash, r]));
    expect(Object.keys(byHash).sort()).toEqual(['s1', 's2']);
    expect(byHash.s1).toMatchObject({ total: 1, flagged: true });
    expect(byHash.s2?.counts).toMatchObject({ misinformation: 1, trustworthy: 1 });
  });

  it('replaces a user’s earlier vote on the same content', async () => {
    await api.submitVote(vote());
    await api.submitVote(vote({ verdict: 'trustworthy' }));
    const [r] = await api.lookup(['s1']);
    expect(r).toMatchObject({ total: 1, flagged: false, topVerdict: 'trustworthy' });
  });

  it('computes domain stats', async () => {
    await api.submitVote(vote());
    await api.submitVote(vote({ userId: 'u2', verdict: 'misleading' }));
    await api.submitVote(vote({ userId: 'u3', contentHash: 'c9', verdict: 'trustworthy' }));
    await api.submitVote(vote({ userId: 'u4', domain: 'other.org' }));
    const s = await api.domainStats('example.com');
    expect(s).toMatchObject({ votesCount: 3, flaggedCount: 1, topVerdict: 'misinformation' });
  });

  it('rejects invalid votes', async () => {
    await expect(api.submitVote(vote({ sentenceHashes: [] }))).rejects.toThrow();
    await expect(api.submitVote(vote({ verdict: 'fake' as never }))).rejects.toThrow();
  });
});

describe('MockApi narratives', () => {
  it('flags a seeded narrative sentence with its signature and no votes', async () => {
    const api = new MockApi(memoryStore());
    const [narrative] = MOCK_NARRATIVES;
    const hash = await hashNormalized(normalizeText(narrative!.sentence));
    const [r] = await api.lookup([hash, 'unknown']);
    expect(r).toMatchObject({ hash, total: 0, flagged: true, signature: narrative!.signature });
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

function signature(narrativeId: string, state: Signature['state'] = 'active'): Signature {
  return {
    narrativeId,
    title: `Narrative ${narrativeId}`,
    verdict: 'misinformation',
    techniques: [],
    explanation: null,
    source: 'euvsdisinfo',
    sourceUrl: null,
    state,
    matchLevel: 'exact',
    settled: true,
  };
}

function index(over: Partial<IndexResponse> = {}): IndexResponse {
  return {
    version: 1,
    full: true,
    signatures: [],
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

describe('SignatureIndex', () => {
  const snapshot = index({
    signatures: [signature('n1'), signature('n2', 'under_review')],
    sentences: [
      { hash: 's1', narrativeId: 'n1' },
      { hash: 's2', narrativeId: 'n2' },
      { hash: 'shared', narrativeId: 'n2' },
      { hash: 'shared', narrativeId: 'n1' },
    ],
    fingerprints: [fingerprint('n1', { 0: 'a', 5: 'b' }), fingerprint('n1', { 9: 'd' }), fingerprint('n2', { 7: 'c' })],
  });
  const lookup = (hashes: string[], blocks: { hash: string; bands: string[] }[] = []) =>
    Object.fromEntries(new SignatureIndex(snapshot).lookup(hashes, blocks).map((r) => [r.hash, r]));

  it('matches sentence hashes exactly, flagging those in active narratives', () => {
    const r = lookup(['s1', 's2', 'unknown']);
    expect(Object.keys(r).sort()).toEqual(['s1', 's2']);
    expect(r.s1).toMatchObject({ flagged: true, total: 0, topVerdict: 'misinformation' });
    expect(r.s1?.signature).toMatchObject({ narrativeId: 'n1', matchLevel: 'exact' });
    expect(r.s2).toMatchObject({ flagged: false, signature: { narrativeId: 'n2' } });
  });

  it('reports a hash in several narratives with the active one', () => {
    expect(lookup(['shared']).shared?.signature?.narrativeId).toBe('n1');
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
    expect(r.variant).toMatchObject({ flagged: true, signature: { narrativeId: 'n1', matchLevel: 'variant' } });
  });

  it('gives a hash that is both a sentence and a block the exact match', () => {
    const r = lookup(['s1'], [{ hash: 's1', bands: blockBands({ 0: 'a', 5: 'b' }) }]);
    expect(r.s1?.signature?.matchLevel).toBe('exact');
  });
});

describe('applyIndexUpdate', () => {
  const base = applyIndexUpdate(
    undefined,
    index({
      signatures: [signature('n1'), signature('n2', 'under_review')],
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
        signatures: [signature('n2'), signature('n3')],
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
    expect(next.signatures.map((s) => [s.narrativeId, s.state])).toEqual([
      ['n2', 'active'],
      ['n3', 'active'],
    ]);
    expect(next.sentences.map((s) => s.hash).sort()).toEqual(['s3', 's4']);
    expect(next.fingerprints.map((f) => f.narrativeId)).toEqual(['n3']);
    expect(next.narrativeVectors).toEqual([{ narrativeId: 'n2', vectors: ['v2b'] }]);
  });

  it('replaces everything with a full index', () => {
    const next = applyIndexUpdate(base, index({ version: 9, signatures: [signature('n3')] }));
    expect(next).toEqual({
      version: 9,
      signatures: [signature('n3')],
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
      index({ version: 1, signatures: [signature('n1')], sentences: [{ hash: 's1', narrativeId: 'n1' }] }),
      index({ version: 2, full: false, signatures: [signature('n2')], sentences: [{ hash: 's2', narrativeId: 'n2' }] }),
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
        signatures: [signature('n1'), signature('n2'), { ...signature('n3', 'disputed'), source: 'report' }],
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
      results.map((r) => [r.hash, r.flagged, r.signature?.narrativeId, r.signature?.matchLevel, tierOf(r)]),
    ).toEqual([
      ['close', true, 'n1', 'narrative', 'likely'],
      ['between', false, 'n2', 'related', 'related'],
      ['opposite', false, 'n3', 'related', 'disputed'],
    ]);
    expect(results[1]?.signature?.similarity).toBeCloseTo(0.8, 2);
    await api.matchNarratives(units.slice(0, 1));
    expect(embedded).toEqual(Object.keys(vectors));
  });

  it('keeps the index across restarts of the background worker', async () => {
    const requests = backend([index({ signatures: [signature('n1')], sentences: [{ hash: 's1', narrativeId: 'n1' }] })]);
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
    counts: { misinformation: 0, manipulative: 0, misleading: 0, trustworthy: 0 },
    total: 0,
    flagged: true,
    topVerdict: 'misinformation',
    signature: signature(narrativeId),
  });
  const seenAt = new Date('2026-11-14T12:00:00Z');

  function reporter() {
    const batches: Hit[][] = [];
    return { batches, reporter: new HitReporter(async (hits) => void batches.push(hits)) };
  }

  it('sends each flagged narrative match once per page in one batch per lookup', async () => {
    const { batches, reporter: r } = reporter();
    const underReview = { ...flagged('s3', 'n2'), flagged: false };
    await r.report('https://news.example/a#top', [flagged('s1'), flagged('s2'), underReview], seenAt);
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
