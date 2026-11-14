import { describe, expect, it } from 'vitest';
import { renderOutcome } from './account';
import type { MeResponse } from './types';

const me: MeResponse = {
  userId: 'u1',
  email: 'a@example.com',
  reputation: 0.8,
  weight: 1.5,
  agreed: 4,
  disagreed: 1,
  reports: [
    {
      narrativeId: 'n1',
      title: 'Report one',
      state: 'active',
      outcome: 'confirmed',
      settledBy: { source: 'euvsdisinfo', title: 'Ukraine <staged> attack', sourceUrl: 'https://euvsdisinfo.eu/report/x' },
      reportedAt: '2026-11-01T00:00:00Z',
      settledAt: '2026-11-02T00:00:00Z',
    },
    {
      narrativeId: 'n2',
      title: 'Report two',
      state: 'under_review',
      outcome: 'pending',
      settledBy: null,
      reportedAt: '2026-11-03T00:00:00Z',
      settledAt: null,
    },
    {
      narrativeId: 'n3',
      title: 'Report three',
      state: 'retired',
      outcome: 'rejected',
      settledBy: null,
      reportedAt: '2026-11-03T00:00:00Z',
      settledAt: '2026-11-04T00:00:00Z',
    },
  ],
};

describe('renderOutcome', () => {
  it('names and links the confirming source, and labels pending and rejected reports', () => {
    const [confirmed, pending, rejected] = me.reports.map(renderOutcome);
    expect(confirmed).toContain('href="https://euvsdisinfo.eu/report/x"');
    expect(confirmed).toContain('Confirmed by EUvsDisinfo: Ukraine &#60;staged&#62; attack');
    expect(pending).toContain('Pending');
    expect(rejected).toContain('Rejected');
  });
});
