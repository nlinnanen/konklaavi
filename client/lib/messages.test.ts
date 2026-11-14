import { describe, expect, it } from 'vitest';
import { sourceHref } from './messages';
import type { Signature } from './types';

const signature = (sourceUrl: string | null): Signature => ({
  narrativeId: 'n1',
  title: 't',
  verdict: 'misinformation',
  techniques: [],
  explanation: null,
  source: 'euvsdisinfo',
  sourceUrl,
  state: 'active',
  matchLevel: 'exact',
  settled: true,
});

describe('sourceHref', () => {
  it('links to http and https sources', () => {
    expect(sourceHref(signature('https://euvsdisinfo.eu/report/x/'))).toBe('https://euvsdisinfo.eu/report/x/');
  });
  it('refuses other schemes and malformed URLs', () => {
    expect(sourceHref(signature('javascript:alert(1)'))).toBeNull();
    expect(sourceHref(signature('not a url'))).toBeNull();
  });
});
