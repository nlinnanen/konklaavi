import { EMBEDDING_DIM, type Embedder } from './embed';
import { displayState, matchRank } from './state';
import type {
  AuthRequest,
  Credentials,
  Helpfulness,
  Hit,
  IndexResponse,
  LookupBlock,
  LookupResult,
  MatchLevel,
  MeResponse,
  Narrative,
  NarrativeDetail,
  NarrativeVectors,
  Note,
  NoteInput,
  NoteRequest,
  NoteResponse,
  NoteStatus,
} from './types';

export type { Hit, IndexResponse, LookupBlock };

/** The backend contract. See docs/api.md. */
export interface TrustApi {
  /** Results for sentence hashes (exact matches) and for block hashes (variant matches). */
  lookup(hashes: string[], blocks?: LookupBlock[]): Promise<LookupResult[]>;
  /** Narrative-level and related results for embedding units, embedded on the device; only backends that keep text local have it. */
  matchNarratives?(units: NarrativeBlock[]): Promise<LookupResult[]>;
  narrative(narrativeId: string): Promise<NarrativeDetail>;
  addNote(note: NoteRequest): Promise<NoteResponse>;
  rateNote(noteId: string, userId: string, helpfulness: Helpfulness): Promise<void>;
  reportNote(noteId: string, userId: string, reason: string): Promise<void>;
  me(userId: string): Promise<MeResponse>;
  /** Creates the account and signs the install in; 409 when the email has an account. */
  register(req: AuthRequest): Promise<MeResponse>;
  /** Signs the install in; 401 for a wrong email or password. */
  login(req: AuthRequest): Promise<MeResponse>;
  logout(userId: string): Promise<MeResponse>;
}

export const MAX_LOOKUP_BATCH = 500;
export const MAX_NOTE_LENGTH = 1000;

/** An embedding unit offered for narrative matching: its content hash and the text `embeddingUnits` gives it. */
export interface NarrativeBlock {
  hash: string;
  text: string;
}

/** The URL when it is safe to link to (http or https), else null. */
export function safeHref(url: string): string | null {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : null;
  } catch {
    return null;
  }
}

/** What keeps a note from being submitted, in words for its writer; null when it can be. */
export function noteProblem(note: Pick<NoteInput, 'body' | 'sources'>): string | null {
  if (!note.body.trim()) return 'Write the context readers need.';
  if (note.body.length > MAX_NOTE_LENGTH) return `Keep the context under ${MAX_NOTE_LENGTH} characters.`;
  if (!note.sources.length) return 'Add at least one source link.';
  const bad = note.sources.find((s) => !safeHref(s));
  if (bad) return `"${bad}" is not an http(s) link.`;
  return null;
}

/** A backend error response; `status` 403 on writing a note means the install is not signed in. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
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
    const text = await res.text();
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const body = JSON.parse(text) as { detail?: unknown };
        if (typeof body.detail === 'string') detail = body.detail;
      } catch {
        // not JSON: keep the status line
      }
      throw new ApiError(res.status, detail);
    }
    return (text ? JSON.parse(text) : undefined) as T;
  }

  private post<T>(path: string, body: unknown): Promise<T> {
    return this.request(path, { method: 'POST', body: JSON.stringify(body) });
  }

  async lookup(hashes: string[], blocks: LookupBlock[] = []): Promise<LookupResult[]> {
    const out: LookupResult[] = [];
    for (let i = 0; i < Math.max(hashes.length, blocks.length); i += MAX_LOOKUP_BATCH) {
      const res = await this.post<{ results: LookupResult[] }>('/lookup', {
        hashes: hashes.slice(i, i + MAX_LOOKUP_BATCH),
        blocks: blocks.slice(i, i + MAX_LOOKUP_BATCH),
      });
      out.push(...(res?.results ?? []));
    }
    return out;
  }

  narrative(narrativeId: string): Promise<NarrativeDetail> {
    return this.request(`/narratives/${encodeURIComponent(narrativeId)}`);
  }

  addNote(note: NoteRequest): Promise<NoteResponse> {
    const problem = noteProblem(note);
    if (problem) return Promise.reject(new Error(problem));
    return this.post('/notes', note);
  }

  async rateNote(noteId: string, userId: string, helpfulness: Helpfulness): Promise<void> {
    await this.post(`/notes/${encodeURIComponent(noteId)}/ratings`, { userId, helpfulness });
  }

  async reportNote(noteId: string, userId: string, reason: string): Promise<void> {
    await this.post(`/notes/${encodeURIComponent(noteId)}/report`, { userId, reason });
  }

  me(userId: string): Promise<MeResponse> {
    return this.request(`/me?userId=${encodeURIComponent(userId)}`);
  }

  /** The narrative index, as a delta from `since` when the backend still has it. */
  index(since?: number): Promise<IndexResponse> {
    const query = since === undefined ? '' : `?since=${encodeURIComponent(String(since))}`;
    return this.request(`/index${query}`, { signal: AbortSignal.timeout(60_000) });
  }

  async submitHits(hits: Hit[]): Promise<void> {
    await this.post('/hits', { hits });
  }

  register(req: AuthRequest): Promise<MeResponse> {
    return this.post('/auth/register', req);
  }

  login(req: AuthRequest): Promise<MeResponse> {
    return this.post('/auth/login', req);
  }

  logout(userId: string): Promise<MeResponse> {
    return this.post('/auth/logout', { userId });
  }
}

