export const CLASSIFICATIONS = ['misleading', 'not_misleading'] as const;
/** What a note says about its narrative. */
export type Classification = (typeof CLASSIFICATIONS)[number];

export const HELPFULNESS = ['helpful', 'somewhat', 'not_helpful'] as const;
export type Helpfulness = (typeof HELPFULNESS)[number];
export type RatingCounts = Record<Helpfulness, number>;

/** A note's status as the scoring job decides it. */
export type NoteStatus = 'needs_more_ratings' | 'helpful' | 'not_helpful' | 'controversial';

/** How a narrative is shown, from its visible notes. */
export type NarrativeState = 'misleading' | 'context' | 'controversial' | 'needs_ratings' | 'tracked';

/** How strongly a passage matched its narrative, strongest first. */
export type MatchLevel = 'exact' | 'variant' | 'narrative' | 'related';

/** A visible note. */
export interface Note {
  id: string;
  classification: Classification;
  /** At most 1000 characters. */
  body: string;
  /** Source URLs. */
  sources: string[];
  status: NoteStatus;
  ratings: RatingCounts;
  /** ISO 8601. */
  createdAt: string;
}

/** A narrative as lookups and the index ship it. */
export interface Narrative {
  narrativeId: string;
  title: string;
  state: NarrativeState;
  /** Visible notes, the decided helpful one first. */
  notes: Note[];
  /** `firstSeen` is ISO 8601: the first observation, or the narrative's creation before any. */
  spread: { sites: number; firstSeen: string };
}

/** Response of GET /narratives/{id}. */
export interface NarrativeDetail extends Narrative {
  spread: { sites: number; pages: number; firstSeen: string };
}

/** One entry of the POST /lookup `blocks`: a text block's content hash and LSH bands, matched at the variant level. */
export interface LookupBlock {
  hash: string;
  bands: string[];
}

/** One entry of the POST /lookup response, for hashes that belong to a narrative. */
export interface LookupResult {
  hash: string;
  /** `related` is a narrative-level cosine below `narrativeThreshold` but at least `relatedThreshold`. */
  matchLevel: MatchLevel;
  /** Cosine of a narrative-level or related match. */
  similarity?: number;
  narrative: Narrative;
}

/** One fingerprint of GET /index: `bands[i]` is LSH band i, null where the block has none. */
export interface IndexFingerprint {
  narrativeId: string;
  bands: (string | null)[];
}

/** A narrative's representative unit vectors, each 384 int8 values round(v * 127), base64-encoded. */
export interface NarrativeVectors {
  narrativeId: string;
  vectors: string[];
}

/**
 * Body of GET /index. A full index replaces the client's. In a delta, each narrative in `narratives`
 * comes with all its sentences and fingerprints and replaces the client's entries for it, and
 * `removed` lists the narrativeIds no longer shipped.
 */
export interface IndexResponse {
  /** Opaque and increasing; sent back as `since`. */
  version: number;
  full: boolean;
  narratives: Narrative[];
  sentences: { hash: string; narrativeId: string }[];
  fingerprints: IndexFingerprint[];
  /** Cosine at or above which a unit matches a narrative's vectors. */
  narrativeThreshold: number;
  /** Cosine at or above which a unit is related to a narrative. */
  relatedThreshold: number;
  narrativeVectors: NarrativeVectors[];
  removed: string[];
}

/** One entry of the POST /hits body. */
export interface Hit {
  hash: string;
  narrativeId: string;
  url: string;
  seenAt: string;
}

/** The passage a note is written on, sent only when the user submits the note. */
export interface Passage {
  text: string;
  url: string;
  contentHash: string;
  sentenceHashes: string[];
}

/** Body of POST /notes. */
export interface NoteRequest {
  userId: string;
  passage: Passage;
  classification: Classification;
  body: string;
  sources: string[];
}

/** What the content script sends; the background adds userId. */
export type NoteInput = Omit<NoteRequest, 'userId'>;

/** Response of POST /notes. */
export interface NoteResponse {
  noteId: string;
  narrativeId: string;
}

/** Body of POST /notes/{id}/ratings. */
export interface RatingRequest {
  userId: string;
  helpfulness: Helpfulness;
}

/** Body of POST /notes/{id}/report. */
export interface ReportRequest {
  userId: string;
  reason: string;
}

export interface Settings {
  /** `local` matches against a downloaded GET /index, so page hashes stay in the browser. */
  backendMode: 'mock' | 'remote' | 'local';
  backendUrl: string;
  highlight: boolean;
  /** Send detection hits to the backend in local index mode. */
  sendHits: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  backendMode: 'mock',
  backendUrl: 'http://localhost:8000',
  highlight: true,
  sendHits: true,
};

/** Body of POST /auth/magic-link: emails a sign-in link that links this install to an account. */
export interface MagicLinkRequest {
  userId: string;
  email: string;
}

/** Response of POST /auth/magic-link; `signedIn` when the server skips email verification (demo servers). */
export interface MagicLinkResponse {
  ok: true;
  signedIn?: boolean;
}

/** A note the user wrote, as GET /me lists it. */
export interface AccountNote {
  id: string;
  classification: Classification;
  body: string;
  status: NoteStatus;
  moderation: 'visible' | 'pending_review' | 'hidden';
  createdAt: string;
  narrativeId: string;
  title: string;
}

/** Response of GET /me?userId=...; `reputation` is in [0, 1], null for an install not linked to an account. */
export interface MeResponse {
  userId: string;
  email: string | null;
  verified: boolean;
  reputation: number | null;
  /** May work the moderation queue: MODERATOR_EMAILS or reputation above MODERATOR_REPUTATION. */
  moderator: boolean;
  notes: AccountNote[];
  /** Ratings cast, and those cast before the note was decided that agree with its status. */
  ratings: { count: number; agreed: number };
}
