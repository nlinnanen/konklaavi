/**
 * MinHash signatures and LSH bands over word 5-grams. Changing any constant
 * here invalidates every stored signature and band.
 */

export const SHINGLE_WORDS = 5;
export const NUM_PERMUTATIONS = 128;
export const BANDS = 32;
export const ROWS_PER_BAND = NUM_PERMUTATIONS / BANDS;

/** 32-bit MurmurHash3 (x86_32) of bytes, as an unsigned integer. */
export function murmur3(bytes: Uint8Array, seed: number): number {
  const c1 = 0xcc9e2d51;
  const c2 = 0x1b873593;
  let h = seed >>> 0;
  const tail = bytes.length & ~3;
  for (let i = 0; i < tail; i += 4) {
    let k = bytes[i]! | (bytes[i + 1]! << 8) | (bytes[i + 2]! << 16) | (bytes[i + 3]! << 24);
    k = Math.imul(k, c1);
    k = (k << 15) | (k >>> 17);
    k = Math.imul(k, c2);
    h ^= k;
    h = (h << 13) | (h >>> 19);
    h = (Math.imul(h, 5) + 0xe6546b64) | 0;
  }
  let k = 0;
  for (let i = bytes.length - 1; i >= tail; i--) k = (k << 8) | bytes[i]!;
  if (bytes.length > tail) {
    k = Math.imul(k, c1);
    k = (k << 15) | (k >>> 17);
    k = Math.imul(k, c2);
    h ^= k;
  }
  h ^= bytes.length;
  h ^= h >>> 16;
  h = Math.imul(h, 0x85ebca6b);
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35);
  h ^= h >>> 16;
  return h >>> 0;
}

/** Word 5-grams of normalized text; a text under 5 words is one shingle. */
export function shingles(normalized: string): string[] {
  const words = normalized.split(' ');
  if (words.length < SHINGLE_WORDS) return [normalized];
  const out = new Set<string>();
  for (let i = 0; i + SHINGLE_WORDS <= words.length; i++) out.add(words.slice(i, i + SHINGLE_WORDS).join(' '));
  return [...out];
}

/**
 * MinHash signature of normalized text: for each seed 0..127, the minimum
 * MurmurHash3 over the shingles. Values are signed 32-bit integers so they
 * fit a Postgres integer[].
 */
export function minhash(normalized: string): number[] {
  const encoder = new TextEncoder();
  const signature = new Uint32Array(NUM_PERMUTATIONS).fill(0xffffffff);
  for (const shingle of shingles(normalized)) {
    const bytes = encoder.encode(shingle);
    for (let seed = 0; seed < NUM_PERMUTATIONS; seed++) {
      const h = murmur3(bytes, seed);
      if (h < signature[seed]!) signature[seed] = h;
    }
  }
  return Array.from(signature, (h) => h | 0);
}

/** LSH bands: bands[i] is the 8-hex-char MurmurHash3 of i and the signature's 4 values in band i. */
export function bands(signature: number[]): string[] {
  const out: string[] = [];
  const view = new DataView(new ArrayBuffer(4 * (1 + ROWS_PER_BAND)));
  for (let band = 0; band < BANDS; band++) {
    view.setUint32(0, band, true);
    for (let row = 0; row < ROWS_PER_BAND; row++) {
      view.setInt32(4 * (1 + row), signature[band * ROWS_PER_BAND + row]!, true);
    }
    out.push(murmur3(new Uint8Array(view.buffer), 0).toString(16).padStart(8, '0'));
  }
  return out;
}

/** Estimated Jaccard similarity of the two shingle sets: the share of equal signature values. */
export function jaccard(a: number[], b: number[]): number {
  let equal = 0;
  for (let i = 0; i < a.length; i++) if (a[i] === b[i]) equal++;
  return equal / a.length;
}