// ---------------------------------------------------------------------------
// Local index (matches in the extension; page hashes stay in the browser)
// ---------------------------------------------------------------------------

export type IndexSnapshot = Omit<IndexResponse, 'full' | 'removed'>;

/** The snapshot after `update`: a full index replaces it, a delta replaces the entries of the narratives it names. */
export function applyIndexUpdate(snapshot: IndexSnapshot | undefined, update: IndexResponse): IndexSnapshot {
  const { version, narratives, sentences, fingerprints, narrativeThreshold, relatedThreshold, narrativeVectors } = update;
  if (update.full || !snapshot) {
    return { version, narratives, sentences, fingerprints, narrativeThreshold, relatedThreshold, narrativeVectors };
  }
  const changed = new Set([...update.removed, ...narratives.map((n) => n.narrativeId)]);
  const kept = <T extends { narrativeId: string }>(list: T[]) => list.filter((e) => !changed.has(e.narrativeId));
  return {
    version,
    narratives: [...kept(snapshot.narratives), ...narratives],
    sentences: [...kept(snapshot.sentences), ...sentences],
    fingerprints: [...kept(snapshot.fingerprints), ...fingerprints],
    narrativeThreshold,
    relatedThreshold,
    narrativeVectors: [...kept(snapshot.narrativeVectors), ...narrativeVectors],
  };
}

/** True when a delta changes nothing, so the stored snapshot need not be rewritten. */
function isEmptyDelta(update: IndexResponse): boolean {
  return !update.full && !update.narratives.length && !update.removed.length;
}

/** Narrative vectors as rows of one matrix of unit vectors, with each row's narrative. */
interface VectorTable {
  narrativeIds: string[];
  rows: Float32Array;
}

function vectorTable(entries: NarrativeVectors[]): VectorTable {
  const narrativeIds: string[] = [];
  const rows = new Float32Array(entries.reduce((n, e) => n + e.vectors.length, 0) * EMBEDDING_DIM);
  let base = 0;
  for (const { narrativeId, vectors } of entries) {
    for (const encoded of vectors) {
      const binary = atob(encoded);
      if (binary.length !== EMBEDDING_DIM) continue;
      let norm = 0;
      for (let k = 0; k < EMBEDDING_DIM; k++) {
        const v = (binary.charCodeAt(k) << 24) >> 24;
        rows[base + k] = v;
        norm += v * v;
      }
      if (!norm) continue;
      norm = Math.sqrt(norm);
      for (let k = 0; k < EMBEDDING_DIM; k++) rows[base + k]! /= norm;
      narrativeIds.push(narrativeId);
      base += EMBEDDING_DIM;
    }
  }
  return { narrativeIds, rows: rows.subarray(0, base) };
}

function unitVector(vector: ArrayLike<number>): Float32Array {
  const out = Float32Array.from(vector);
  let norm = 0;
  for (let k = 0; k < out.length; k++) norm += out[k]! * out[k]!;
  norm = Math.sqrt(norm) || 1;
  for (let k = 0; k < out.length; k++) out[k]! /= norm;
  return out;
}

/**
 * The result a match is reported with, from candidate narratives and their
 * scores (shared bands or cosine): the strongest match first, then the highest score.
 */
