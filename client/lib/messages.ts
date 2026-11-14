import { browser } from 'wxt/browser';
import type { LookupBlock } from './api';
import type { DomainStats, LookupResult, Signature, Verdict, VoteInput } from './types';

/** Requests handled by the background worker: [payload, response]. */
export interface BackgroundApi {
  vote: [VoteInput, null];
  lookup: [{ hashes: string[]; blocks: LookupBlock[] }, LookupResult[]];
  domainStats: [string, DomainStats];
  clearMock: [null, null];
}
export type BackgroundRequest = {
  [K in keyof BackgroundApi]: { target: 'background'; type: K; payload: BackgroundApi[K][0] };
}[keyof BackgroundApi];

export type Envelope<T> = { ok: true; data: T } | { ok: false; error: string };

export async function callBackground<K extends keyof BackgroundApi>(
  type: K,
  payload: BackgroundApi[K][0],
): Promise<BackgroundApi[K][1]> {
  const res = (await browser.runtime.sendMessage({ target: 'background', type, payload })) as
    | Envelope<BackgroundApi[K][1]>
    | undefined;
  if (!res) throw new Error('No response from background worker');
  if (!res.ok) throw new Error(res.error);
  return res.data;
}

/** Requests the popup sends to the content script in the active tab. */
export type ContentRequest = { target: 'content'; type: 'pageStatus' } | { target: 'content'; type: 'rescan' };

export interface FlaggedItem {
  text: string;
  verdict: Verdict;
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
};

/** The signature's source URL when it is safe to link to (http or https), else null. */
export function sourceHref(signature: Signature): string | null {
  if (!signature.sourceUrl) return null;
  try {
    const url = new URL(signature.sourceUrl);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

/** The verdict a flagged result is shown with: its narrative's verdict when it has one. */
export function displayVerdict(result: LookupResult): Verdict {
  return result.signature?.verdict ?? result.topVerdict;
}
