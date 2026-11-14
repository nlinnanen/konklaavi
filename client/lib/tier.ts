import type { LookupResult } from './types';

/** How a match is shown, strongest first. */
export const TIERS = ['confirmed', 'likely', 'reported', 'related', 'disputed'] as const;
export type Tier = (typeof TIERS)[number];

export const TIER_LABELS: Record<Tier, string> = {
  confirmed: 'Confirmed',
  likely: 'Likely',
  reported: 'Reported',
  related: 'Related',
  disputed: 'Disputed',
};

/** CSS declarations of a tier's highlight, also used for the popup legend. */
export const TIER_STYLES: Record<Tier, string> = {
  confirmed: 'background-color: rgba(220, 38, 38, 0.45);',
  likely: 'background-color: rgba(249, 115, 22, 0.3);',
  related: 'text-decoration: underline dotted rgba(234, 179, 8, 0.9); text-decoration-thickness: 2px;',
  reported: 'background-color: rgba(168, 85, 247, 0.15);',
  disputed: 'background-color: rgba(156, 163, 175, 0.25);',
};

/**
 * The tier of a lookup result, or null when it is not shown. A result flagged
 * by votes alone counts as reported.
 */
export function tierOf(result: LookupResult): Tier | null {
  const signature = result.signature;
  if (!signature) return result.flagged ? 'reported' : null;
  if (signature.state === 'disputed') return 'disputed';
  if (signature.state === 'under_review') return signature.source === 'report' || result.flagged ? 'reported' : null;
  switch (signature.matchLevel) {
    case 'related':
      return 'related';
    case 'narrative':
      return 'likely';
    default:
      return signature.settled ? 'confirmed' : 'likely';
  }
}

/** Higher is stronger; -1 for no tier. */
export function tierRank(tier: Tier | null): number {
  return tier ? TIERS.length - TIERS.indexOf(tier) : -1;
}
