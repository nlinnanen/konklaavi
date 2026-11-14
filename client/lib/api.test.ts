import { beforeEach, describe, expect, it } from 'vitest';
import { summarize } from './aggregate';
import { MockApi, type KeyValueStore } from './api';
import type { VoteRequest } from './types';

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
