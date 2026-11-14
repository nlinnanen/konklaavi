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
import { barCss, guideCss } from './panel';

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

const cfg: Config = JSON.parse(document.getElementById('konklaavi-data')!.textContent!);

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
  let id = store.get<string | null>('konklaavi.userId', null);
  if (!id) {
    id = crypto.randomUUID();
    store.set('konklaavi.userId', id);
  }
  return id;
})();

// Notes and ratings need no account, as in the extension; this browser's install id writes them.
let myRatings = store.get<Record<string, Helpfulness>>('konklaavi.myRatings', {});

// ---- The top bar: the demo badge, the address bar and the page's status, over the frame in the /view
// shell (same origin), else on the page itself. It replaces the shell's banner. Like the extension, the
// page itself shows only highlights and cards.

const shell = window !== window.top ? window.top!.document : null;
const doc = shell ?? document;
doc.getElementById('ht-host')?.remove();
doc.getElementById('ht-loading')?.remove();
doc.getElementById('ht-banner')?.remove();
if (shell) shell.title = `${document.title || cfg.host} · Konklaavi`;
const barHost = doc.createElement('div');
barHost.id = 'ht-host';
barHost.style.cssText = 'all:initial;position:fixed;top:0;left:0;right:0;height:48px;z-index:2147483647;';
doc.body.append(barHost);
const root = barHost.attachShadow({ mode: 'open' });
root.innerHTML = `<style>${barCss}</style>
<div class="bar" role="banner">
  <span class="badge" title="A copy of the page rendered by Konklaavi; links, videos and logins on it may not work">DEMO</span>
  <form target="_top"><input name="url" type="url" required aria-label="Article address"><button class="btn primary">Open</button></form>
  <div class="status" role="status"></div>
  <button class="btn next" data-act="next" hidden title="Jump to the next narrative on the page (N)">Next narrative <span class="pos"></span></button>
  <button class="btn quiet" data-act="guide">How it works</button>
  <a class="readme" target="_blank" rel="noopener">Get the extension →</a>
</div>`;
const $ = <T extends Element>(s: string) => root.querySelector(s) as T;
$<HTMLInputElement>('input').value = cfg.url;
$<HTMLFormElement>('form').action = cfg.proxy + 'view';
$<HTMLAnchorElement>('.readme').href = cfg.readme;
$<HTMLButtonElement>('[data-act=guide]').addEventListener('click', () => showGuide());
$<HTMLButtonElement>('[data-act=next]').addEventListener('click', () => nextNarrative());
const statusEl = $<HTMLDivElement>('.status');
const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
const spinner = '<span class="spinner" aria-hidden="true"></span>';

// ---- Locating matches: whitespace is ignored, since extracted text adds spaces at line breaks and
// block edges that the DOM may not have.

function textIndex() {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) =>
      n.parentElement && !n.parentElement.closest('style,script,noscript,template,textarea,konklaavi-ui')
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
        store.set('konklaavi.myRatings', myRatings);
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
        console.warn('[konklaavi] loading the narrative failed', e);
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
  return { pin };
}

// ---- Next narrative: steps through the page's narratives in reading order, scrolling to each one's
// first passage, flashing its highlights and opening its card.

const before = (a: Range, b: Range) => a.compareBoundaryPoints(Range.START_TO_START, b) < 0;
let narrativeAt = -1;

/** The narratives with highlights, in the order they first appear, each with its passages in order. */
function narrativesInOrder(): FlaggedRange[][] {
  return [...groups.values()]
    .map((g) => [...g.items].sort((a, b) => (before(a.range, b.range) ? -1 : 1)))
    .filter((items) => items.length)
    .sort((a, b) => (before(a[0].range, b[0].range) ? -1 : 1));
}

function nextNarrative() {
  const list = narrativesInOrder();
  if (!list.length) return;
  narrativeAt = (narrativeAt + 1) % list.length;
  const items = list[narrativeAt];
  const first = items[0];
  first.range.startContainer.parentElement?.scrollIntoView({ block: 'center', behavior: 'instant' as ScrollBehavior });
  $('.pos').textContent = `${narrativeAt + 1} / ${list.length}`;
  flash(items.map((x) => x.range));
  // After the scroll's own event, which closes cards, has fired.
  setTimeout(() => {
    const rect = first.range.getClientRects()[0] ?? first.range.getBoundingClientRect();
    cards.pin(rect.left + Math.min(rect.width / 2, 40), rect.bottom, first);
  }, 120);
}

/** Outlines ranges for a moment, as the page jumps to them. */
function flash(ranges: Range[]) {
  if (!('highlights' in CSS)) return;
  if (!document.getElementById('ht-flash-style')) {
    const style = document.createElement('style');
    style.id = 'ht-flash-style';
    style.textContent = '::highlight(ht-flash) { background-color: oklch(0.7 0.15 255 / 0.45); }';
    document.head.append(style);
  }
  CSS.highlights.set('ht-flash', new Highlight(...ranges));
  setTimeout(() => CSS.highlights.delete('ht-flash'), 1400);
}