function pick(
  hash: string,
  candidates: Map<string, number>,
  narratives: Map<string, Narrative>,
  level: (score: number) => MatchLevel,
  similarity = false,
): LookupResult | undefined {
  let best: { result: LookupResult; rank: number; score: number } | undefined;
  for (const [id, score] of candidates) {
    const narrative = narratives.get(id);
    if (!narrative) continue;
    const matchLevel = level(score);
    const rank = matchRank(matchLevel, narrative.state);
    if (!best || rank > best.rank || (rank === best.rank && score > best.score)) {
      const result: LookupResult = { hash, matchLevel, narrative, ...(similarity ? { similarity: score } : {}) };
      best = { result, rank, score };
    }
  }
  return best?.result;
}

function push<K, V>(map: Map<K, V[]>, key: K, value: V) {
  const list = map.get(key);
  if (list) list.push(value);
  else map.set(key, [value]);
}

/** Answers lookups from an index snapshot by the rules of POST /lookup. */
export class NarrativeIndex {
  private narratives: Map<string, Narrative>;
  private sentences = new Map<string, string[]>();
  /** `${band index}:${hash}` to the positions in `fingerprints` that carry it. */
  private bands = new Map<string, number[]>();
  private fingerprints: IndexSnapshot['fingerprints'];
  private vectors?: VectorTable;

