import { describe, expect, it } from 'vitest';
import { displayState, headline, leadNote, truncate } from './state';
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
  });

  it('gives context when only a not-misleading note is helpful', () => {
    const notes = [note('a', 'misleading', 'controversial'), note('b', 'not_misleading', 'helpful')];
    expect(displayState(notes)).toBe('context');
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
  });
});

describe('headline', () => {
  it('names what readers concluded without a double negative, and the spread of an unannotated narrative', () => {
    const spread = { sites: 12, firstSeen: '2026-11-03T12:00:00Z' };
    const now = new Date('2026-11-15T00:00:00Z');
    expect(headline({ state: 'misleading', spread }, now)).toBe('Readers flagged this as misleading');
    expect(headline({ state: 'context', spread }, now)).toBe('Readers checked this: no issues found');
    expect(headline({ state: 'controversial', spread }, now)).toBe('Readers disagree about this claim');
    expect(headline({ state: 'needs_ratings', spread }, now)).toBe('Readers are reviewing this — help rate it');
    expect(headline({ state: 'tracked', spread }, now)).toBe('This text appears on 12 sites since 3 Nov');
  });
});

describe('leadNote', () => {
  it('leads with the helpful note, else the most-rated note the reader has not rated, else the most-rated', () => {
    const rated = (n: Note, helpful: number): Note => ({ ...n, ratings: { ...n.ratings, helpful } });
    const unhelpful = rated(note('a', 'misleading', 'not_helpful'), 9);
    const few = rated(note('b', 'misleading', 'needs_more_ratings'), 1);
    const many = rated(note('c', 'not_misleading', 'controversial'), 3);
    const helpful = note('d', 'misleading', 'helpful');
    expect(leadNote({ notes: [unhelpful, many, helpful] }, { d: 'helpful' })?.id).toBe('d');
    expect(leadNote({ notes: [unhelpful, few, many] })?.id).toBe('c');
    expect(leadNote({ notes: [unhelpful, few, many] }, { c: 'helpful' })?.id).toBe('b');
    expect(leadNote({ notes: [few, many] }, { b: 'helpful', c: 'somewhat' })?.id).toBe('c');
    expect(leadNote({ notes: [unhelpful] })).toBeUndefined();
  });
});

describe('truncate', () => {
  it('cuts long text at a word boundary within the limit and marks the cut', () => {
    const text = 'The claim leaves out that the rule applies only to commercial farms, not to private gardens.';
    const cut = truncate(text, 40);
    expect(cut).toBe('The claim leaves out that the rule…');
    expect(cut.length).toBeLessThanOrEqual(40);
    expect(truncate('Short note.', 40)).toBe('Short note.');
  });
});
