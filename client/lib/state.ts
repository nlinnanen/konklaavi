import type { MatchLevel, Narrative, NarrativeState, Note } from './types';

/** Display states, most urgent first: the order of the legend and of the tie-break between matches. */
export const STATES = ['misleading', 'controversial', 'context', 'needs_ratings', 'tracked'] as const satisfies readonly NarrativeState[];

export const STATE_LABELS: Record<NarrativeState, string> = {
  misleading: 'Misleading',
  context: 'Context added',
  controversial: 'Raters disagree',
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

/** The helpful note a misleading or context narrative is shown with. */
export function leadNote(narrative: Pick<Narrative, 'state' | 'notes'>): Note | undefined {
  const classification = { misleading: 'misleading', context: 'not_misleading' }[narrative.state as string];
  if (!classification) return undefined;
  return narrative.notes.find((n) => n.status === 'helpful' && n.classification === classification);
}

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

const dateFormat = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' });

/** "Seen on 3 sites since 4 Nov 2026", the narrative's spread. */
export function spreadLine(spread: Narrative['spread']): string {
  const sites = `${spread.sites} site${spread.sites === 1 ? '' : 's'}`;
  if (!spread.firstSeen) return `Tracked on ${sites}`;
  return `Seen on ${sites} since ${dateFormat.format(new Date(spread.firstSeen))}`;
}
