/**
 * Canonical text form used before hashing. Must stay stable: changing it
 * invalidates every stored hash (bump FINGERPRINT_VERSION in fingerprint.ts).
 */
export function normalizeText(input: string): string {
  return input
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, ' ') // drop punctuation, quotes, symbols, emoji
    .replace(/\s+/g, ' ')
    .trim();
}

export function wordCount(normalized: string): number {
  return normalized === '' ? 0 : normalized.split(' ').length;
}
