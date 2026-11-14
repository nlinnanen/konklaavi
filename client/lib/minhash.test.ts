import { describe, expect, it } from 'vitest';
import { bands, jaccard, minhash, murmur3 } from './minhash';
import { normalizeText } from './normalize';

const PARAGRAPH =
  'Western sanctions against Russia have backfired and are destroying the European economy, ' +
  'while ordinary citizens in Germany and France pay record prices for gas and electricity ' +
  'because their governments follow orders from Washington instead of defending national interests.';

const signatureOf = (text: string) => minhash(normalizeText(text));

function sharedBands(a: string, b: string): number {
  const x = bands(signatureOf(a));
  const y = bands(signatureOf(b));
  return x.filter((band, i) => band === y[i]).length;
}

describe('murmur3', () => {
  it('matches the reference MurmurHash3 x86_32', () => {
    const utf8 = (s: string) => new TextEncoder().encode(s);
    expect(murmur3(utf8(''), 1)).toBe(0x514e28b7);
    expect(murmur3(utf8('The quick brown fox jumps over the lazy dog'), 0)).toBe(0x2e4ff723);
  });
});

describe('minhash and bands', () => {
  it('gives identical text an identical signature and bands', () => {
    expect(signatureOf(PARAGRAPH)).toEqual(signatureOf(`  ${PARAGRAPH.toUpperCase()}!! `));
    expect(sharedBands(PARAGRAPH, PARAGRAPH)).toBe(32);
  });

  it('keeps many bands of a copy with one word changed', () => {
    const edited = PARAGRAPH.replace('record prices', 'insane prices');
    expect(sharedBands(PARAGRAPH, edited)).toBeGreaterThanOrEqual(8);
    expect(jaccard(signatureOf(PARAGRAPH), signatureOf(edited))).toBeGreaterThan(0.7);
  });

  it('shares almost no bands with unrelated text', () => {
    const other =
      'The city council approved a new bicycle lane along the river after a long public consultation ' +
      'in which residents asked for safer routes to schools, parks and the central railway station.';
    expect(sharedBands(PARAGRAPH, other)).toBeLessThanOrEqual(1);
    expect(jaccard(signatureOf(PARAGRAPH), signatureOf(other))).toBeLessThan(0.1);
  });

  it('fingerprints a text under five words as one shingle', () => {
    expect(signatureOf('Sanctions backfired badly')).toEqual(signatureOf('sanctions backfired badly'));
    expect(jaccard(signatureOf('Sanctions backfired badly'), signatureOf('Sanctions backfired'))).toBe(0);
  });

  it('returns 128 signed 32-bit values and 32 eight-hex-char bands', () => {
    const signature = signatureOf(PARAGRAPH);
    expect(signature).toHaveLength(128);
    expect(signature.every((v) => Number.isInteger(v) && v >= -(2 ** 31) && v < 2 ** 31)).toBe(true);
    expect(bands(signature)).toHaveLength(32);
    expect(bands(signature).every((b) => /^[0-9a-f]{8}$/.test(b))).toBe(true);
  });
});
