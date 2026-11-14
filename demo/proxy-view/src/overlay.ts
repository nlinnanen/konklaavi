/**
 * The proxy view's in-page script: the browser extension's highlights, cards, note form and sign-in,
 * on a proxied page. It reuses the extension's own modules (client/entrypoints/content and client/lib)
 * and replaces its background worker with calls to the API through the proxy's /api/.
 *
 * Matching runs on the server: the page's text blocks go to /check, and each match is located in the
 * page and highlighted. Built into overlay.bundle.js by build.mjs.
 */
import { Highlighter, type FlaggedRange } from '@/entrypoints/content/highlighter';
import { blockFor, buildModel, offsetsOf, textNodesUnder } from '@/entrypoints/content/textModel';
import { OverlayUi, type CardActions } from '@/entrypoints/content/ui';
import { fingerprintSentences, splitSentences, type Sentence } from '@/lib/fingerprint';
import { HIGHLIGHT_STYLES, highlightStyle, matchRank, STATE_LABELS, STATES } from '@/lib/state';
import type { Helpfulness, LookupResult, Narrative, NarrativeState } from '@/lib/types';
import { guideCss, panelCss } from './panel';

interface Config {
  url: string;
  host: string;
  blocks: string[];
  mode: string;
  test: boolean;
  proxy: string;
  readme: string;
}

interface CheckMatch {
  text: string;
  result: LookupResult;
}

const cfg: Config = JSON.parse(document.getElementById('hashtrust-data')!.textContent!);

// ---- API through the proxy, as the extension's background worker would call it.

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function api<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(cfg.proxy + 'api/' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : data.error || `HTTP ${res.status}`;
    throw new ApiError(detail, res.status);
  }
  return data as T;
}

/** Local storage, which a private window or blocked site data can refuse. */
const store = {
  get<T>(key: string, fallback: T): T {
    try {
      const raw = localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : fallback;
    } catch {
      return fallback;
    }
  },
  set(key: string, value: unknown) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* this page view only */
    }
  },
};

/** This browser's install id, as the extension keeps one per install. */
const userId: string = (() => {
  let id = store.get<string | null>('hashtrust.userId', null);
  if (!id) {
    id = crypto.randomUUID();
    store.set('hashtrust.userId', id);
  }
  return id;
})();

// Notes and ratings need no account, as in the extension; this browser's install id writes them.
let myRatings = store.get<Record<string, Helpfulness>>('hashtrust.myRatings', {});

// ---- The side panel, beside the frame in the /view shell (same origin), else on the page itself.

const shell = window !== window.top ? window.top!.document : null;
const doc = shell ?? document;
doc.getElementById('ht-host')?.remove();
doc.getElementById('ht-loading')?.remove();
if (shell) shell.title = `${document.title || cfg.host} · HashTrust`;
const panelHost = doc.createElement('div');
panelHost.id = 'ht-host';
panelHost.style.cssText = 'all:initial;position:fixed;top:0;right:0;bottom:0;width:340px;z-index:2147483647;';
doc.body.append(panelHost);
const root = panelHost.attachShadow({ mode: 'open' });
root.innerHTML = `<style>${panelCss}</style>
<div class="panel">
  <header>
    <div class="brand"><b>HashTrust</b><span>demo</span><button class="toggle" title="Collapse panel">»</button></div>
    <form target="_top"><input name="url" type="url" required aria-label="Article URL"><button class="btn primary">Scan</button></form>
    <div class="status"></div>
  </header>
  <div class="list"></div>
  <footer>
    <p class="demo-note">A demo: this page was rendered again on our server, so it may not look or work exactly like the
    original, and some sites don't work. The real product is the browser extension.
    <a class="readme" target="_blank" rel="noopener">How to install it</a></p>
    <p>Select a passage on the page to add a note. Click a highlight to rate its notes.
    <button class="link" data-act="guide">How it works</button></p>
  </footer>
</div>`;
const $ = <T extends Element>(s: string) => root.querySelector(s) as T;
$<HTMLInputElement>('input').value = cfg.url;
$<HTMLFormElement>('form').action = cfg.proxy + 'view';
$<HTMLAnchorElement>('.readme').href = cfg.readme;
$<HTMLButtonElement>('[data-act=guide]').addEventListener('click', () => showGuide());
$<HTMLButtonElement>('.toggle').addEventListener('click', () => {
  const collapsed = panelHost.classList.toggle('collapsed');
  panelHost.style.width = collapsed ? '44px' : '340px';
  doc.body.classList.toggle('ht-collapsed', collapsed);
  $<HTMLButtonElement>('.toggle').textContent = collapsed ? '«' : '»';
});
const statusEl = $<HTMLDivElement>('.status');
const listEl = $<HTMLDivElement>('.list');
const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
const clip = (t: string, max: number) => (t.length > max ? t.slice(0, max) + '…' : t);
const spinner = '<span class="spinner" aria-hidden="true"></span>';

