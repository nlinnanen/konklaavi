import { browser } from 'wxt/browser';
import { callBackground, sourceHref, SOURCE_LABELS, type ContentRequest, type FlaggedItem, type PageStatus } from '@/lib/messages';
import { getSettings, updateSettings } from '@/lib/settings';
import { VERDICTS, VERDICT_LABELS, type DomainStats, type Settings } from '@/lib/types';

const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

async function activeTabId(): Promise<number | undefined> {
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  return tab?.id;
}

async function sendToContent<T>(req: Omit<ContentRequest, 'target'>): Promise<T | null> {
  const id = await activeTabId();
  if (id === undefined) return null;
  try {
    return (await browser.tabs.sendMessage(id, { target: 'content', ...req })) as T;
  } catch {
    return null; // no content script on this tab (e.g. browser pages, or tab opened before install)
  }
}

function renderFlagged(f: FlaggedItem): string {
  const text = escapeHtml(f.text.length > 160 ? f.text.slice(0, 160) + '…' : f.text);
  const votes = f.total ? ` <span class="muted">(${f.total})</span>` : '';
  let narrative = '';
  if (f.signature) {
    const href = sourceHref(f.signature);
    const label = escapeHtml(SOURCE_LABELS[f.signature.source]);
    const source = href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${label}</a>` : label;
    narrative = `<div class="narrative"><b>${escapeHtml(f.signature.title)}</b> <span class="muted">· ${source}</span></div>`;
  }
  return `<li><span class="chip ${f.verdict}">${VERDICT_LABELS[f.verdict]}</span>${text}${votes}${narrative}</li>`;
}

function renderPage(status: PageStatus | null) {
  const el = $('#page');
  if (!status) {
    el.innerHTML = `<p class="muted">Not available on this page. Try reloading the tab.</p>`;
    return;
  }
  const items = status.flagged.map(renderFlagged).join('');
  el.innerHTML = `
    <div class="stat">
      <div><b>${status.flagged.length}</b><span class="muted">flagged</span></div>
      <div><b>${status.scannedSentences}</b><span class="muted">sentences checked</span></div>
    </div>
    ${items ? `<ul class="flagged">${items}</ul>` : `<p class="muted">Nothing on this page has been flagged.</p>`}
    ${status.highlightSupported ? '' : `<p class="muted">This browser doesn't support in-page highlighting.</p>`}
    <p class="muted">Select text on the page to rate it.</p>`;
}

function renderDomain(stats: DomainStats | null, error?: string) {
  const el = $('#domain');
  if (error) {
    el.innerHTML = `<p class="muted">Could not load domain stats: ${escapeHtml(error)}</p>`;
    return;
  }
  if (!stats || stats.votesCount === 0) {
    el.innerHTML = `<p class="muted">No votes for this domain yet.</p>`;
    return;
  }
  const segments = VERDICTS.filter((v) => stats.counts[v])
    .map((v) => `<span style="width:${(stats.counts[v] / stats.votesCount) * 100}%;background:var(--${v})" title="${VERDICT_LABELS[v]}"></span>`)
    .join('');
  const legend = VERDICTS.filter((v) => stats.counts[v]).map((v) => `<span>${VERDICT_LABELS[v]}: ${stats.counts[v]}</span>`).join('');
  el.innerHTML = `
    <div class="stat">
      <div><b>${stats.flaggedCount}</b><span class="muted">flagged items</span></div>
      <div><b>${stats.votesCount}</b><span class="muted">votes</span></div>
    </div>
    <div class="bar">${segments}</div>
    <div class="legend">${legend}</div>`;
}

async function loadSettingsForm(): Promise<Settings> {
  const s = await getSettings();
  const form = $('#settings') as HTMLFormElement;
  (form.querySelector(`input[name="backendMode"][value="${s.backendMode}"]`) as HTMLInputElement).checked = true;
  (form.elements.namedItem('backendUrl') as HTMLInputElement).value = s.backendUrl;
  (form.elements.namedItem('sendExcerpt') as HTMLInputElement).checked = s.sendExcerpt;
  (form.elements.namedItem('highlight') as HTMLInputElement).checked = s.highlight;
  $('#mode-badge').textContent = s.backendMode === 'mock' ? 'mock backend' : 'remote backend';
  (form.elements.namedItem('backendUrl') as HTMLInputElement).disabled = s.backendMode !== 'remote';
  return s;
}

async function refresh() {
  const status = await sendToContent<PageStatus>({ type: 'pageStatus' });
  renderPage(status);
  if (!status) {
    renderDomain(null);
    return;
  }
  $('#domain-name').textContent = status.domain;
  try {
    renderDomain(await callBackground('domainStats', status.domain));
  } catch (e) {
    renderDomain(null, e instanceof Error ? e.message : String(e));
  }
}

async function main() {
  await loadSettingsForm();
  const form = $('#settings') as HTMLFormElement;
  form.addEventListener('change', async () => {
    const data = new FormData(form);
    await updateSettings({
      backendMode: data.get('backendMode') === 'remote' ? 'remote' : 'mock',
      backendUrl: String((form.elements.namedItem('backendUrl') as HTMLInputElement).value || ''),
      sendExcerpt: data.has('sendExcerpt'),
      highlight: data.has('highlight'),
    });
    await loadSettingsForm();
    setTimeout(refresh, 300); // give the content script a moment to rescan
  });
  $('#rescan').addEventListener('click', async () => {
    await sendToContent({ type: 'rescan' });
    await refresh();
  });
  $('#clear-mock').addEventListener('click', async () => {
    if (!confirm('Delete all locally stored mock votes?')) return;
    await callBackground('clearMock', null);
    await sendToContent({ type: 'rescan' });
    await refresh();
  });
  await refresh();
}

void main();