  constructor(private snapshot: IndexSnapshot) {
    this.narratives = new Map(snapshot.narratives.map((n) => [n.narrativeId, n]));
    this.fingerprints = snapshot.fingerprints;
    for (const s of snapshot.sentences) push(this.sentences, s.hash, s.narrativeId);
    snapshot.fingerprints.forEach((f, position) =>
      f.bands.forEach((band, i) => {
        if (band !== null) push(this.bands, `${i}:${band}`, position);
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
      const result = ids && pick(hash, new Map(ids.map((id) => [id, 1])), this.narratives, () => 'exact');
      if (result) out.set(hash, result);
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
      const result = pick(block.hash, shared, this.narratives, () => 'variant');
      if (result) out.set(block.hash, result);
    }
    return [...out.values()];
  }

  get hasNarrativeVectors(): boolean {
    return this.snapshot.narrativeVectors.length > 0;
  }

  /**
   * A unit matches a narrative at the narrative level when its cosine to one of
   * the narrative's vectors reaches `narrativeThreshold`, and is related to it
   * from `relatedThreshold`.
   */
  matchNarratives(units: { hash: string; vector: ArrayLike<number> }[]): LookupResult[] {
    this.vectors ??= vectorTable(this.snapshot.narrativeVectors);
    const { narrativeIds, rows } = this.vectors;
    const { narrativeThreshold, relatedThreshold } = this.snapshot;
    const level = (cosine: number): MatchLevel => (cosine >= narrativeThreshold ? 'narrative' : 'related');
    const out: LookupResult[] = [];
    for (const { hash, vector } of units) {
      const query = unitVector(vector);
      const best = new Map<string, number>();
      for (let r = 0, base = 0; r < narrativeIds.length; r++, base += EMBEDDING_DIM) {
        let cosine = 0;
        for (let k = 0; k < EMBEDDING_DIM; k++) cosine += query[k]! * rows[base + k]!;
        const id = narrativeIds[r]!;
        if (cosine >= relatedThreshold && cosine > (best.get(id) ?? -1)) best.set(id, cosine);
      }
      const result = pick(hash, best, this.narratives, level, true);
      if (result) out.push(result);
    }
    return out;
  }
}

/** Embeddings kept per unit content hash, so a unit seen again is not embedded again. */
const MAX_CACHED_EMBEDDINGS = 5000;

/** How old a downloaded index gets before a lookup triggers a delta download; the background also refreshes on this period. */
export const INDEX_REFRESH_MS = 30_000;

/** The narratives a download changed or removed; `added` when it brought narratives the index lacked, which may match any page. */
export interface IndexChange {
  narrativeIds: string[];
  added: boolean;
}
const INDEX_KEY = 'index';

interface StoredIndex {
  baseUrl: string;
  checkedAt: number;
  snapshot: IndexSnapshot;
}

/**
 * Downloads the narrative index from the backend and answers lookups from it,
 * so no page hashes leave the browser. Notes, ratings and accounts go to the backend.
 */
export class LocalIndexApi implements TrustApi {
  private remote: RemoteApi;
  private stored?: StoredIndex;
  private index?: NarrativeIndex;
  private refreshing?: Promise<void>;
  private embeddings = new Map<string, Float32Array>();
  /** Called after each download that changed the index. */
  onChange?: (change: IndexChange) => void;

  constructor(
    readonly baseUrl: string,
    private store: KeyValueStore,
    private now: () => number = Date.now,
    private embed?: Embedder,
  ) {
    this.remote = new RemoteApi(baseUrl);
  }

  narrative(narrativeId: string) {
    return this.remote.narrative(narrativeId);
  }
  addNote(note: NoteRequest) {
    return this.remote.addNote(note);
  }
  rateNote(noteId: string, userId: string, helpfulness: Helpfulness) {
    return this.remote.rateNote(noteId, userId, helpfulness);
  }
  reportNote(noteId: string, userId: string, reason: string) {
    return this.remote.reportNote(noteId, userId, reason);
  }
  me(userId: string) {
    return this.remote.me(userId);
  }
  register(req: AuthRequest) {
    return this.remote.register(req);
  }
  login(req: AuthRequest) {
    return this.remote.login(req);
  }
  logout(userId: string) {
    return this.remote.logout(userId);
  }

  async lookup(hashes: string[], blocks: LookupBlock[] = []): Promise<LookupResult[]> {
    return (await this.ready()).lookup(hashes, blocks);
  }

  /** Embeds the units and matches them against the narrative vectors. */
  async matchNarratives(units: NarrativeBlock[]): Promise<LookupResult[]> {
    const index = await this.ready();
    if (!this.embed || !units.length || !index.hasNarrativeVectors) return [];
    const vectors = await this.embedCached(units, this.embed);
    return index.matchNarratives(units.map((u, i) => ({ hash: u.hash, vector: vectors[i]! })));
  }

  private async embedCached(blocks: NarrativeBlock[], embed: Embedder): Promise<Float32Array[]> {
    const missing = [...new Map(blocks.filter((b) => !this.embeddings.has(b.hash)).map((b) => [b.hash, b.text]))];
    if (missing.length) {
      const vectors = await embed(missing.map(([, text]) => text));
      missing.forEach(([hash], i) => this.embeddings.set(hash, Float32Array.from(vectors[i]!)));
    }
    const out = blocks.map((b) => this.embeddings.get(b.hash)!);
    for (const hash of this.embeddings.keys()) {
      if (this.embeddings.size <= MAX_CACHED_EMBEDDINGS) break;
      this.embeddings.delete(hash);
    }
    return out;
  }

  /** Waits for the first download; an index older than INDEX_REFRESH_MS answers while a delta downloads. */
  private async ready(): Promise<NarrativeIndex> {
    await this.load();
    if (!this.stored) await this.refresh();
    else if (this.now() - this.stored.checkedAt >= INDEX_REFRESH_MS) {
      this.refresh().catch((e) => console.warn('[hashtrust] index refresh failed', e));
    }
    this.index ??= new NarrativeIndex(this.stored!.snapshot);
    return this.index;
  }

  /** Downloads the full index, or the delta since the stored version. Concurrent calls share one download. */
  refresh(): Promise<void> {
    this.refreshing ??= this.download().finally(() => (this.refreshing = undefined));
    return this.refreshing;
  }

  private async load(): Promise<void> {
    if (this.stored) return;
    const stored = await this.store.get<StoredIndex>(INDEX_KEY);
    // A stored index of another shape (without `narratives`) is downloaded again.
    if (stored?.baseUrl === this.baseUrl && Array.isArray(stored.snapshot.narratives)) this.stored ??= stored;
  }

  private async download(): Promise<void> {
    await this.load();
    const update = await this.remote.index(this.stored?.snapshot.version);
    if (this.stored && isEmptyDelta(update)) {
      this.stored = { ...this.stored, checkedAt: this.now(), snapshot: { ...this.stored.snapshot, version: update.version } };
      return;
    }
    const before = new Set(this.stored?.snapshot.narratives.map((n) => n.narrativeId));
    this.stored = {
      baseUrl: this.baseUrl,
      checkedAt: this.now(),
      snapshot: applyIndexUpdate(this.stored?.snapshot, update),
    };
    this.index = undefined;
    await this.store.set(INDEX_KEY, this.stored);
    const changed = [...(update.full ? before : update.removed), ...update.narratives.map((n) => n.narrativeId)];
    const narrativeIds = [...new Set(changed)];
    this.onChange?.({ narrativeIds, added: update.narratives.some((n) => !before.has(n.narrativeId)) });
  }
}

// ---------------------------------------------------------------------------
// Detection hits
// ---------------------------------------------------------------------------

/** Pages whose reported hashes are remembered, so a long session doesn't grow without bound. */
const MAX_REPORTED_PAGES = 500;

/** Reports narrative matches as detection hits, each hash once per page; related matches are too loose to count. */
export class HitReporter {
  private reported = new Map<string, Set<string>>();

  constructor(private send: (hits: Hit[]) => Promise<void>) {}

  /** Sends one batch for the matches of a lookup on `pageUrl`; pages other than http(s) are not reported. */
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
      if (r.matchLevel === 'related' || reported.has(r.hash)) continue;
      reported.add(r.hash);
      hits.push({ hash: r.hash, narrativeId: r.narrative.narrativeId, url: page, seenAt: seenAt.toISOString() });
    }
    if (hits.length) await this.send(hits);
  }
}