// ---- Locating matches: whitespace is ignored, since extracted text adds spaces at line breaks and
// block edges that the DOM may not have.

function textIndex() {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) =>
      n.parentElement && !n.parentElement.closest('style,script,noscript,template,textarea,hashtrust-ui')
        ? NodeFilter.FILTER_ACCEPT
        : NodeFilter.FILTER_REJECT,
  });
  let flat = '';
  const map: [Text, number][] = [];
  for (let n: Node | null; (n = walker.nextNode()); ) {
    const t = (n as Text).nodeValue!;
    for (let i = 0; i < t.length; i++) {
      if (/\s/.test(t[i])) continue;
      flat += t[i];
      map.push([n as Text, i]);
    }
  }
  return { flat, folded: fold(flat.toLowerCase()), map };
}
const fold = (s: string) => s.replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/[–—]/g, '-');

let index: ReturnType<typeof textIndex> | null = null;

function locate(text: string): Range | null {
  index ??= textIndex();
  const needle = text.replace(/\s+/g, '');
  let at = index.flat.indexOf(needle);
  if (at < 0) at = index.folded.indexOf(fold(needle.toLowerCase()));
  if (at < 0 || !needle) return null;
  const [sn, so] = index.map[at];
  const [en, eo] = index.map[at + needle.length - 1];
  const range = document.createRange();
  range.setStart(sn, so);
  range.setEnd(en, eo + 1);
  return range;
}

const overlaps = (a: Range, b: Range) =>
  a.compareBoundaryPoints(Range.START_TO_END, b) > 0 && a.compareBoundaryPoints(Range.END_TO_START, b) < 0;

// ---- Highlights and the side panel list.

const highlighter = new Highlighter();
const ui = new OverlayUi();
const flagged: FlaggedRange[] = [];
const groups = new Map<string, { narrative: Narrative; best: FlaggedRange; items: FlaggedRange[]; missing: number }>();

function place(matches: CheckMatch[]) {
  // Strongest first, so where matches overlap the stronger one is highlighted.
  const rank = (m: CheckMatch) => matchRank(m.result.matchLevel, m.result.narrative.state) * 10 + (m.result.similarity ?? 1);
  matches = matches.filter((m) => m.result?.narrative).sort((a, b) => rank(b) - rank(a));
  const seen = new Set<string>();
  const byBlock = new Map<Element, FlaggedRange[]>();
  for (const m of matches) {
    if (seen.has(m.text)) continue;
    seen.add(m.text);
    const n = m.result.narrative;
    const group = groups.get(n.narrativeId) ?? { narrative: n, best: undefined!, items: [], missing: 0 };
    groups.set(n.narrativeId, group);
    const range = locate(m.text);
    if (!range) {
      group.missing++;
      continue;
    }
    if (flagged.some((f) => overlaps(f.range, range))) continue;
    const item: FlaggedRange = {
      range,
      text: m.text,
      result: m.result,
      style: highlightStyle(n.state, m.result.matchLevel),
      contentHash: '',
      sentenceHashes: [],
    };
    flagged.push(item);
    group.items.push(item);
    group.best ??= item;
    const block = blockFor(range.startContainer);
    byBlock.set(block, [...(byBlock.get(block) ?? []), item]);
  }
  for (const [block, items] of byBlock) highlighter.setBlock(block, items);
}

