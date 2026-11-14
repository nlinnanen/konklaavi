import { NEGATIVE_VERDICTS, VERDICTS, type LookupResult, type Verdict, type VerdictCounts } from './types';

/** Share of negative votes needed to flag content. */
export const FLAG_THRESHOLD = 0.5;

export function emptyCounts(): VerdictCounts {
  return { misinformation: 0, manipulative: 0, misleading: 0, trustworthy: 0 };
}

function argmax(counts: VerdictCounts, among: readonly Verdict[]): Verdict {
  let best = among[0]!;
  for (const v of among) if (counts[v] > counts[best]) best = v;
  return best;
}

export function summarize(hash: string, counts: VerdictCounts, minVotes: number): LookupResult {
  const total = VERDICTS.reduce((n, v) => n + counts[v], 0);
  const negative = NEGATIVE_VERDICTS.reduce((n, v) => n + counts[v], 0);
  const flagged = total >= minVotes && total > 0 && negative / total >= FLAG_THRESHOLD;
  return {
    hash,
    counts,
    total,
    flagged,
    topVerdict: flagged ? argmax(counts, NEGATIVE_VERDICTS) : argmax(counts, VERDICTS),
  };
}
