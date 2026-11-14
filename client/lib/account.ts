import { SOURCE_LABELS, sourceHref } from './messages';
import type { AccountReport, MeResponse } from './types';

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/** A report's outcome as HTML: who confirmed it, linking to their write-up, or Pending / Rejected. */
export function renderOutcome(report: AccountReport): string {
  if (report.outcome === 'pending') return `<span class="outcome pending">Pending</span>`;
  if (report.outcome === 'rejected') return `<span class="outcome rejected">Rejected</span>`;
  const by = report.settledBy;
  if (!by) return `<span class="outcome confirmed">Confirmed</span>`;
  const label = `Confirmed by ${escapeHtml(SOURCE_LABELS[by.source])}: ${escapeHtml(by.title)}`;
  const href = sourceHref(by);
  const text = href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${label}</a>` : label;
  return `<span class="outcome confirmed">${text}</span>`;
}

/** The signed-in account: reputation, agreement counts and the user's reports with their outcomes. */
export function renderAccount(me: MeResponse): string {
  const reports = me.reports
    .map((r) => `<li><b>${escapeHtml(r.title)}</b><div>${renderOutcome(r)}</div></li>`)
    .join('');
  return `
    <p>${me.email ? `Signed in as <b>${escapeHtml(me.email)}</b>` : `<span class="muted">Not signed in</span>`}</p>
    <div class="stat">
      <div><b>${Math.round(me.reputation * 100)}%</b><span class="muted">reputation</span></div>
      <div><b>${me.weight.toFixed(2)}×</b><span class="muted">vote weight</span></div>
      <div><b>${me.agreed}</b><span class="muted">agreed</span></div>
      <div><b>${me.disagreed}</b><span class="muted">disagreed</span></div>
    </div>
    ${reports ? `<ul class="flagged reports">${reports}</ul>` : `<p class="muted">No reports yet.</p>`}`;
}