function renderList() {
  const list = [...groups.values()].sort(
    (a, b) => STATES.indexOf(a.narrative.state) - STATES.indexOf(b.narrative.state),
  );
  if (!list.length) {
    listEl.innerHTML = '<p class="empty">Nothing on this page matches a tracked narrative.</p>';
    return;
  }
  listEl.innerHTML = list
    .map((g, i) => {
      const n = g.narrative;
      const count = g.items.length;
      return `<button class="item" data-group="${i}" data-state="${n.state}">
        <span class="chip">${esc(STATE_LABELS[n.state as NarrativeState] ?? n.state)}</span>
        <span class="title">${esc(clip(n.title, 140))}</span>
        <span class="meta">${count ? `${count} passage${count === 1 ? '' : 's'} highlighted` : 'Not located in the page'}${
          n.notes.length ? ` · ${n.notes.length} note${n.notes.length === 1 ? '' : 's'}` : ' · no notes yet'
        }</span>
      </button>`;
    })
    .join('');
  listEl.querySelectorAll<HTMLElement>('.item').forEach((el) =>
    el.addEventListener('click', () => {
      const g = list[+el.dataset.group!];
      const first = g.items[0];
      if (!first) return;
      first.range.startContainer.parentElement?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      flash(g.items.map((x) => x.range));
    }),
  );
}

/** Outlines ranges for a moment, after the panel scrolled to them. */
function flash(ranges: Range[]) {
  if (!('highlights' in CSS)) return;
  if (!document.getElementById('ht-flash-style')) {
    const s = document.createElement('style');
    s.id = 'ht-flash-style';
    s.textContent = '::highlight(ht-flash) { background-color: oklch(0.7 0.15 255 / 0.45); }';
    document.head.append(s);
  }
  CSS.highlights.set('ht-flash', new Highlight(...ranges));
  setTimeout(() => CSS.highlights.delete('ht-flash'), 1400);
}

// ---- Notes: sign-in first, then the form, then the posted note (as NoteFlow in the extension).

/** Sentences (snapped to full sentence boundaries) that the range touches, across blocks. */
function sentencesInRange(range: Range): Sentence[] {
  const blocks = new Set<Element>();
  for (const t of textNodesUnder(blockFor(range.commonAncestorContainer))) {
    if (range.intersectsNode(t)) blocks.add(blockFor(t));
  }
  const out: Sentence[] = [];
  for (const block of blocks) {
    const model = buildModel(block);
    const offsets = offsetsOf(model, range);
    if (!offsets) continue;
    const [lo, hi] = offsets;
    out.push(...splitSentences(model.text).filter((s) => s.start < hi && s.end > lo));
  }
  return out;
}

const message = (err: unknown) => (err instanceof Error ? err.message : String(err));

async function openNote(range: Range) {
  const sentences = sentencesInRange(range);
  if (!sentences.length) {
    ui.hideAddButton();
    ui.toast('Select at least one full sentence of 5 or more words.', 'error');
    return;
  }
  const anchor = range.getBoundingClientRect();
  const text = sentences.map((s) => s.text).join(' ');
  const { contentHash, sentenceHashes } = await fingerprintSentences(sentences);
  ui.openNoteForm(anchor, text, async (note) => {
    await api('notes', { ...note, userId, passage: { text, url: cfg.url.split('#')[0], contentHash, sentenceHashes } });
    window.getSelection()?.removeAllRanges();
    ui.toast('Note submitted. It is shown once it passes review and readers rate it.');
  });
}

function setupSelection() {
  let pending: Range | null = null;
  const onSelectionDone = (e: Event) => {
    if (ui.owns(e) || ui.panelOpen) return;
    setTimeout(() => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || sel.rangeCount === 0 || sel.toString().trim().length < 10) {
        pending = null;
        ui.hideAddButton();
        return;
      }
      pending = sel.getRangeAt(0).cloneRange();
      const rects = pending.getClientRects();
      const rect = rects[rects.length - 1] ?? pending.getBoundingClientRect();
      ui.showAddButton(rect, () => {
        if (pending) openNote(pending).catch((err) => ui.toast(message(err), 'error'));
      });
    }, 0);
  };
  document.addEventListener('mouseup', onSelectionDone);
  document.addEventListener('keyup', (e) => {
    if (e.key === 'Escape') {
      ui.closePanel();
      ui.hideAddButton();
    } else if (e.shiftKey || e.key === 'Shift') onSelectionDone(e);
  });
  document.addEventListener('mousedown', (e) => {
    if (ui.owns(e)) return;
    ui.hideAddButton();
    if (ui.panelOpen) ui.closePanel();
  });
  window.addEventListener('scroll', () => ui.hideAddButton(), { passive: true });
}