// ---------------------------------------------------------------------------
// Mock (in-extension, for trying the interface without a backend)
// ---------------------------------------------------------------------------

export interface KeyValueStore {
  get<T>(key: string): Promise<T | undefined>;
  set(key: string, value: unknown): Promise<void>;
}

const MOCK_KEY = 'mock:notes';

interface MockState {
  narratives: { narrativeId: string; title: string; hashes: string[]; urls: string[]; firstSeen: string }[];
  notes: (Omit<Note, 'status' | 'ratings'> & { narrativeId: string; author: string })[];
  ratings: { noteId: string; userId: string; helpfulness: Helpfulness }[];
  accounts?: Credentials[];
  /** The signed-in email; a fresh mock is signed in as MOCK_EMAIL. */
  signedInAs?: string | null;
}

const MOCK_EMAIL = 'you@mock.invalid';

const HELPFULNESS_SCORE: Record<Helpfulness, number> = { helpful: 1, somewhat: 0.5, not_helpful: 0 };

/**
 * Stores notes and ratings in extension storage, with one signed-in user. A
 * note's status is the mean of its ratings: helpful from 0.7, not helpful up
 * to 0.3, controversial between, so a single rating can try each state.
 */
export class MockApi implements TrustApi {
  constructor(private store: KeyValueStore) {}

  private async state(): Promise<MockState> {
    return (await this.store.get<MockState>(MOCK_KEY)) ?? { narratives: [], notes: [], ratings: [] };
  }

  private statusOf(state: MockState, noteId: string): { status: NoteStatus; ratings: Note['ratings'] } {
    const ratings = { helpful: 0, somewhat: 0, not_helpful: 0 };
    const own = state.ratings.filter((r) => r.noteId === noteId);
    for (const r of own) ratings[r.helpfulness]++;
    if (!own.length) return { status: 'needs_more_ratings', ratings };
    const mean = own.reduce((s, r) => s + HELPFULNESS_SCORE[r.helpfulness], 0) / own.length;
    const status = mean >= 0.7 ? 'helpful' : mean <= 0.3 ? 'not_helpful' : 'controversial';
    return { status, ratings };
  }

  private narrativeOf(state: MockState, narrativeId: string): NarrativeDetail {
    const n = state.narratives.find((x) => x.narrativeId === narrativeId);
    if (!n) throw new Error(`Unknown narrative ${narrativeId}`);
    const notes: Note[] = state.notes
      .filter((x) => x.narrativeId === narrativeId)
      .map(({ narrativeId: _, author: __, ...note }) => ({ ...note, ...this.statusOf(state, note.id) }))
      .sort((a, b) => Number(b.status === 'helpful') - Number(a.status === 'helpful'));
    const sites = new Set(n.urls.map((u) => new URL(u).hostname)).size;
    return {
      narrativeId,
      title: n.title,
      state: displayState(notes),
      notes,
      spread: { sites, pages: n.urls.length, firstSeen: n.firstSeen },
    };
  }

  /** Sentences match exactly and blocks by their content hash, as a variant. */
  async lookup(hashes: string[], blocks: LookupBlock[] = []): Promise<LookupResult[]> {
    const state = await this.state();
    const out: LookupResult[] = [];
    const find = (hash: string, matchLevel: MatchLevel) => {
      const n = state.narratives.find((x) => x.hashes.includes(hash));
      if (n) out.push({ hash, matchLevel, narrative: this.narrativeOf(state, n.narrativeId) });
    };
    hashes.forEach((h) => find(h, 'exact'));
    blocks.forEach((b) => find(b.hash, 'variant'));
    return out;
  }

