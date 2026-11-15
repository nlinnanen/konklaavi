import { browser } from 'wxt/browser';
import { escapeHtml, renderAccount } from '@/lib/account';
import { callBackground, type ContentRequest, type PageMatch, type PageStatus } from '@/lib/messages';
import { getSettings, updateSettings, type BackendMode } from '@/lib/settings';
import { HIGHLIGHT_STYLES, isWeak, spreadLine, STATE_LABELS, STATES } from '@/lib/state';
import type { MeResponse, NarrativeState } from '@/lib/types';

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
  context: 'Readers checked it and found no issues. Highlighted only with "Show all highlights" on.',
  controversial: 'Readers split on the notes so far.',
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
    el.innerHTML = `<p class="muted">Konklaavi can't read this page. Reload the tab, or open an article.</p>`;
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

/** The user's standing, with the sign-in form for an install without an account; true when signed in. */
async function refreshAccount(): Promise<boolean> {
  const account = $('#account');
  try {
    const me = await callBackground('me', null);
    showAccount(me);
    return me.verified;
  } catch (e) {
    account.innerHTML = `<p class="muted">Could not load your account: ${escapeHtml(e instanceof Error ? e.message : String(e))}</p>`;
    $('#sign-in').hidden = true;
    $('#sign-out').hidden = true;
    return false;
  }
}

function showAccount(me: MeResponse) {
  $('#account').innerHTML = renderAccount(me);
  $('#sign-in').hidden = me.verified;
  $('#sign-out').hidden = !me.verified;
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
  field('showAll').checked = s.showAll;
  field('showAll').disabled = !s.highlight;
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
      showAll: (form.elements.namedItem('showAll') as HTMLInputElement).checked,
      sendHits: (form.elements.namedItem('sendHits') as HTMLInputElement).checked,
    });
    await loadSettingsForm();
    void refreshAccount();
    setTimeout(refreshPage, 400); // give the content script a moment to look the page up again
  });
  $('#refresh').addEventListener('click', async () => {
    const button = $<HTMLButtonElement>('#refresh');
    button.disabled = true;
    try {
      await callBackground('refreshIndex', null);
      await sendToContent({ type: 'rescan' });
      await refreshPage();
    } catch (e) {
      $('#page').insertAdjacentHTML('beforeend', `<p class="error">${escapeHtml(e instanceof Error ? e.message : String(e))}</p>`);
    } finally {
      button.disabled = false;
    }
  });
  $('#rescan').addEventListener('click', async () => {
    await sendToContent({ type: 'rescan' });
    await refreshPage();
  });
  $('#benchmark').addEventListener('click', () => browser.tabs.create({ url: browser.runtime.getURL('/benchmark.html') }));
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
    const data = new FormData(signIn);
    const credentials = { email: String(data.get('email') ?? '').trim(), password: String(data.get('password') ?? '') };
    const action = (e.submitter as HTMLButtonElement | null)?.value === 'register' ? 'register' : 'login';
    const status = $('#sign-in-status');
    status.textContent = '';
    try {
      showAccount(await callBackground(action, credentials));
      signIn.reset();
    } catch (err) {
      status.textContent = err instanceof Error ? err.message : String(err);
    }
  });
  $('#sign-out').addEventListener('click', async () => {
    try {
      showAccount(await callBackground('logout', null));
    } catch (err) {
      $('#account').insertAdjacentHTML('beforeend', `<p class="error">${escapeHtml(err instanceof Error ? err.message : String(err))}</p>`);
    }
  });
  void refreshAccount();
  await refreshPage();
}

void main();
