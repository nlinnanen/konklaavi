import { describe, expect, it } from 'vitest';
import { displayState, leadNote } from './state';
import type { Classification, Note, NoteStatus } from './types';

const note = (id: string, classification: Classification, status: NoteStatus): Note => ({
  id,
  classification,
  status,
  body: '',
  sources: [],
  ratings: { helpful: 0, somewhat: 0, not_helpful: 0 },
  createdAt: '2026-11-14T00:00:00Z',
});

describe('displayState', () => {
  it('shows a narrative as misleading when a misleading note is helpful, even beside helpful context', () => {
    const notes = [note('a', 'not_misleading', 'helpful'), note('b', 'misleading', 'helpful')];
    expect(displayState(notes)).toBe('misleading');
    expect(leadNote({ state: 'misleading', notes })?.id).toBe('b');
  });

  it('gives context when only a not-misleading note is helpful', () => {
    const notes = [note('a', 'misleading', 'controversial'), note('b', 'not_misleading', 'helpful')];
    expect(displayState(notes)).toBe('context');
    expect(leadNote({ state: 'context', notes })?.id).toBe('b');
  });

  it('prefers controversial over undecided notes, and undecided over notes rated not helpful', () => {
    expect(displayState([note('a', 'misleading', 'needs_more_ratings'), note('b', 'misleading', 'controversial')])).toBe(
      'controversial',
    );
    expect(displayState([note('a', 'misleading', 'not_helpful'), note('b', 'misleading', 'needs_more_ratings')])).toBe(
      'needs_ratings',
    );
  });

  it('only tracks a narrative with no notes or only unhelpful ones', () => {
    expect(displayState([])).toBe('tracked');
    expect(displayState([note('a', 'misleading', 'not_helpful')])).toBe('tracked');
    expect(leadNote({ state: 'tracked', notes: [] })).toBeUndefined();
  });
});
