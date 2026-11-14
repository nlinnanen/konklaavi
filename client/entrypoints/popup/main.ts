import { browser } from 'wxt/browser';
import { escapeHtml, renderAccount } from '@/lib/account';
import { callBackground, type ContentRequest, type PageMatch, type PageStatus } from '@/lib/messages';
import { getSettings, updateSettings, type BackendMode } from '@/lib/settings';
import { HIGHLIGHT_STYLES, isWeak, spreadLine, STATE_LABELS, STATES } from '@/lib/state';
import type { NarrativeState } from '@/lib/types';

const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;

async function sendToContent<T>(req: Omit<ContentRequest, 'target'>): Promise<T | null> {
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  if (tab?.id === undefined) return null;
  try {
    return (await browser.tabs.sendMessage(tab.id, { target: 'content', ...req })) as T;
  } catch {
    return null; // no content script on this tab (e.g. browser pages, or tab opened before install)
  }
}

const STATE_MEANINGS: Record<NarrativeState, string> = {
  misleading: 'A note saying it is misleading was rated helpful.',
  context: 'A note adding context was rated helpful.',
  controversial: 'Raters split on the notes so far.',
  needs_ratings: 'Notes are waiting for ratings.',
  tracked: 'A known narrative with no note shown yet.',
};

function chip(state: NarrativeState): string {
  return `<span class="badge" data-state="${state}">${STATE_LABELS[state]}</span>`;
}

function renderLegend() {
  $('#legend').innerHTML = STATES.map(
    (s) => `<li>
      <span class="sample" style="${HIGHLIGHT_STYLES[s]}">Sample text</span>
      <span><b>${STATE_LABELS[s]}</b><span class="muted">${STATE_MEANINGS[s]}</span></span>
    </li>`,
  ).join('');
}

/** One row per narrative: the strongest of its matches decides whether it reads as a copy or a reworded passage. */
function renderPage(status: PageStatus | null) {
  const el = $('#page');
  if (!status) {
    el.innerHTML = `<p class="muted">HashTrust can't read this page. Reload the tab, or open an article.</p>`;
    return;
  }
  const byNarrative = new Map<string, PageMatch[]>();
  for (const m of status.matches) byNarrative.set(m.narrative.narrativeId, [...(byNarrative.get(m.narrative.narrativeId) ?? []), m]);
  const rows = [...byNarrative.values()]
    .sort((a, b) => STATES.indexOf(a[0]!.narrative.state) - STATES.indexOf(b[0]!.narrative.state))
    .map((matches) => {
      const { narrative } = matches[0]!;
      const copies = matches.filter((m) => !isWeak(m.matchLevel)).length;
      const how = copies ? `${copies} passage${copies === 1 ? '' : 's'}` : 'Reworded or related';
      return `<li>
        <div class="row"><b>${escapeHtml(narrative.title)}</b>${chip(narrative.state)}</div>
        <p class="muted">${how} · ${escapeHtml(spreadLine(narrative.spread))}</p>
      </li>`;
    })
    .join('');
  const scanned = `${status.scannedSentences} sentence${status.scannedSentences === 1 ? '' : 's'} checked`;
  el.innerHTML = rows
    ? `<ul class="list">${rows}</ul><p class="muted">${scanned}. Click a highlight to read and rate its notes.</p>`
    : `<p class="muted">No tracked narrative here (${scanned}). Select a passage to add context.</p>`;
  if (!status.highlightSupported) el.insertAdjacentHTML('beforeend', `<p class="muted">This browser can't highlight passages.</p>`);
}

/** The account, or the sign-in form for an install that is not verified; true when verified. */
async function refreshAccount(): Promise<boolean> {
  const account = $('#account');
  try {
    const me = await callBackground('me', null);
    account.innerHTML = me.verified ? renderAccount(me) : '';
    $('#sign-in').hidden = me.verified;
    return me.verified;
  } catch (e) {
    account.innerHTML = `<p class="muted">Could not load your account: ${escapeHtml(e instanceof Error ? e.message : String(e))}</p>`;
    $('#sign-in').hidden = true;
    return false;
  }
}

/** Checks the account every few seconds for two minutes, so the popup notices the clicked sign-in link. */
function awaitSignIn() {
  const started = Date.now();
  const timer = setInterval(async () => {
    if ((await refreshAccount()) || Date.now() - started > 120_000) clearInterval(timer);
  }, 3000);
}

const MODE_BADGES: Record<BackendMode, string> = {
  mock: 'Mock',
  remote: 'Remote',
  local: 'Local index',
};

async function loadSettingsForm() {
  const s = await getSettings();
  const form = $<HTMLFormElement>('#settings');
  const field = (name: string) => form.elements.namedItem(name) as HTMLInputElement;
  form.querySelector<HTMLInputElement>(`input[name="backendMode"][value="${s.backendMode}"]`)!.checked = true;
  field('backendUrl').value = s.backendUrl;
  field('backendUrl').disabled = s.backendMode === 'mock';
  field('highlight').checked = s.highlight;
  field('sendHits').checked = s.sendHits;
  field('sendHits').disabled = s.backendMode !== 'local';
  $('#clear-mock').hidden = s.backendMode !== 'mock';
  $('#mode-badge').textContent = MODE_BADGES[s.backendMode];
}

async function refreshPage() {
  renderPage(await sendToContent<PageStatus>({ type: 'pageStatus' }));
}

async function main() {
  renderLegend();
  await loadSettingsForm();
  const form = $<HTMLFormElement>('#settings');
  form.addEventListener('change', async () => {
    const data = new FormData(form);
    const mode = data.get('backendMode');
    await updateSettings({
      backendMode: mode === 'remote' || mode === 'local' ? mode : 'mock',
      backendUrl: (form.elements.namedItem('backendUrl') as HTMLInputElement).value,
      highlight: data.has('highlight'),
      sendHits: (form.elements.namedItem('sendHits') as HTMLInputElement).checked,
    });
    await loadSettingsForm();
    void refreshAccount();
    setTimeout(refreshPage, 400); // give the content script a moment to look the page up again
  });
  $('#rescan').addEventListener('click', async () => {
    await sendToContent({ type: 'rescan' });
    await refreshPage();
  });
  $('#clear-mock').addEventListener('click', () => ($('#clear-confirm').hidden = false));
  $('#clear-no').addEventListener('click', () => ($('#clear-confirm').hidden = true));
  $('#clear-yes').addEventListener('click', async () => {
    $('#clear-confirm').hidden = true;
    await callBackground('clearMock', null);
    await sendToContent({ type: 'rescan' });
    await Promise.all([refreshPage(), refreshAccount()]);
  });
  const signIn = $<HTMLFormElement>('#sign-in');
  signIn.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = String(new FormData(signIn).get('email') ?? '').trim();
    const status = $('#sign-in-status');
    try {
      const signedIn = await callBackground('requestMagicLink', email);
      status.textContent = signedIn ? `Signed in as ${email}.` : `Check ${email} for your sign-in link.`;
      awaitSignIn();
    } catch (err) {
      status.textContent = `Could not send the link: ${err instanceof Error ? err.message : String(err)}`;
    }
  });
  void refreshAccount();
  await refreshPage();
}

void main();