// ---- Cards: hover shows one, a click pins it with rating, reporting and a new note (as in the extension).

function setupCards() {
  let frame = 0;
  const view = (item: FlaggedRange, narrative: Narrative = item.result.narrative) => ({
    narrative,
    matchLevel: item.result.matchLevel,
    similarity: item.result.similarity,
    passage: item.text,
  });
  const actionsFor = (item: FlaggedRange, refresh: () => Promise<void>): CardActions => ({
    async rate(noteId, helpfulness) {
      try {
        await api(`notes/${encodeURIComponent(noteId)}/ratings`, { userId, helpfulness });
        myRatings = { ...myRatings, [noteId]: helpfulness };
        store.set('hashtrust.myRatings', myRatings);
      } catch (err) {
        ui.toast(`Could not save your rating: ${message(err)}`, 'error');
        throw err;
      }
      void refresh();
    },
    async report(noteId, reason) {
      try {
        await api(`notes/${encodeURIComponent(noteId)}/report`, { userId, reason });
      } catch (err) {
        ui.toast(`Could not report the note: ${err instanceof ApiError && err.status === 403 ? 'reporting needs an account, which the extension\'s popup signs in to' : message(err)}`, 'error');
        throw err;
      }
    },
    addNote() {
      ui.hideCard();
      openNote(item.range).catch((err) => ui.toast(message(err), 'error'));
    },
  });
  const pin = (x: number, y: number, item: FlaggedRange) => {
    if (item.result.narrative.narrativeId.startsWith('test-')) {
      ui.showCard(x, y, view(item), true, myRatings);
      return;
    }
    const refresh = async () => {
      try {
        const detail = await api<Narrative>(`narratives/${encodeURIComponent(item.result.narrative.narrativeId)}`);
        ui.updateCard(view(item, detail), myRatings, actions);
      } catch (e) {
        console.warn('[hashtrust] loading the narrative failed', e);
      }
    };
    const actions = actionsFor(item, refresh);
    ui.showCard(x, y, view(item), true, myRatings, actions);
    void refresh();
  };
  document.addEventListener(
    'mousemove',
    (e) => {
      if (frame || ui.cardPinned) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (ui.cardPinned) return;
        const hit = highlighter.hitTest(e.clientX, e.clientY);
        if (hit) ui.showCard(e.clientX, e.clientY, view(hit), false, myRatings);
        else ui.hideCard();
      });
    },
    { passive: true },
  );
  document.addEventListener('click', (e) => {
    if (ui.owns(e)) return;
    const hit = window.getSelection()?.isCollapsed !== false ? highlighter.hitTest(e.clientX, e.clientY) : undefined;
    if (hit) {
      e.preventDefault();
      pin(e.clientX, e.clientY, hit);
    } else if (ui.cardPinned) ui.hideCard();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && ui.cardPinned) ui.hideCard();
  });
  window.addEventListener('scroll', () => ui.hideCard(), { passive: true });
}

// ---- ?test=1: fake matches on real sentences, to check highlighting on a site without a real hit.

function testMatches(): CheckMatch[] {
  let sentences = cfg.blocks.flatMap((b) => b.split(/(?<=[.!?])\s+/)).map((s) => s.trim()).filter((s) => s.length > 60);
  if (sentences.length < 3) sentences.push(...cfg.blocks.filter((b) => b.length > 30));
  const pick = [sentences[1], sentences[Math.floor(sentences.length / 2)], sentences[sentences.length - 2]].filter(Boolean);
  const cases = [
    ['exact', 'misleading', 'misleading'],
    ['narrative', 'context', 'not_misleading'],
    ['related', 'needs_ratings', 'misleading'],
  ] as const;
  return pick.map((text, i) => {
    const [matchLevel, state, classification] = cases[i];
    return {
      text,
      result: {
        hash: `test-${i}`,
        matchLevel,
        similarity: matchLevel === 'exact' ? undefined : 0.86 - i * 0.05,
        narrative: {
          narrativeId: `test-${i}`,
          title: 'TEST highlight (not a real match)',
          state,
          notes: [
            {
              id: `test-note-${i}`,
              classification,
              body: 'Injected by test mode to check that highlighting works on this site.',
              sources: [],
              status: state === 'needs_ratings' ? 'needs_more_ratings' : 'helpful',
              ratings: { helpful: 0, somewhat: 0, not_helpful: 0 },
              createdAt: new Date().toISOString(),
            },
          ],
          spread: { sites: 1, firstSeen: '' },
        },
      },
    };
  });
}

