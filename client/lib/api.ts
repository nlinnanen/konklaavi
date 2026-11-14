import { emptyCounts, summarize } from './aggregate';
import { hashNormalized } from './fingerprint';
import { normalizeText } from './normalize';
import {
  VERDICTS,
  type DomainStats,
  type Hit,
  type IndexResponse,
  type IndexSignature,
  type LookupBlock,
  type LookupResult,
  type Signature,
  type VoteRequest,
} from './types';

export type { Hit, LookupBlock };

/** The backend contract. See docs/api.md. */
export interface TrustApi {
  submitVote(vote: VoteRequest): Promise<void>;
  /** Results for sentence hashes (exact matches) and for block hashes (variant matches). */
  lookup(hashes: string[], blocks?: LookupBlock[]): Promise<LookupResult[]>;
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
      signal: AbortSignal.timeout(10_000),
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
    if (!res.ok) throw new Error(`Backend ${res.status} ${res.statusText} for ${path}`);
    const text = await res.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  async submitVote(vote: VoteRequest): Promise<void> {
    validateVote(vote);
    await this.request('/votes', { method: 'POST', body: JSON.stringify(vote) });
  }

  async lookup(hashes: string[], blocks: LookupBlock[] = []): Promise<LookupResult[]> {
    const out: LookupResult[] = [];
    for (let i = 0; i < Math.max(hashes.length, blocks.length); i += MAX_LOOKUP_BATCH) {
      const res = await this.request<{ results: LookupResult[] }>('/lookup', {
        method: 'POST',
        body: JSON.stringify({
          hashes: hashes.slice(i, i + MAX_LOOKUP_BATCH),
          blocks: blocks.slice(i, i + MAX_LOOKUP_BATCH),
        }),
      });
      out.push(...(res?.results ?? []));
    }
    return out;
  }

  domainStats(domain: string): Promise<DomainStats> {
    return this.request(`/domains/${encodeURIComponent(domain)}`);
  }

  /** The signature index, as a delta from `since` when the backend still has it. */
  index(since?: number): Promise<IndexResponse> {
    const query = since === undefined ? '' : `?since=${encodeURIComponent(String(since))}`;
    return this.request(`/index${query}`, { signal: AbortSignal.timeout(60_000) });
  }

  async submitHits(hits: Hit[]): Promise<void> {
    await this.request('/hits', { method: 'POST', body: JSON.stringify({ hits }) });
  }
}

// ---------------------------------------------------------------------------
// Local index (matches in the extension; page hashes stay in the browser)
// ---------------------------------------------------------------------------

export type { IndexResponse };

export type IndexSnapshot = Omit<IndexResponse, 'full' | 'removed'>;

/** The snapshot after `update`: a full index replaces it, a delta replaces the entries of the narratives it names. */
export function applyIndexUpdate(snapshot: IndexSnapshot | undefined, update: IndexResponse): IndexSnapshot {
  const { version, signatures, sentences, fingerprints } = update;
  if (update.full || !snapshot) return { version, signatures, sentences, fingerprints };
  const changed = new Set([...update.removed, ...signatures.map((s) => s.narrativeId)]);
  const kept = <T extends { narrativeId: string }>(list: T[]) => list.filter((e) => !changed.has(e.narrativeId));
  return {
    version,
    signatures: [...kept(snapshot.signatures), ...signatures],
    sentences: [...kept(snapshot.sentences), ...sentences],
    fingerprints: [...kept(snapshot.fingerprints), ...fingerprints],
  };
}

/** The signature a match is reported with: an active narrative first, then the one with the most shared bands. */
function pick(candidates: Map<string, number>, signatures: Map<string, IndexSignature>): IndexSignature | undefined {
  let best: { signature: IndexSignature; rank: number } | undefined;
  for (const [id, shared] of candidates) {
    const signature = signatures.get(id);
    if (!signature) continue;
    const rank = (signature.state === 'active' ? 1e6 : 0) + shared;
    if (!best || rank > best.rank) best = { signature, rank };
  }
  return best?.signature;
}

