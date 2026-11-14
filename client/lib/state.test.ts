import { describe, expect, it } from 'vitest';
import { displayState, featuredNote, leadNote } from './state';
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

describe('featuredNote', () => {
  it('leads with the decided helpful note, else the most-rated note readers can still decide on', () => {
    const rated = (n: Note, helpful: number): Note => ({ ...n, ratings: { ...n.ratings, helpful } });
    const unhelpful = rated(note('a', 'misleading', 'not_helpful'), 9);
    const few = rated(note('b', 'misleading', 'needs_more_ratings'), 1);
    const many = rated(note('c', 'not_misleading', 'controversial'), 3);
    const helpful = note('d', 'misleading', 'helpful');
    expect(featuredNote({ state: 'misleading', notes: [unhelpful, many, helpful] })?.id).toBe('d');
    expect(featuredNote({ state: 'controversial', notes: [unhelpful, few, many] })?.id).toBe('c');
    expect(featuredNote({ state: 'tracked', notes: [unhelpful] })).toBeUndefined();
  });
});