// ---- The guide: how to use the demo, over the page on the first visit and from the panel's "How it works".

const GUIDE_KEY = 'hashtrust.guideSeen';

function showGuide() {
  doc.getElementById('ht-guide')?.remove();
  const host = doc.createElement('div');
  host.id = 'ht-guide';
  const panelWidth = panelHost.classList.contains('collapsed') ? 44 : 340;
  host.style.cssText = `all:initial;position:fixed;top:${shell ? 36 : 0}px;left:0;right:${panelWidth}px;bottom:0;z-index:2147483646;`;
  const guide = host.attachShadow({ mode: 'open' });
  const swatch = (state: NarrativeState) =>
    `<span class="swatch" style="${HIGHLIGHT_STYLES[state]}">${esc(STATE_LABELS[state])}</span>`;
  guide.innerHTML = `<style>${guideCss}</style>
  <div class="backdrop">
    <section class="card" role="dialog" aria-modal="true" aria-labelledby="guide-title">
      <p class="eyebrow">HashTrust demo</p>
      <h2 id="guide-title">How to use it</h2>
      <ol>
        <li><b>Highlights</b> mark passages that belong to a narrative readers are tracking. The colour is what their notes say:
          <span class="swatches">${swatch('misleading')}${swatch('context')}${swatch('controversial')}${swatch('needs_ratings')}</span>
          A lighter highlight matched by meaning, not by wording.</li>
        <li><b>Hover</b> a highlight to see its note. <b>Click</b> it to read every note and rate them.</li>
        <li><b>Select a sentence</b> and click <b>Add context</b> to write your own note. No account needed.</li>
        <li>The <b>panel on the right</b> lists every narrative found on this page; click one to jump to it.</li>
      </ol>
      <p class="demo">This is a demo: a copy of ${esc(cfg.host)} rendered on our server, so some things on the page may not
        work. The real product is the browser extension, which does this on the pages you read.
        <a href="${esc(cfg.readme)}" target="_blank" rel="noopener">How to install it</a></p>
      <div class="actions"><button class="btn primary" autofocus>Got it</button></div>
    </section>
  </div>`;
  const close = () => {
    store.set(GUIDE_KEY, true);
    host.remove();
    doc.removeEventListener('keydown', onKey);
  };
  const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close();
  guide.querySelector('.btn')!.addEventListener('click', close);
  guide.querySelector('.backdrop')!.addEventListener('click', (e) => e.target === e.currentTarget && close());
  doc.addEventListener('keydown', onKey);
  doc.body.append(host);
  (guide.querySelector('.btn') as HTMLButtonElement).focus();
}

// ---- Run.

setupSelection();
setupCards();
if (!store.get(GUIDE_KEY, false)) showGuide();

if (!cfg.blocks.length) {
  statusEl.innerHTML = `<span class="err">No text extracted from ${esc(cfg.host)}.</span>`;
} else {
  statusEl.innerHTML = `${spinner} Checking <b>${cfg.blocks.length}</b> text blocks from <b>${esc(cfg.host)}</b>…`;
  listEl.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  const t0 = performance.now();
  api<{ matches: CheckMatch[] }>('check', { text: cfg.blocks.join('\n\n') })
    .then(({ matches }) => {
      if (cfg.test) matches = matches.concat(testMatches());
      place(matches);
      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      const passages = flagged.length;
      statusEl.innerHTML =
        `<b>${cfg.blocks.length}</b> blocks (${esc(cfg.mode)}) checked in ${secs}s · ` +
        `<b>${groups.size}</b> narrative${groups.size === 1 ? '' : 's'}, <b>${passages}</b> passage${passages === 1 ? '' : 's'}`;
      renderList();
    })
    .catch((e) => {
      statusEl.innerHTML = `<span class="err">Check failed: ${esc(message(e))}</span>`;
      listEl.innerHTML = '';
    });
}