/** N jumps to the next narrative, unless the reader is typing. */
function onNextKey(e: KeyboardEvent) {
  const target = e.composedPath()[0] as HTMLElement | undefined;
  if (e.key !== 'n' || e.ctrlKey || e.metaKey || e.altKey) return;
  if (target && (/^(input|textarea|select)$/i.test(target.tagName) || target.isContentEditable)) return;
  if (doc.getElementById('ht-guide') || ui.panelOpen) return;
  nextNarrative();
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

// ---- The guide: a tour of the features over the page, on the first visit and from the panel's
// "How it works". Each step spotlights what it explains: the banner, a highlight, a paragraph, the panel.

const GUIDE_KEY = 'konklaavi.guideSeen';

interface Step {
  title: string;
  body: string;
  /** What the step points at, in the shell's viewport; none centres the card. */
  target?: () => DOMRect | null;
}

const frameEl = shell?.getElementById('ht-frame') as HTMLIFrameElement | null;

/** A rectangle in the frame, moved into the shell's viewport. */
function fromFrame(r: DOMRect): DOMRect {
  const f = frameEl?.getBoundingClientRect();
  return new DOMRect(r.left + (f?.left ?? 0), r.top + (f?.top ?? 0), r.width, r.height);
}

/** Scrolls an element of the page to the middle of the frame, at once so the spotlight can measure it. */
function bringIntoView(el: Element | null) {
  el?.scrollIntoView({ block: 'center', behavior: 'instant' as ScrollBehavior });
}

function steps(): Step[] {
  const first = flagged[0];
  const swatch = (state: NarrativeState) =>
    `<span class="swatch" style="${HIGHLIGHT_STYLES[state]}">${esc(STATE_LABELS[state])}</span>`;
  const paragraph = [...document.querySelectorAll('p')].find(
    (p) => (p.textContent ?? '').trim().length > 140 && !p.closest('konklaavi-ui') && p.getClientRects().length,
  );
  const highlightRect = () => {
    if (!first) return null;
    bringIntoView(first.range.startContainer.parentElement);
    return fromFrame(first.range.getBoundingClientRect());
  };
  return [
    {
      title: 'Welcome to the Konklaavi demo',
      body: `Konklaavi shows you, as you read, which passages belong to a narrative that readers are tracking and
        what their notes say about it. This short tour shows how. It takes a minute.`,
    },
    {
      title: 'This is a demo copy',
      body: `The page is a copy of ${esc(cfg.host)} that our server rendered, so its links, videos and logins may not work,
        and some sites don't work at all. The real product is the browser extension, which does this on the pages you read.`,
      target: () => barHost.getBoundingClientRect(),
    },
    {
      title: 'Highlights',
      body: `${first ? 'Like this one, a highlight' : 'A highlight'} marks a passage that belongs to a tracked narrative.
        The colour is what readers' notes say:
        <span class="swatches">${swatch('misleading')}${swatch('context')}${swatch('controversial')}${swatch('needs_ratings')}</span>
        A lighter highlight matched by meaning rather than by wording.${first ? '' : ' This page has no highlights yet.'}`,
      target: highlightRect,
    },
    {
      title: 'Read and rate the notes',
      body: `<b>Hover</b> a highlight to preview its note. <b>Click</b> it to open every note on the narrative, rate each
        one Helpful, Somewhat or Not helpful, or report it. A note is shown once readers who usually disagree both find it helpful.`,
      target: highlightRect,
    },
    {
      title: 'Add context',
      body: `<b>Select a sentence</b> anywhere on the page and click <b>Add context</b>. Say whether the passage is misleading,
        write what readers should know and add sources. No account needed. The note is reviewed, then rated by readers.`,
      target: () => {
        if (!paragraph) return null;
        bringIntoView(paragraph);
        return fromFrame(paragraph.getBoundingClientRect());
      },
    },
    {
      title: 'Open another article',
      body: `Paste an article's address in the bar and click <b>Open</b>. The bar also shows how many narratives were found
        on the page; <b>Next narrative</b> (or the N key) jumps to each one in turn, and <b>How it works</b> brings this tour back.`,
      target: () => root.querySelector('form')?.getBoundingClientRect() ?? null,
    },
    {
      title: "That's it",
      body: `Try it on this page, or open another article. To have Konklaavi on the pages you read, install the browser extension.
        <a href="${esc(cfg.readme)}" target="_blank" rel="noopener">How to install it</a>`,
    },
  ];
}

function showGuide() {
  doc.getElementById('ht-guide')?.remove();
  ui.hideCard();
  const host = doc.createElement('div');
  host.id = 'ht-guide';
  host.style.cssText = 'all:initial;position:fixed;inset:0;z-index:2147483647;';
  const guide = host.attachShadow({ mode: 'open' });
  guide.innerHTML = `<style>${guideCss}</style>
  <div class="dim"></div><div class="hole hidden"></div>
  <section class="card" role="dialog" aria-modal="true" aria-labelledby="guide-title">
    <p class="eyebrow"></p>
    <h2 id="guide-title"></h2>
    <div class="body"></div>
    <div class="actions">
      <button class="btn quiet skip">Skip tour</button>
      <span class="dots"></span>
      <button class="btn back">Back</button>
      <button class="btn primary next">Next</button>
    </div>
  </section>`;
  const q = <T extends Element>(sel: string) => guide.querySelector(sel) as T;
  const list = steps();
  let at = 0;

  const place = () => {
    const step = list[at];
    q('.eyebrow').textContent = `Step ${at + 1} of ${list.length}`;
    q('h2').textContent = step.title;
    q('.body').innerHTML = step.body;
    q('.dots').innerHTML = list.map((_, i) => `<i class="${i === at ? 'on' : ''}"></i>`).join('');
    q<HTMLButtonElement>('.back').hidden = at === 0;
    q<HTMLButtonElement>('.next').textContent = at === list.length - 1 ? 'Done' : 'Next';
    const card = q<HTMLElement>('.card');
    const hole = q<HTMLElement>('.hole');
    const rect = step.target?.() ?? null;
    const vw = innerWidth;
    const vh = innerHeight;
    if (!rect || !rect.width || rect.bottom < 0 || rect.top > vh) {
      hole.classList.add('hidden');
      q('.dim').classList.remove('hidden');
      card.style.left = `${Math.max(16, (vw - card.offsetWidth) / 2)}px`;
      card.style.top = `${Math.max(16, (vh - card.offsetHeight) / 2)}px`;
      return;
    }
    const pad = 6;
    const box = {
      left: Math.max(4, rect.left - pad),
      top: Math.max(4, rect.top - pad),
      right: Math.min(vw - 4, rect.right + pad),
      bottom: Math.min(vh - 4, rect.bottom + pad),
    };
    hole.classList.remove('hidden');
    q('.dim').classList.add('hidden');
    Object.assign(hole.style, {
      left: `${box.left}px`,
      top: `${box.top}px`,
      width: `${box.right - box.left}px`,
      height: `${box.bottom - box.top}px`,
    });
    // Beside the target where there is room: below, above, else to its left.
    const w = card.offsetWidth;
    const h = card.offsetHeight;
    let left = Math.min(Math.max(16, box.left), vw - w - 16);
    let top: number;
    if (box.bottom + 14 + h < vh) top = box.bottom + 14;
    else if (box.top - 14 - h > 0) top = box.top - 14 - h;
    else {
      top = Math.max(16, Math.min(box.top, vh - h - 16));
      left = Math.max(16, box.left - w - 16);
    }
    card.style.left = `${left}px`;
    card.style.top = `${top}px`;
  };

  const close = () => {
    store.set(GUIDE_KEY, true);
    host.remove();
    doc.removeEventListener('keydown', onKey);
    removeEventListener('resize', place);
  };
  const go = (to: number) => {
    if (to >= list.length) return close();
    at = Math.max(0, to);
    place();
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') close();
    else if (e.key === 'ArrowRight') go(at + 1);
    else if (e.key === 'ArrowLeft') go(at - 1);
  };
  q('.next').addEventListener('click', () => go(at + 1));
  q('.back').addEventListener('click', () => go(at - 1));
  q('.skip').addEventListener('click', close);
  doc.addEventListener('keydown', onKey);
  addEventListener('resize', place);
  doc.body.append(host);
  place();
  q<HTMLButtonElement>('.next').focus();
}

// ---- Run.

setupSelection();
const cards = setupCards();
document.addEventListener('keydown', onNextKey);
if (shell) shell.addEventListener('keydown', onNextKey);
// The tour starts once the page is checked, so it can point at a real highlight.
const firstVisit = !store.get(GUIDE_KEY, false);

if (!cfg.blocks.length) {
  statusEl.innerHTML = `<span class="err">No text could be read from ${esc(cfg.host)}.</span>`;
  if (firstVisit) showGuide();
} else {
  statusEl.innerHTML = `${spinner} Checking the page…`;
  const t0 = performance.now();
  api<{ matches: CheckMatch[] }>('check', { text: cfg.blocks.join('\n\n') })
    .then(({ matches }) => {
      if (cfg.test) matches = matches.concat(testMatches());
      place(matches);
      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      const shown = new Set(flagged.map((f) => f.result.narrative.narrativeId)).size;
      statusEl.innerHTML = shown
        ? `<b>${shown}</b> narrative${shown === 1 ? '' : 's'} on this page, <b>${flagged.length}</b> passage${flagged.length === 1 ? '' : 's'} highlighted`
        : 'No tracked narratives on this page';
      statusEl.title = `${cfg.blocks.length} text blocks (${cfg.mode}) checked in ${secs}s`;
      const count = narrativesInOrder().length;
      if (count) {
        $<HTMLButtonElement>('[data-act=next]').hidden = false;
        $('.pos').textContent = `0 / ${count}`;
      }
      if (firstVisit) showGuide();
    })
    .catch((e) => {
      statusEl.innerHTML = `<span class="err">Check failed: ${esc(message(e))}</span>`;
      if (firstVisit) showGuide();
    });
}