  async narrative(narrativeId: string): Promise<NarrativeDetail> {
    return this.narrativeOf(await this.state(), narrativeId);
  }

  async addNote(req: NoteRequest): Promise<NoteResponse> {
    const problem = noteProblem(req);
    if (problem) throw new Error(problem);
    const state = await this.state();
    const { passage } = req;
    const hashes = [passage.contentHash, ...passage.sentenceHashes];
    let narrative = state.narratives.find((n) => n.hashes.some((h) => hashes.includes(h)));
    if (!narrative) {
      narrative = {
        narrativeId: `mock-${state.narratives.length + 1}`,
        title: passage.text.split(/(?<=[.!?])\s/)[0]!.slice(0, 200),
        hashes,
        urls: [],
        firstSeen: new Date().toISOString(),
      };
      state.narratives.push(narrative);
    }
    if (!narrative.urls.includes(passage.url)) narrative.urls.push(passage.url);
    const noteId = `mock-note-${state.notes.length + 1}`;
    state.notes.push({
      id: noteId,
      narrativeId: narrative.narrativeId,
      author: req.userId,
      classification: req.classification,
      body: req.body,
      sources: req.sources,
      createdAt: new Date().toISOString(),
    });
    await this.store.set(MOCK_KEY, state);
    return { noteId, narrativeId: narrative.narrativeId };
  }

  async rateNote(noteId: string, userId: string, helpfulness: Helpfulness): Promise<void> {
    const state = await this.state();
    state.ratings = [...state.ratings.filter((r) => !(r.noteId === noteId && r.userId === userId)), { noteId, userId, helpfulness }];
    await this.store.set(MOCK_KEY, state);
  }

  /** The mock has no moderation: a report changes nothing. */
  async reportNote(): Promise<void> {}

  async me(userId: string): Promise<MeResponse> {
    const state = await this.state();
    const ratings = state.ratings.filter((r) => r.userId === userId);
    const agreed = ratings.filter((r) => {
      const { status } = this.statusOf(state, r.noteId);
      return status === 'helpful' ? r.helpfulness !== 'not_helpful' : status === 'not_helpful' && r.helpfulness === 'not_helpful';
    }).length;
    const email = state.signedInAs === undefined ? MOCK_EMAIL : state.signedInAs;
    return {
      userId,
      email,
      verified: email !== null,
      reputation: email === null ? null : 0.5,
      moderator: false,
      notes: state.notes
        .filter((n) => n.author === userId)
        .map((n) => ({
          id: n.id,
          classification: n.classification,
          body: n.body,
          status: this.statusOf(state, n.id).status,
          moderation: 'visible',
          createdAt: n.createdAt,
          narrativeId: n.narrativeId,
          title: state.narratives.find((x) => x.narrativeId === n.narrativeId)!.title,
        })),
      ratings: { count: ratings.length, agreed },
    };
  }

  async register(req: AuthRequest): Promise<MeResponse> {
    const state = await this.state();
    const email = req.email.trim().toLowerCase();
    const accounts = state.accounts ?? [];
    if (email === MOCK_EMAIL || accounts.some((a) => a.email === email)) {
      throw new ApiError(409, 'an account with this email exists, sign in instead');
    }
    await this.store.set(MOCK_KEY, { ...state, accounts: [...accounts, { email, password: req.password }], signedInAs: email });
    return this.me(req.userId);
  }

  async login(req: AuthRequest): Promise<MeResponse> {
    const state = await this.state();
    const email = req.email.trim().toLowerCase();
    if (!(state.accounts ?? []).some((a) => a.email === email && a.password === req.password)) {
      throw new ApiError(401, 'wrong email or password');
    }
    await this.store.set(MOCK_KEY, { ...state, signedInAs: email });
    return this.me(req.userId);
  }

  async logout(userId: string): Promise<MeResponse> {
    await this.store.set(MOCK_KEY, { ...(await this.state()), signedInAs: null });
    return this.me(userId);
  }

  async clear(): Promise<void> {
    await this.store.set(MOCK_KEY, undefined);
  }
}
