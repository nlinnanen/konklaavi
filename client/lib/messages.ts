import { browser } from 'wxt/browser';
import type { LookupBlock, NarrativeBlock } from './api';
import type { HighlightStyle } from './state';
import type {
  Credentials,
  Helpfulness,
  LookupResult,
  MatchLevel,
  MeResponse,
  Narrative,
  NarrativeDetail,
  NoteInput,
  NoteResponse,
} from './types';

/** Requests handled by the background worker: [payload, response]. */
export interface BackgroundApi {
  lookup: [{ hashes: string[]; blocks: LookupBlock[] }, LookupResult[]];
  /** Narrative-level and related matches for embedding units with no exact or variant match. */
  matchNarratives: [NarrativeBlock[], LookupResult[]];
  narrative: [string, NarrativeDetail];
  addNote: [NoteInput, NoteResponse];
  rateNote: [{ noteId: string; helpfulness: Helpfulness }, null];
  reportNote: [{ noteId: string; reason: string }, null];
  /** This install's ratings by note id. */
  myRatings: [null, Record<string, Helpfulness>];
  clearMock: [null, null];
  register: [Credentials, MeResponse];
  login: [Credentials, MeResponse];
  logout: [null, MeResponse];
  me: [null, MeResponse];
}
export type BackgroundRequest = {
  [K in keyof BackgroundApi]: { target: 'background'; type: K; payload: BackgroundApi[K][0] };
}[keyof BackgroundApi];

export type Envelope<T> = { ok: true; data: T } | { ok: false; error: string; status?: number };

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

/** A failed background request; `status` is the backend's HTTP status when there was one. */
export class BackgroundError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = 'BackgroundError';
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
  if (!res.ok) throw new BackgroundError(res.error, res.status);
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

/** A highlighted passage of the page. */
export interface PageMatch {
  text: string;
  style: HighlightStyle;
  matchLevel: MatchLevel;
  narrative: Narrative;
}

export interface PageStatus {
  scannedSentences: number;
  matches: PageMatch[];
  highlightSupported: boolean;
}
