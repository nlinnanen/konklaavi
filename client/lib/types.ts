export const VERDICTS = ['misinformation', 'manipulative', 'misleading', 'trustworthy'] as const;
export type Verdict = (typeof VERDICTS)[number];

/** Verdicts that count against content. */
export const NEGATIVE_VERDICTS = ['misinformation', 'manipulative', 'misleading'] as const;
export type NegativeVerdict = (typeof NEGATIVE_VERDICTS)[number];

export const VERDICT_LABELS: Record<Verdict, string> = {
  misinformation: 'Misinformation',
  manipulative: 'Manipulative',
  misleading: 'Misleading',
  trustworthy: 'Trustworthy',
};

export type VerdictCounts = Record<Verdict, number>;

/** Body of POST /votes. */
export interface VoteRequest {
  /** Anonymous per-install id. */
  userId: string;
  /** Hash of the whole (sentence-snapped) selection. */
  contentHash: string;
  /** Hashes of each sentence in the selection; used for matching on other pages. */
  sentenceHashes: string[];
  verdict: Verdict;
  comment?: string;
  url: string;
  domain: string;
  /** Human-readable selected text. Omitted when the user disables it. */
  excerpt?: string;
  /** ISO 8601 timestamp. */
  createdAt: string;
}

/** What the content script sends; background adds userId/createdAt. */
export type VoteInput = Omit<VoteRequest, 'userId' | 'createdAt'>;

/** One entry of the POST /lookup response. Only returned for hashes with votes. */
export interface LookupResult {
  hash: string;
  counts: VerdictCounts;
  total: number;
  flagged: boolean;
  /** When flagged: most-voted negative verdict. Otherwise: most-voted verdict overall. */
  topVerdict: Verdict;
}

/** Response of GET /domains/:domain. */
export interface DomainStats {
  domain: string;
  votesCount: number;
  /** Number of distinct pieces of content (contentHash) on this domain that are flagged. */
  flaggedCount: number;
  counts: VerdictCounts;
  topVerdict: Verdict | null;
}

export interface Settings {
  backendMode: 'mock' | 'remote';
  backendUrl: string;
  sendExcerpt: boolean;
  highlight: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  backendMode: 'mock',
  backendUrl: 'http://localhost:8000',
  sendExcerpt: true,
  highlight: true,
};