function indexResult(hash: string, signature: IndexSignature, matchLevel: Signature['matchLevel']): LookupResult {
  return {
    hash,
    counts: emptyCounts(),
    total: 0,
    flagged: signature.state === 'active',
    topVerdict: signature.verdict,
    signature: { ...signature, matchLevel },
  };
}

/** Answers lookups from an index snapshot by the rules of POST /lookup, without vote counts. */
export class SignatureIndex {
  private signatures: Map<string, IndexSignature>;
  private sentences = new Map<string, string[]>();
  /** `${band index}:${hash}` to the positions in `fingerprints` that carry it. */
  private bands = new Map<string, number[]>();
  private fingerprints: IndexSnapshot['fingerprints'];

  constructor(snapshot: IndexSnapshot) {
    this.signatures = new Map(snapshot.signatures.map((s) => [s.narrativeId, s]));
    this.fingerprints = snapshot.fingerprints;
    const add = <T>(m: Map<string, T[]>, key: string, value: T) => m.set(key, [...(m.get(key) ?? []), value]);
    for (const s of snapshot.sentences) add(this.sentences, s.hash, s.narrativeId);
    snapshot.fingerprints.forEach((f, position) =>
      f.bands.forEach((band, i) => {
        if (band !== null) add(this.bands, `${i}:${band}`, position);
      }),
    );
  }

  /**
   * Sentence hashes match exactly. A block matches at the variant level when
   * it shares at least 2 bands, at the same index, with one fingerprint.
   */
  lookup(hashes: string[], blocks: LookupBlock[] = []): LookupResult[] {
    const out = new Map<string, LookupResult>();
    for (const hash of hashes) {
      const ids = this.sentences.get(hash);
      const signature = ids && pick(new Map(ids.map((id) => [id, 1])), this.signatures);
      if (signature) out.set(hash, indexResult(hash, signature, 'exact'));
    }
    for (const block of blocks) {
      if (out.has(block.hash)) continue;
      const perFingerprint = new Map<number, number>();
      block.bands.forEach((band, i) => {
        for (const position of this.bands.get(`${i}:${band}`) ?? []) {
          perFingerprint.set(position, (perFingerprint.get(position) ?? 0) + 1);
        }
      });
      const shared = new Map<string, number>();
      for (const [position, n] of perFingerprint) {
        const id = this.fingerprints[position]!.narrativeId;
        if (n >= 2) shared.set(id, Math.max(shared.get(id) ?? 0, n));
      }
      const signature = pick(shared, this.signatures);
      if (signature) out.set(block.hash, indexResult(block.hash, signature, 'variant'));
    }
    return [...out.values()];
  }
}

/** How old a downloaded index gets before a lookup triggers a delta download. */
export const INDEX_REFRESH_MS = 5 * 60_000;
const INDEX_KEY = 'index';

interface StoredIndex {
  baseUrl: string;
  checkedAt: number;
  snapshot: IndexSnapshot;
}

/**
 * Downloads the signature index from the backend and answers lookups from it,
 * so no page hashes leave the browser. Votes and domain stats go to the backend.
 */
export class LocalIndexApi implements TrustApi {
  private remote: RemoteApi;
  private stored?: StoredIndex;
  private index?: SignatureIndex;
  private refreshing?: Promise<void>;

  constructor(
    readonly baseUrl: string,
    private store: KeyValueStore,
    private now: () => number = Date.now,
  ) {
    this.remote = new RemoteApi(baseUrl);
  }

  submitVote(vote: VoteRequest): Promise<void> {
    return this.remote.submitVote(vote);
  }

  domainStats(domain: string): Promise<DomainStats> {
    return this.remote.domainStats(domain);
  }

  /** Waits for the first download; an index older than INDEX_REFRESH_MS answers while a delta downloads. */
  async lookup(hashes: string[], blocks: LookupBlock[] = []): Promise<LookupResult[]> {
    if (!this.stored) {
      const stored = await this.store.get<StoredIndex>(INDEX_KEY);
      if (stored?.baseUrl === this.baseUrl && stored.snapshot.fingerprints) this.stored = stored;
    }
    if (!this.stored) await this.refresh();
    else if (this.now() - this.stored.checkedAt >= INDEX_REFRESH_MS) {
      this.refresh().catch((e) => console.warn('[trust-verifier] index refresh failed', e));
    }
    this.index ??= new SignatureIndex(this.stored!.snapshot);
    return this.index.lookup(hashes, blocks);
  }

