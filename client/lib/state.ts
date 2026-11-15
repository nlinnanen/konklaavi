import type { Classification, MatchLevel, Narrative, NarrativeState, Note } from './types';

/** Display states, most urgent first: the order of the legend and of the tie-break between matches. */
export const STATES = ['misleading', 'controversial', 'context', 'needs_ratings', 'tracked'] as const satisfies readonly NarrativeState[];

export const STATE_LABELS: Record<NarrativeState, string> = {
  misleading: 'Misleading',
  context: 'No issues found',
  controversial: 'Readers disagree',
  needs_ratings: 'Needs ratings',
  tracked: 'Tracked',
};

/**
 * A narrative's display state from its visible notes: a helpful misleading
 * note makes it misleading, else a helpful not-misleading note gives it
 * context, else a controversial note makes it controversial; notes still
 * waiting for ratings leave it needing ratings, and with none of those it is
 * only tracked.
 */
export function displayState(notes: Pick<Note, 'classification' | 'status'>[]): NarrativeState {
  const helpful = notes.filter((n) => n.status === 'helpful');
  if (helpful.some((n) => n.classification === 'misleading')) return 'misleading';
  if (helpful.length) return 'context';
  if (notes.some((n) => n.status === 'controversial')) return 'controversial';
  if (notes.some((n) => n.status === 'needs_more_ratings')) return 'needs_ratings';
  return 'tracked';
}

const ratingCount = (n: Pick<Note, 'ratings'>) => n.ratings.helpful + n.ratings.somewhat + n.ratings.not_helpful;

const mostRated = (notes: Note[]) =>
  notes.reduce<Note | undefined>((best, n) => (!best || ratingCount(n) > ratingCount(best) ? n : best), undefined);

/** Notes readers rated not helpful are counted but not shown. */
export const shownNotes = (notes: Note[]) => notes.filter((n) => n.status !== 'not_helpful');

/**
 * The note a highlight leads with: the helpful note, a misleading one first as
 * the display state puts it; else the note the reader hasn't rated that is
 * closest to being decided, by its ratings; else the most-rated note.
 */
export function leadNote(narrative: Pick<Narrative, 'notes'>, myRatings: Record<string, unknown> = {}): Note | undefined {
  const shown = shownNotes(narrative.notes);
  const helpful = shown.filter((n) => n.status === 'helpful');
  if (helpful.length) return helpful.find((n) => n.classification === 'misleading') ?? helpful[0];
  return mostRated(shown.filter((n) => !(n.id in myRatings))) ?? mostRated(shown);
}

/** The best-rated shown note of each classification, for the two sides of a disputed claim. */
export function bothSides(notes: Note[]): Partial<Record<Classification, Note>> {
  const shown = shownNotes(notes);
  return {
    misleading: mostRated(shown.filter((n) => n.classification === 'misleading')),
    not_misleading: mostRated(shown.filter((n) => n.classification === 'not_misleading')),
  };
}

/** Narratives readers found accurate are highlighted only when the reader asks to see all. */
export const isHighlighted = (state: NarrativeState, showAll: boolean) => showAll || state !== 'context';

const dayMonth = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' });
const dayMonthYear = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });

/** "3 Nov", with the year when it isn't this year. */
function shortDate(iso: string, now = new Date()): string {
  const date = new Date(iso);
  return (date.getFullYear() === now.getFullYear() ? dayMonth : dayMonthYear).format(date);
}

const sitesText = (sites: number) => `${sites} site${sites === 1 ? '' : 's'}`;

/** "On 3 sites since 4 Nov", the narrative's spread. */
export function spreadLine(spread: Narrative['spread'], now = new Date()): string {
  const since = spread.firstSeen ? ` since ${shortDate(spread.firstSeen, now)}` : '';
  return `On ${sitesText(spread.sites)}${since}`;
}

