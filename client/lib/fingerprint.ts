import { normalizeText, wordCount } from './normalize';

/** Prefix mixed into every hash so the algorithm can evolve without collisions. */
export const FINGERPRINT_VERSION = 'v1';
/** Sentences shorter than this are too generic to match on. */
export const MIN_SENTENCE_WORDS = 5;

export interface Sentence {
  /** Offsets into the source text (trimmed of surrounding whitespace). */
  start: number;
  end: number;
  text: string;
  normalized: string;
}

const segmenter = new Intl.Segmenter('en', { granularity: 'sentence' });

export function splitSentences(text: string): Sentence[] {
  const out: Sentence[] = [];
  for (const { segment, index } of segmenter.segment(text)) {
    const normalized = normalizeText(segment);
    if (wordCount(normalized) < MIN_SENTENCE_WORDS) continue;
    const lead = segment.length - segment.trimStart().length;
    const trimmed = segment.trim();
    out.push({ start: index + lead, end: index + lead + trimmed.length, text: trimmed, normalized });
  }
  return out;
}

export async function sha256Hex(input: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(input));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

/** Hash of an already-normalized string. */
export function hashNormalized(normalized: string): Promise<string> {
  return sha256Hex(`${FINGERPRINT_VERSION}:${normalized}`);
}

export interface ContentFingerprint {
  contentHash: string;
  sentenceHashes: string[];
}

/** Fingerprint a list of sentences that together form one voted-on piece of content. */
export async function fingerprintSentences(sentences: Sentence[]): Promise<ContentFingerprint> {
  const sentenceHashes = [...new Set(await Promise.all(sentences.map((s) => hashNormalized(s.normalized))))];
  const contentHash = await hashNormalized(sentences.map((s) => s.normalized).join(' '));
  return { contentHash, sentenceHashes };
}
