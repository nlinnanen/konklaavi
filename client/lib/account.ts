import type { AccountNote, MeResponse, NoteStatus } from './types';

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

const STATUS: Record<NoteStatus, [label: string, state: string]> = {
  helpful: ['Helpful', 'context'],
  not_helpful: ['Not helpful', 'tracked'],
  controversial: ['Raters disagree', 'controversial'],
  needs_more_ratings: ['Needs ratings', 'needs_ratings'],
};

/** A note's standing as a badge: moderation first, since a held note is not shown at all. */
function noteBadge(note: AccountNote): string {
  if (note.moderation === 'pending_review') return `<span class="badge" data-state="needs_ratings">In review</span>`;
  if (note.moderation === 'hidden') return `<span class="badge" data-state="tracked">Hidden</span>`;
  const [label, state] = STATUS[note.status];
  return `<span class="badge" data-state="${state}">${label}</span>`;
}

/** The user's reputation, ratings and notes with their status; without an account, a call to sign in. */
export function renderAccount(me: MeResponse): string {
  const notes = me.notes
    .map(
      (n) => `<li>
        <div class="row"><b>${escapeHtml(n.title)}</b>${noteBadge(n)}</div>
        <p class="clamp">${escapeHtml(n.body)}</p>
      </li>`,
    )
    .join('');
  const reputation = Math.round(me.reputation * 100);
  const ratings = me.reputationCapped
    ? ''
    : `<div><dt>Ratings</dt><dd>${me.ratings.count}<small>${me.ratings.agreed} matched the outcome</small></dd></div>`;
  return `
    ${me.reputationCapped ? `<p class="who">Sign in to build reputation.</p>` : `<p class="who">${escapeHtml(me.email ?? '')}</p>`}
    <dl class="stats">
      <div><dt>Reputation</dt><dd>${reputation}<span class="meter"><span style="width:${reputation}%"></span></span>${me.reputationCapped ? '<small>Fixed until you sign in</small>' : ''}</dd></div>
      <div><dt>Notes</dt><dd>${me.notes.length}</dd></div>
      ${ratings}
    </dl>
    ${notes ? `<h3>Your notes</h3><ul class="list">${notes}</ul>` : `<p class="muted">Notes you write appear here with their status.</p>`}`;
}