  /** Downloads the full index, or the delta since the stored version. Concurrent calls share one download. */
  refresh(): Promise<void> {
    this.refreshing ??= this.download().finally(() => (this.refreshing = undefined));
    return this.refreshing;
  }

  private async download(): Promise<void> {
    const update = await this.remote.index(this.stored?.snapshot.version);
    this.stored = {
      baseUrl: this.baseUrl,
      checkedAt: this.now(),
      snapshot: applyIndexUpdate(this.stored?.snapshot, update),
    };
    this.index = undefined;
    await this.store.set(INDEX_KEY, this.stored);
  }
}

// ---------------------------------------------------------------------------
// Detection hits
// ---------------------------------------------------------------------------

/** Pages whose reported hashes are remembered, so a long session doesn't grow without bound. */
const MAX_REPORTED_PAGES = 500;

/** Reports flagged narrative matches as detection hits, each hash once per page. */
export class HitReporter {
  private reported = new Map<string, Set<string>>();

  constructor(private send: (hits: Hit[]) => Promise<void>) {}

  /** Sends one batch for the flagged results of a lookup on `pageUrl`; pages other than http(s) are not reported. */
  async report(pageUrl: string, results: LookupResult[], seenAt = new Date()): Promise<void> {
    let url: URL;
    try {
      url = new URL(pageUrl);
    } catch {
      return;
    }
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return;
    url.hash = '';
    const page = url.href;
    const reported = this.reported.get(page) ?? new Set<string>();
    this.reported.delete(page);
    this.reported.set(page, reported);
    if (this.reported.size > MAX_REPORTED_PAGES) this.reported.delete(this.reported.keys().next().value!);

    const hits: Hit[] = [];
    for (const r of results) {
      if (!r.flagged || !r.signature || reported.has(r.hash)) continue;
      reported.add(r.hash);
      hits.push({ hash: r.hash, narrativeId: r.signature.narrativeId, url: page, seenAt: seenAt.toISOString() });
    }
    if (hits.length) await this.send(hits);
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

/**
 * Sentences the mock backend treats as part of an active narrative, so the
 * signature UI can be tried without a backend: put one on any page.
 */
export const MOCK_NARRATIVES: { sentence: string; signature: Signature }[] = [
  {
    sentence: 'The European Union is secretly preparing to ban all private gardens by the end of the decade.',
    signature: {
      narrativeId: 'mock-1',
      title: 'EU plans to ban private gardens',
      verdict: 'misinformation',
      techniques: ['fabrication', 'conspiracy framing'],
      explanation:
        'No EU institution has proposed restricting private gardens. The claim distorts the Nature Restoration Law, which sets targets for public ecosystems and places no obligations on private households.',
      source: 'euvsdisinfo',
      sourceUrl: 'https://euvsdisinfo.eu/',
      state: 'active',
      matchLevel: 'exact',
    },
  },
];

export class MockApi implements TrustApi {
  /** minVotes = 1 so a single local vote is enough to see flagging in action. */
  constructor(
    private store: KeyValueStore,
    private minVotes = 1,
  ) {}

  private narratives?: Promise<Map<string, Signature>>;

  private signatures(): Promise<Map<string, Signature>> {
    this.narratives ??= Promise.all(
      MOCK_NARRATIVES.map(async (n) => [await hashNormalized(normalizeText(n.sentence)), n.signature] as const),
    ).then((entries) => new Map(entries));
    return this.narratives;
  }

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

  /** Matches sentences only; blocks are ignored. */
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
    const signatures = await this.signatures();
    for (const h of wanted) if (signatures.has(h) && !counts.has(h)) counts.set(h, emptyCounts());
    return [...counts].map(([hash, c]) => {
      const result = summarize(hash, c, this.minVotes);
      const signature = signatures.get(hash);
      if (!signature) return result;
      return { ...result, flagged: result.flagged || signature.state === 'active', signature };
    });
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