/** One plain sentence on what readers concluded about a narrative, coloured by its state. */
export function headline(narrative: Pick<Narrative, 'state' | 'spread'>, now = new Date()): string {
  switch (narrative.state) {
    case 'misleading':
      return 'Readers flagged this as misleading';
    case 'context':
      return 'Readers checked this: no issues found';
    case 'controversial':
      return 'Readers disagree about this claim';
    case 'needs_ratings':
      return 'Readers are reviewing this — help rate it';
    case 'tracked': {
      const { sites, firstSeen } = narrative.spread;
      return `This text appears on ${sitesText(sites)}${firstSeen ? ` since ${shortDate(firstSeen, now)}` : ''}`;
    }
  }
}

/** Why a note is trusted: who rated it helpful, how many ratings and sources it has. */
export function trustLine(note: Pick<Note, 'status' | 'decidedBy' | 'ratings' | 'sources'>): string {
  const parts: string[] = [];
  if (note.status === 'helpful') {
    parts.push(note.decidedBy === 'agreement' ? 'Rated helpful by agreement' : 'Rated helpful by people who usually disagree');
  }
  const total = ratingCount(note);
  if (total) parts.push(`${total} rating${total === 1 ? '' : 's'}`);
  if (note.sources.length) parts.push(`${note.sources.length} source${note.sources.length === 1 ? '' : 's'}`);
  return parts.join(' · ');
}

/** The text cut at a word boundary to at most `max` characters, with an ellipsis when cut. */
export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max - 1);
  const space = cut.lastIndexOf(' ');
  return (space > max * 0.6 ? cut.slice(0, space) : cut).replace(/[\s.,;:!?-]+$/, '') + '…';
}

/** A note in at most `max` characters: its summary, else its body on one line, cut short. */
export const noteSnippet = (note: Pick<Note, 'body' | 'summary'>, max: number) =>
  note.summary || truncate(note.body.replace(/\s+/g, ' ').trim(), max);

/** Narrative-level and related matches are drawn lighter than exact and variant ones. */
export function isWeak(level: MatchLevel): boolean {
  return level === 'narrative' || level === 'related';
}

const LEVEL_RANK: Record<MatchLevel, number> = { exact: 3, variant: 3, narrative: 2, related: 1 };

/** Higher is the stronger match: the match level first, then the more urgent state. */
export function matchRank(level: MatchLevel, state: NarrativeState): number {
  return LEVEL_RANK[level] * 10 + (STATES.length - STATES.indexOf(state));
}

/** A highlight's style: its state, drawn weak or strong. */
export type HighlightStyle = `${NarrativeState}${'' | '-weak'}`;

export const highlightStyle = (state: NarrativeState, level: MatchLevel): HighlightStyle =>
  isWeak(level) ? `${state}-weak` : state;

/**
 * CSS declarations of each highlight, also used for the popup legend.
 * Translucent fills and underlines read on light and dark pages alike.
 */
export const HIGHLIGHT_STYLES: Record<HighlightStyle, string> = {
  misleading:
    'background-color: oklch(0.68 0.19 35 / 0.36); text-decoration: underline solid oklch(0.6 0.2 32); text-decoration-thickness: 2px;',
  'misleading-weak':
    'background-color: oklch(0.68 0.19 35 / 0.14); text-decoration: underline dotted oklch(0.6 0.2 32 / 0.8); text-decoration-thickness: 2px;',
  context:
    'background-color: oklch(0.74 0.1 200 / 0.24); text-decoration: underline solid oklch(0.6 0.11 205); text-decoration-thickness: 2px;',
  'context-weak':
    'background-color: oklch(0.74 0.1 200 / 0.1); text-decoration: underline dotted oklch(0.6 0.11 205 / 0.8); text-decoration-thickness: 2px;',
  controversial: 'background-color: oklch(0.6 0.015 260 / 0.26);',
  'controversial-weak': 'background-color: oklch(0.6 0.015 260 / 0.12);',
  needs_ratings: 'background-color: oklch(0.82 0.09 85 / 0.2);',
  'needs_ratings-weak': 'background-color: oklch(0.82 0.09 85 / 0.1);',
  tracked:
    'text-decoration: underline dotted oklch(0.58 0.03 260 / 0.75); text-decoration-thickness: 1.5px;',
  'tracked-weak':
    'text-decoration: underline dotted oklch(0.58 0.03 260 / 0.4); text-decoration-thickness: 1px;',
};
