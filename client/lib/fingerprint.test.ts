import { describe, expect, it } from 'vitest';
import { fingerprintSentences, hashNormalized, splitSentences } from './fingerprint';
import { normalizeText } from './normalize';

describe('normalizeText', () => {
  it('ignores case, punctuation, quote styles and whitespace', () => {
    expect(normalizeText('  The  Moon’s made of  CHEESE!! ')).toBe(normalizeText("the moon's made of cheese"));
  });
  it('applies NFKC (full-width, ligatures)', () => {
    expect(normalizeText('Ｆｕｌｌ ﬁle')).toBe('full file');
  });
  it('keeps non-latin letters and digits', () => {
    expect(normalizeText('Rokotteet aiheuttavat 5G-säteilyä.')).toBe('rokotteet aiheuttavat 5g säteilyä');
  });
});

describe('splitSentences', () => {
  const text = 'Short one. Vaccines contain microchips that track you everywhere. Ok. The election was stolen by millions of fake ballots!';

  it('drops sentences shorter than 5 words', () => {
    expect(splitSentences(text).map((s) => s.text)).toEqual([
      'Vaccines contain microchips that track you everywhere.',
      'The election was stolen by millions of fake ballots!',
    ]);
  });

  it('returns offsets that slice back to the sentence text', () => {
    for (const s of splitSentences(text)) expect(text.slice(s.start, s.end)).toBe(s.text);
  });
});

describe('hashing', () => {
  it('produces stable 64-char hex', async () => {
    const h = await hashNormalized('hello world');
    expect(h).toMatch(/^[0-9a-f]{64}$/);
    expect(await hashNormalized('hello world')).toBe(h);
  });

  it('matches the same sentence across differently formatted pages', async () => {
    const a = await fingerprintSentences(splitSentences('Vaccines contain   microchips that track you!'));
    const b = await fingerprintSentences(splitSentences('“VACCINES contain microchips that track you.”'));
    expect(a.sentenceHashes).toEqual(b.sentenceHashes);
  });

  it('treats extra words as a different sentence', async () => {
    const a = await fingerprintSentences(splitSentences('Breaking: vaccines contain microchips that track you.'));
    const b = await fingerprintSentences(splitSentences('Vaccines contain microchips that track you.'));
    expect(a.sentenceHashes).not.toEqual(b.sentenceHashes);
  });

  it('dedupes repeated sentences', async () => {
    const s = splitSentences('This exact claim is repeated here. This exact claim is repeated here.');
    const fp = await fingerprintSentences(s);
    expect(fp.sentenceHashes).toHaveLength(1);
  });
});
