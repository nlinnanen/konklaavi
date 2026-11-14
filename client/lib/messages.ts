import { browser } from 'wxt/browser';
import type { LookupBlock, NarrativeBlock } from './api';
import type { Tier } from './tier';
import type { DomainStats, LookupResult, MeResponse, Signature, Verdict, VoteInput } from './types';

/** Requests handled by the background worker: [payload, response]. */
export interface BackgroundApi {
  vote: [VoteInput, null];
  lookup: [{ hashes: string[]; blocks: LookupBlock[] }, LookupResult[]];
  /** Narrative-level and related matches for embedding units with no exact or variant match. */
  matchNarratives: [NarrativeBlock[], LookupResult[]];
  domainStats: [string, DomainStats];
  clearMock: [null, null];
  /** Emails a sign-in link for this install to the given address. */
  requestMagicLink: [string, null];
  /** The install's account and reputation; null in mock mode, which has no accounts. */
  me: [null, MeResponse | null];
}
export type BackgroundRequest = {
  [K in keyof BackgroundApi]: { target: 'background'; type: K; payload: BackgroundApi[K][0] };
}[keyof BackgroundApi];

export type Envelope<T> = { ok: true; data: T } | { ok: false; error: string };

/**
 * Thrown when the extension was reloaded or removed while this content script
 * kept running: its runtime is gone and no message will ever be answered.
 */
export class ContextInvalidatedError extends Error {
  constructor() {
    super('Extension context invalidated');
    this.name = 'ContextInvalidatedError';
  }
}

/** True while this script's extension runtime is alive. */
export function runtimeAlive(): boolean {
  try {
    return Boolean(browser.runtime?.id);
  } catch {
    return false;
  }
}

export async function callBackground<K extends keyof BackgroundApi>(
  type: K,
  payload: BackgroundApi[K][0],
): Promise<BackgroundApi[K][1]> {
  if (!runtimeAlive()) throw new ContextInvalidatedError();
  let res: Envelope<BackgroundApi[K][1]> | undefined;
  try {
    res = (await browser.runtime.sendMessage({ target: 'background', type, payload })) as typeof res;
  } catch (e) {
    if (!runtimeAlive() || String(e).includes('Extension context invalidated')) throw new ContextInvalidatedError();
    throw e;
  }
  if (!res) throw new Error('No response from background worker');
  if (!res.ok) throw new Error(res.error);
  // A worker that doesn't handle `type` answers ok with no data.
  if (!('data' in res)) throw new Error(`Background worker sent no data for ${type}`);
  return res.data;
}

/** The background worker's request to the offscreen document, which embeds texts with the on-device model. */
export interface EmbedRequest {
  target: 'offscreen';
  type: 'embed';
  texts: string[];
}

/** Requests the popup sends to the content script in the active tab. */
export type ContentRequest = { target: 'content'; type: 'pageStatus' } | { target: 'content'; type: 'rescan' };

export interface FlaggedItem {
  text: string;
  verdict: Verdict;
  tier: Tier;
  total: number;
  signature?: Signature;
}

export interface PageStatus {
  domain: string;
  scannedSentences: number;
  flagged: FlaggedItem[];
  highlightSupported: boolean;
}

export const SOURCE_LABELS: Record<Signature['source'], string> = {
  euvsdisinfo: 'EUvsDisinfo',
  report: 'User reports',
  cluster: 'Detected cluster',
  network: 'Documented network (VIGINUM)',
  factcheck: 'Fact-checker',
};

/** The source URL when it is safe to link to (http or https), else null. */
export function sourceHref(source: { sourceUrl: string | null }): string | null {
  if (!source.sourceUrl) return null;
  try {
    const url = new URL(source.sourceUrl);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

/** The verdict a flagged result is shown with: its narrative's verdict when it has one. */
export function displayVerdict(result: LookupResult): Verdict {
  return result.signature?.verdict ?? result.topVerdict;
}
