import { describe, expect, it } from 'vitest';
import { emptyCounts } from './aggregate';
import { tierOf } from './tier';
import type { LookupResult, Signature } from './types';

function result(signature?: Partial<Signature>, flagged = false): LookupResult {
  return {
    hash: 'h',
    counts: emptyCounts(),
    total: 0,
    flagged,
    topVerdict: 'misinformation',
    signature: signature && {
      narrativeId: 'n1',
      title: 'T',
      verdict: 'misinformation',
      techniques: [],
      explanation: null,
      source: 'euvsdisinfo',
      sourceUrl: null,
      state: 'active',
      matchLevel: 'exact',
      settled: true,
      ...signature,
    },
  };
}

describe('tierOf', () => {
  it('ranks a match by its narrative state, settlement and match level', () => {
    expect(tierOf(result({}))).toBe('confirmed');
    expect(tierOf(result({ matchLevel: 'variant' }))).toBe('confirmed');
    expect(tierOf(result({ matchLevel: 'narrative' }))).toBe('likely');
    expect(tierOf(result({ source: 'cluster', settled: false }))).toBe('likely');
    expect(tierOf(result({ matchLevel: 'related' }))).toBe('related');
    expect(tierOf(result({ source: 'report', state: 'under_review', settled: false }))).toBe('reported');
    expect(tierOf(result({ source: 'cluster', state: 'under_review', settled: false }))).toBeNull();
    expect(tierOf(result({ source: 'report', state: 'disputed', settled: false, matchLevel: 'variant' }))).toBe('disputed');
    expect(tierOf(result(undefined, true))).toBe('reported');
    expect(tierOf(result(undefined, false))).toBeNull();
  });
});
