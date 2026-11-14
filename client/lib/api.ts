import { emptyCounts, summarize } from './aggregate';
import { VERDICTS, type DomainStats, type LookupResult, type VoteRequest } from './types';

/** The backend contract. See PLAN.md. */
export interface TrustApi {
  submitVote(vote: VoteRequest): Promise<void>;
  lookup(hashes: string[]): Promise<LookupResult[]>;
  domainStats(domain: string): Promise<DomainStats>;
}

export const MAX_LOOKUP_BATCH = 500;

export function validateVote(vote: VoteRequest): void {
  if (!VERDICTS.includes(vote.verdict)) throw new Error(`Invalid verdict: ${vote.verdict}`);
  if (!vote.sentenceHashes.length) throw new Error('Vote has no sentence hashes');
  if (vote.comment && vote.comment.length > 500) throw new Error('Comment too long');
}

// ---------------------------------------------------------------------------
// Remote (real backend)
// ---------------------------------------------------------------------------

export class RemoteApi implements TrustApi {
  constructor(private baseUrl: string) {}

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const url = this.baseUrl.replace(/\/+$/, '') + path;
    const res = await fetch(url, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) throw new Error(`Backend ${res.status} ${res.statusText} for ${path}`);
    const text = await res.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  async submitVote(vote: VoteRequest): Promise<void> {
    validateVote(vote);
    await this.request('/votes', { method: 'POST', body: JSON.stringify(vote) });
  }

  async lookup(hashes: string[]): Promise<LookupResult[]> {
    const out: LookupResult[] = [];
    for (let i = 0; i < hashes.length; i += MAX_LOOKUP_BATCH) {
      const res = await this.request<{ results: LookupResult[] }>('/lookup', {
        method: 'POST',
        body: JSON.stringify({ hashes: hashes.slice(i, i + MAX_LOOKUP_BATCH) }),
      });
      out.push(...(res?.results ?? []));
    }
    return out;
  }

  domainStats(domain: string): Promise<DomainStats> {
    return this.request(`/domains/${encodeURIComponent(domain)}`);
  }
}

// ---------------------------------------------------------------------------
// Mock (in-extension, for development until the backend exists)
// ---------------------------------------------------------------------------

export interface KeyValueStore {
  get<T>(key: string): Promise<T | undefined>;
  set(key: string, value: unknown): Promise<void>;
}

const VOTES_KEY = 'mock:votes';

export class MockApi implements TrustApi {
  /** minVotes = 1 so a single local vote is enough to see flagging in action. */
  constructor(
    private store: KeyValueStore,
    private minVotes = 1,
  ) {}

  private async votes(): Promise<VoteRequest[]> {
    return (await this.store.get<VoteRequest[]>(VOTES_KEY)) ?? [];
  }

  async submitVote(vote: VoteRequest): Promise<void> {
    validateVote(vote);
    // One vote per user per piece of content: a new vote replaces the old one.
    const votes = (await this.votes()).filter(
      (v) => !(v.userId === vote.userId && v.contentHash === vote.contentHash),
    );
    votes.push(vote);
    await this.store.set(VOTES_KEY, votes);
  }

  async lookup(hashes: string[]): Promise<LookupResult[]> {
    const wanted = new Set(hashes);
    const counts = new Map<string, ReturnType<typeof emptyCounts>>();
    for (const vote of await this.votes()) {
      for (const h of vote.sentenceHashes) {
        if (!wanted.has(h)) continue;
        const c = counts.get(h) ?? emptyCounts();
        c[vote.verdict]++;
        counts.set(h, c);
      }
    }
    return [...counts].map(([hash, c]) => summarize(hash, c, this.minVotes));
  }

  async domainStats(domain: string): Promise<DomainStats> {
    const votes = (await this.votes()).filter((v) => v.domain === domain);
    const counts = emptyCounts();
    const perContent = new Map<string, ReturnType<typeof emptyCounts>>();
    for (const v of votes) {
      counts[v.verdict]++;
      const c = perContent.get(v.contentHash) ?? emptyCounts();
      c[v.verdict]++;
      perContent.set(v.contentHash, c);
    }
    const flaggedCount = [...perContent].filter(([h, c]) => summarize(h, c, this.minVotes).flagged).length;
    return {
      domain,
      votesCount: votes.length,
      flaggedCount,
      counts,
      topVerdict: votes.length ? summarize(domain, counts, 1).topVerdict : null,
    };
  }

  async clear(): Promise<void> {
    await this.store.set(VOTES_KEY, []);
  }
}
