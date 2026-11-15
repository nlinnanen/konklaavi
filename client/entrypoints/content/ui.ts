import { MAX_NOTE_LENGTH, noteProblem, safeHref } from '@/lib/api';
import { bothSides, headline, leadNote, noteSnippet, shownNotes, spreadLine, trustLine } from '@/lib/state';
import type {
  Classification,
  Helpfulness,
  MatchLevel,
  Narrative,
  Note,
  NoteStatus,
} from '@/lib/types';
import { UI_HOST_TAG } from './textModel';

/**
 * Tokens and components of the in-page UI. Sizes are in px because rem would
 * follow the host page's root font size. Colours are OKLCH, neutrals tinted
 * warm, and the palette follows the reader's light or dark preference.
 */
const CSS_TEXT = `
:host { all: initial; }
.layer {
  --ink: oklch(0.24 0.012 70);
  --ink-2: oklch(0.43 0.012 70);
  --ink-3: oklch(0.55 0.01 70);
  --paper: oklch(0.992 0.003 85);
  --paper-2: oklch(0.966 0.006 85);
  --paper-3: oklch(0.935 0.008 85);
  --line: oklch(0.9 0.008 85);
  --action: oklch(0.28 0.014 70);
  --action-ink: oklch(0.98 0.004 85);
  --focus: oklch(0.58 0.14 255);
  --danger: oklch(0.52 0.17 30);
  --shadow: 0 1px 2px oklch(0.2 0.01 70 / 0.08), 0 12px 32px -8px oklch(0.2 0.01 70 / 0.22);

  --misleading: oklch(0.55 0.19 33);
  --misleading-soft: oklch(0.955 0.03 35);
  --context: oklch(0.5 0.1 215);
  --context-soft: oklch(0.955 0.025 210);
  --controversial: oklch(0.48 0.015 260);
  --controversial-soft: oklch(0.945 0.006 260);
  --needs_ratings: oklch(0.52 0.1 75);
  --needs_ratings-soft: oklch(0.955 0.035 85);
  --tracked: oklch(0.48 0.02 260);
  --tracked-soft: oklch(0.95 0.005 260);

  position: fixed; z-index: 2147483647; box-sizing: border-box;
  --font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  font: 400 13px/1.45 var(--font);
  color: var(--ink); -webkit-font-smoothing: antialiased; text-align: left;
  animation: enter 160ms cubic-bezier(0.25, 1, 0.5, 1);
}
@media (prefers-color-scheme: dark) {
  .layer {
    --ink: oklch(0.94 0.006 85);
    --ink-2: oklch(0.79 0.01 85);
    --ink-3: oklch(0.66 0.01 85);
    --paper: oklch(0.235 0.008 70);
    --paper-2: oklch(0.275 0.009 70);
    --paper-3: oklch(0.31 0.01 70);
    --line: oklch(0.36 0.01 70);
    --action: oklch(0.93 0.008 85);
    --action-ink: oklch(0.22 0.01 70);
    --focus: oklch(0.72 0.13 255);
    --danger: oklch(0.72 0.15 30);
    --shadow: 0 1px 2px oklch(0 0 0 / 0.3), 0 16px 40px -8px oklch(0 0 0 / 0.55);

    --misleading: oklch(0.75 0.15 35);
    --misleading-soft: oklch(0.31 0.06 33);
    --context: oklch(0.76 0.09 210);
    --context-soft: oklch(0.3 0.04 215);
    --controversial: oklch(0.78 0.01 260);
    --controversial-soft: oklch(0.3 0.008 260);
    --needs_ratings: oklch(0.8 0.1 80);
    --needs_ratings-soft: oklch(0.31 0.04 75);
    --tracked: oklch(0.78 0.015 260);
    --tracked-soft: oklch(0.3 0.006 260);
  }
}
@keyframes enter { from { opacity: 0; transform: translateY(4px); } }
@media (prefers-reduced-motion: reduce) { .layer { animation: none; } }
.layer *, .layer *::before, .layer *::after { box-sizing: border-box; }
.hidden { display: none !important; }
p { margin: 0; }
a { color: inherit; }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }

/* Buttons */
.btn {
  font: 600 12.5px/1 var(--font); display: inline-flex; align-items: center; gap: 6px;
  height: 32px; padding: 0 14px; border-radius: 8px; cursor: pointer;
  border: 1px solid var(--line); background: var(--paper); color: var(--ink);
  transition: background-color 150ms, border-color 150ms, opacity 150ms;
}
.btn:hover:not(:disabled) { background: var(--paper-2); border-color: var(--ink-3); }
.btn.primary { background: var(--action); color: var(--action-ink); border-color: var(--action); }
.btn.primary:hover:not(:disabled) { background: var(--action); opacity: .88; }
.btn.quiet { border-color: transparent; background: transparent; color: var(--ink-2); padding: 0 8px; }
.btn.quiet:hover:not(:disabled) { background: var(--paper-2); border-color: transparent; color: var(--ink); }
.btn:disabled { opacity: .5; cursor: default; }
.icon-btn {
  width: 28px; height: 28px; display: grid; place-items: center; border: 0; border-radius: 6px;
  background: transparent; color: var(--ink-3); cursor: pointer; font: inherit; font-size: 16px; line-height: 1;
}
.icon-btn:hover { background: var(--paper-2); color: var(--ink); }

/* Selection button */
.add-btn {
  height: 34px; padding: 0 14px 0 12px; border-radius: 999px; border: 0; cursor: pointer;
  display: inline-flex; align-items: center; gap: 7px;
  background: var(--action); color: var(--action-ink); font: 600 13px/1 var(--font);
  box-shadow: var(--shadow);
}
.add-btn:hover { opacity: .9; }
.add-btn svg { width: 15px; height: 15px; }

/* Headline: the state's colour dot and what readers concluded */
.headline {
  display: flex; align-items: baseline; gap: 8px; min-width: 0;
  font-size: 13px; font-weight: 650; line-height: 1.35; color: var(--c);
}
.headline::before { content: ""; flex: none; width: 8px; height: 8px; border-radius: 50%; background: currentColor; transform: translateY(-1px); }
[data-state="misleading"] { --c: var(--misleading); --c-soft: var(--misleading-soft); }
[data-state="context"] { --c: var(--context); --c-soft: var(--context-soft); }
[data-state="controversial"] { --c: var(--controversial); --c-soft: var(--controversial-soft); }
[data-state="needs_ratings"] { --c: var(--needs_ratings); --c-soft: var(--needs_ratings-soft); }
[data-state="tracked"] { --c: var(--tracked); --c-soft: var(--tracked-soft); }
.headline[data-state="needs_ratings"], .headline[data-state="tracked"] { font-weight: 600; color: var(--ink-2); }
.headline[data-state="needs_ratings"]::before { background: var(--needs_ratings); }
.headline[data-state="tracked"]::before { background: var(--tracked); }

/* Text roles: body 13px, meta 12px, small 11.5px */
.text { font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
.meta { font-size: 12px; line-height: 1.4; color: var(--ink-2); }
.small { font-size: 11.5px; line-height: 1.4; color: var(--ink-3); }
.clamp { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: var(--lines); overflow: hidden; }
.clamp.open { display: block; -webkit-line-clamp: unset; }
.one-line { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.body { white-space: pre-line; }
.more {
  border: 0; background: none; padding: 2px 0; margin-top: 2px; cursor: pointer;
  font: 600 12px/1.4 var(--font); color: var(--ink-2); text-decoration: underline; text-underline-offset: 2px;
}
.more:hover { color: var(--ink); }

/* Card: the tooltip of a highlight, a short preview on hover and complete once pinned */
.card {
  width: 340px; max-height: min(560px, calc(100vh - 16px)); overflow: auto; overscroll-behavior: contain;
  background: var(--paper); border: 1px solid var(--line); border-radius: 14px; box-shadow: var(--shadow);
  pointer-events: none;
}
.card.pinned { width: 380px; pointer-events: auto; }
.preview { padding: 12px 14px; display: grid; gap: 6px; }
.card-head { padding: 12px 16px 10px; display: grid; gap: 4px; }
.card-top { display: flex; align-items: flex-start; gap: 8px; }
.card-top .headline { flex: 1; padding-top: 5px; }
.card-top .icon-btn { margin: 0 -6px 0 0; flex: none; }
.section { padding: 0 8px 8px; }
.lead { padding: 12px; border-radius: 10px; background: var(--c-soft); display: grid; gap: 6px; }
.says { font-size: 12px; font-weight: 650; color: var(--c); }
.sources { display: flex; flex-wrap: wrap; gap: 4px; }
.sources a {
  display: inline-flex; align-items: center; height: 22px; padding: 0 8px; border-radius: 999px;
  font-size: 11.5px; color: var(--ink-2); text-decoration: none; background: var(--paper); border: 1px solid var(--line);
}
.sources a:hover { color: var(--ink); border-color: var(--ink-3); }
.rate { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 2px; }
.rate-label { font-size: 12px; color: var(--ink-2); margin-right: 2px; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: var(--paper); }
.seg button {
  font: 550 12px/1 var(--font); height: 28px; padding: 0 10px; border: 0; cursor: pointer;
  background: transparent; color: var(--ink-2); transition: background-color 150ms, color 150ms;
}
.seg button + button { border-left: 1px solid var(--line); }
.seg button:hover:not(:disabled) { background: var(--paper-3); color: var(--ink); }
.seg button[aria-pressed="true"] { background: var(--action); color: var(--action-ink); }
.seg button:disabled { cursor: default; opacity: .6; }
.report-btn {
  margin-left: auto; border: 0; background: none; padding: 4px; cursor: pointer; font: inherit; font-size: 11.5px;
  color: var(--ink-3); text-decoration: underline; text-decoration-color: transparent; text-underline-offset: 2px;
}
.report-btn:hover { color: var(--danger); text-decoration-color: currentColor; }
.report-form { display: flex; gap: 6px; }
.report-form input { flex: 1; min-width: 0; }
.sides { margin: 0 8px 8px; padding: 10px 12px; border-radius: 10px; background: var(--paper-2); display: grid; gap: 4px; }
.sides b { font-weight: 650; color: var(--ink); }
.others { margin: 0 8px 8px; }
.others summary, .matched summary {
  cursor: pointer; width: fit-content; padding: 4px; font-size: 11.5px; font-weight: 650; color: var(--ink-3);
}
.others summary:hover, .matched summary:hover { color: var(--ink-2); }
.others ul { list-style: none; margin: 4px 0 0; padding: 0; display: grid; gap: 4px; }
.others li { display: flex; gap: 8px; align-items: baseline; padding: 8px 10px; border-radius: 8px; background: var(--paper-2); }
.others li .text { flex: 1; min-width: 0; }
.others li .small { flex: none; font-weight: 650; }
.hidden-count { padding: 0 12px 8px; }
.empty { padding: 0 16px 10px; }
.card-foot { padding: 8px 12px 12px 12px; border-top: 1px solid var(--line); display: grid; gap: 4px; }
.card-foot .btn { justify-self: start; }
.matched p { margin: 4px 4px 0; padding: 8px 10px; border-radius: 8px; background: var(--paper-2); color: var(--ink-2); font-size: 12px; line-height: 1.5; }

/* Panel: the note form */
.panel {
  width: 400px; max-height: calc(100vh - 16px); overflow: auto; padding: 16px 18px 18px;
  background: var(--paper); border: 1px solid var(--line); border-radius: 14px; box-shadow: var(--shadow);
}
.panel-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.panel h2 { margin: 0; font-size: 15px; font-weight: 650; letter-spacing: -.005em; }
.panel .lede { color: var(--ink-2); margin-bottom: 14px; text-wrap: pretty; }
.quote {
  margin: 0 0 16px; padding: 10px 12px; border-radius: 8px; background: var(--paper-2); color: var(--ink-2);
  font-size: 12.5px; line-height: 1.5; max-height: 92px; overflow: auto;
}
.quote::before { content: "\\201C"; } .quote::after { content: "\\201D"; }
.field { margin-bottom: 14px; }
.label { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; font-size: 12.5px; font-weight: 600; }
.label small { font-weight: 400; color: var(--ink-3); font-size: 11.5px; }
.choices { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.choice {
  position: relative; display: block; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; cursor: pointer;
  transition: border-color 150ms, background-color 150ms;
}
.choice:hover { border-color: var(--ink-3); }
.choice input { position: absolute; opacity: 0; pointer-events: none; }
.choice b { display: flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 650; }
.choice b::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--c); }
.choice span { display: block; margin-top: 3px; font-size: 11.5px; line-height: 1.4; color: var(--ink-2); }
.choice:has(input:checked) { border-color: var(--c); background: var(--c-soft); }
.choice:has(input:focus-visible) { outline: 2px solid var(--focus); outline-offset: 2px; }
textarea, input[type="text"] {
  width: 100%; font: 13px/1.45 var(--font); color: var(--ink); background: var(--paper);
  border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; transition: border-color 150ms;
}
textarea { resize: vertical; display: block; }
textarea:hover, input:hover { border-color: var(--ink-3); }
textarea:focus, input:focus { outline: none; border-color: var(--focus); box-shadow: 0 0 0 3px oklch(0.6 0.14 255 / 0.18); }
textarea::placeholder, input::placeholder { color: var(--ink-3); }
.counter.over { color: var(--danger); }
.fine { font-size: 11.5px; color: var(--ink-3); margin: 2px 0 14px; text-wrap: pretty; }
.error { font-size: 12px; color: var(--danger); margin: -4px 0 12px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.inline { display: flex; gap: 8px; }
.inline input { flex: 1; min-width: 0; }
.status-line { margin-top: 10px; font-size: 12px; color: var(--ink-2); }

/* Toast */
.toast {
  bottom: 20px; right: 20px; max-width: 360px; padding: 10px 14px; border-radius: 10px;
  background: var(--action); color: var(--action-ink); font-size: 13px; box-shadow: var(--shadow);
}
.toast.error { background: var(--danger); color: oklch(0.99 0.003 85); }
`;

const PLUS_ICON =
  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M8 3v10M3 8h10"/></svg>';

const MATCH_LABELS: Record<MatchLevel, string> = {
  exact: 'Exact match',
  variant: 'Edited copy',
  narrative: 'Same claim, reworded',
  related: 'Related theme',
};

const STATUS_WORDS: Record<NoteStatus, string> = {
  helpful: 'Helpful',
  not_helpful: 'Not helpful',
  controversial: 'Disputed',
  needs_more_ratings: 'Needs ratings',
};

/** What a note says about the text, in the card. */
const SAYS: Record<Classification, string> = {
  misleading: 'This note says the text is misleading',
  not_misleading: 'This note says the text is accurate',
};

const YOU_RATED: Record<Helpfulness, string> = {
  helpful: 'You rated this helpful',
  somewhat: 'You rated this somewhat helpful',
  not_helpful: 'You rated this not helpful',
};

/** Characters of a note shown on one line, and in the hover preview. */
const LINE_CHARS = 90;
const PREVIEW_CHARS = 140;

const HELPFULNESS_LABELS: [Helpfulness, string][] = [
  ['helpful', 'Helpful'],
  ['somewhat', 'Somewhat'],
  ['not_helpful', 'Not helpful'],
];

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  cls?: string,
  ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  e.append(...children);
  return e;
}

function button(label: string, cls: string, onClick: () => void): HTMLButtonElement {
  const b = el('button', cls, label);
  b.type = 'button';
  b.onclick = onClick;
  return b;
}

function closeButton(onClick: () => void): HTMLButtonElement {
  const b = button('×', 'icon-btn', onClick);
  b.setAttribute('aria-label', 'Close');
  return b;
}

function headlineEl(narrative: Narrative): HTMLElement {
  const h = el('p', 'headline', headline(narrative));
  h.dataset.state = narrative.state;
  return h;
}

/** Text clamped to `lines`, with a "Show more" button that `fitClamps` drops where nothing is cut. */
function clamped(text: string, lines: number, cls = 'text body'): HTMLElement {
  const p = el('p', `${cls} clamp`, text);
  p.style.setProperty('--lines', String(lines));
  const more = button('Show more', 'more', () => {
    const open = p.classList.toggle('open');
    more.textContent = open ? 'Show less' : 'Show more';
  });
  return el('div', 'clamped', p, more);
}

const squash = (text: string) => text.replace(/\s+/g, ' ').trim().toLowerCase();

/** Host name of a source link, without www. */
function sourceLabel(href: string): string {
  return new URL(href).hostname.replace(/^www\./, '');
}

function sourceChips(note: Note): HTMLElement | undefined {
  const links = note.sources.flatMap((s) => {
    const href = safeHref(s);
    if (!href) return [];
    const a = el('a', undefined, sourceLabel(href));
    a.href = href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.title = href;
    return [a];
  });
  return links.length ? el('div', 'sources', ...links) : undefined;
}

export interface CardActions {
  rate(noteId: string, helpfulness: Helpfulness): Promise<void>;
  report(noteId: string, reason: string): Promise<void>;
  /** Opens the note form for the highlighted passage. */
  addNote(): void;
}

/** A highlight's narrative and how its passage matched; `passage` is the highlighted text. */
export interface CardView {
  narrative: Narrative;
  matchLevel: MatchLevel;
  similarity?: number;
  passage: string;
}

export interface NoteFormResult {
  classification: Classification;
  body: string;
  sources: string[];
}

/** All in-page UI, isolated in a shadow root so page CSS can't affect it (and vice versa). */
export class OverlayUi {
  readonly host: HTMLElement;
  private root: ShadowRoot;
  private addBtn: HTMLButtonElement;
  private panel: HTMLDivElement;
  private card: HTMLDivElement;
  private toastEl: HTMLDivElement;
  private toastTimer?: ReturnType<typeof setTimeout>;
  /** The narrative the card shows, so a hover over the same highlight doesn't rebuild it. */
  private cardKey?: string;

  constructor() {
    this.host = document.createElement(UI_HOST_TAG);
    // Open in dev builds so e2e tests and devtools can reach the UI.
    this.root = this.host.attachShadow({ mode: import.meta.env.DEV ? 'open' : 'closed' });
    const style = el('style');
    style.textContent = CSS_TEXT;

    this.addBtn = el('button', 'layer add-btn hidden');
    this.addBtn.type = 'button';
    this.addBtn.innerHTML = `${PLUS_ICON}Add context`;
    this.addBtn.addEventListener('mousedown', (e) => e.preventDefault()); // keep the page selection
    this.panel = el('div', 'layer panel hidden');
    this.panel.setAttribute('role', 'dialog');
    this.card = el('div', 'layer card hidden');
    this.toastEl = el('div', 'layer toast hidden');
    this.toastEl.setAttribute('role', 'status');

    this.root.append(style, this.addBtn, this.panel, this.card, this.toastEl);
    document.documentElement.append(this.host);
  }

  /** True if the event originated inside our UI. */
  owns(e: Event): boolean {
    return e.composedPath().includes(this.host);
  }

  get panelOpen(): boolean {
    return !this.panel.classList.contains('hidden');
  }

  showAddButton(rect: DOMRect, onClick: () => void) {
    this.addBtn.onclick = onClick;
    this.addBtn.classList.remove('hidden');
    const { width, height } = this.addBtn.getBoundingClientRect();
    this.place(this.addBtn, rect.right - width, rect.bottom + 8, width, height, rect.top - height - 8);
  }

  hideAddButton() {
    this.addBtn.classList.add('hidden');
  }

  private openPanel(anchor: DOMRect, label: string, ...children: Node[]) {
    this.hideAddButton();
    this.hideCard();
    this.panel.replaceChildren(...children);
    this.panel.setAttribute('aria-label', label);
    this.panel.classList.remove('hidden');
    const { width, height } = this.panel.getBoundingClientRect();
    this.place(this.panel, anchor.left, anchor.bottom + 10, width, height, anchor.top - height - 10);
    this.panel.querySelector<HTMLElement>('input:not([type="radio"]), textarea, input[type="radio"]')?.focus();
  }

  closePanel() {
    this.panel.classList.add('hidden');
    this.panel.replaceChildren();
  }

  /** The note form. `onSubmit` rejects with the message to show when the note was not accepted. */
  openNoteForm(anchor: DOMRect, passage: string, onSubmit: (note: NoteFormResult) => Promise<void>) {
    const form = el('form');
    form.noValidate = true;

    const head = el('div', 'panel-head', el('h2', undefined, 'Add context'), closeButton(() => this.closePanel()));
    const quote = el('p', 'quote', passage.length > 600 ? passage.slice(0, 600).trimEnd() + '…' : passage);

    const choices = el('div', 'choices');
    choices.setAttribute('role', 'radiogroup');
    const options: [Classification, string, string][] = [
      ['misleading', 'Misleading', 'It leaves out or distorts facts readers need.'],
      ['not_misleading', 'Not misleading', 'It holds up, and context helps readers.'],
    ];
    for (const [value, title, hint] of options) {
      const input = el('input');
      input.type = 'radio';
      input.name = 'classification';
      input.value = value;
      input.checked = value === 'misleading';
      const label = el('label', 'choice', input, el('b', undefined, title), el('span', undefined, hint));
      label.dataset.state = value === 'misleading' ? 'misleading' : 'context';
      choices.append(label);
    }
    const classification = el('div', 'field', el('div', 'label', 'Is this passage misleading?'), choices);

    const body = el('textarea');
    body.name = 'body';
    body.rows = 4;
    body.maxLength = MAX_NOTE_LENGTH;
    body.placeholder = 'What should readers know? Stick to facts a source can back.';
    const counter = el('small', 'counter', `0 / ${MAX_NOTE_LENGTH}`);
    body.addEventListener('input', () => {
      counter.textContent = `${body.value.length} / ${MAX_NOTE_LENGTH}`;
      counter.classList.toggle('over', body.value.length >= MAX_NOTE_LENGTH);
    });
    const bodyId = 'ht-body';
    body.id = bodyId;
    const bodyLabel = el('label', 'label', 'Context', counter);
    bodyLabel.htmlFor = bodyId;

    const sources = el('textarea');
    sources.id = 'ht-sources';
    sources.name = 'sources';
    sources.rows = 2;
    sources.placeholder = 'https://…';
    const sourcesLabel = el('label', 'label', 'Sources', el('small', undefined, 'Optional, one link per line'));
    sourcesLabel.htmlFor = sources.id;

    const fine = el('p', 'fine', 'Your note and this passage are sent to Konklaavi. Readers who usually disagree decide together whether it is shown.');
    const error = el('p', 'error hidden');
    error.setAttribute('role', 'alert');
    const submit = el('button', 'btn primary', 'Submit note');
    submit.type = 'submit';
    const actions = el('div', 'actions', button('Cancel', 'btn quiet', () => this.closePanel()), submit);

    form.append(
      classification,
      el('div', 'field', bodyLabel, body),
      el('div', 'field', sourcesLabel, sources),
      fine,
      error,
      actions,
    );
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const chosen = form.querySelector<HTMLInputElement>('input[name="classification"]:checked');
      const note: NoteFormResult = {
        classification: (chosen?.value ?? 'misleading') as Classification,
        body: body.value.trim(),
        sources: sources.value.split(/\s+/).filter(Boolean),
      };
      const problem = chosen ? noteProblem(note) : 'Choose whether the passage is misleading.';
      const fail = (message: string) => {
        error.textContent = message;
        error.classList.remove('hidden');
      };
      if (problem) return fail(problem);
      error.classList.add('hidden');
      submit.disabled = true;
      submit.textContent = 'Submitting…';
      try {
        await onSubmit(note);
        this.closePanel();
      } catch (err) {
        fail(err instanceof Error ? err.message : String(err));
        submit.disabled = false;
        submit.textContent = 'Submit note';
      }
    });
    this.openPanel(anchor, 'Add context', head, quote, form);
  }

  get cardPinned(): boolean {
    return this.card.classList.contains('pinned') && !this.card.classList.contains('hidden');
  }

  /**
   * Shows a highlight's narrative: compact on hover; pinned, with every note,
   * the rating buttons and the note action, after a click. `myRatings` marks
   * the reader's own ratings. A card without `actions` offers none.
   */
  showCard(
    x: number,
    y: number,
    view: CardView,
    pinned: boolean,
    myRatings: Record<string, Helpfulness>,
    actions?: CardActions,
  ) {
    const key = `${view.narrative.narrativeId}|${pinned}`;
    const visible = !this.card.classList.contains('hidden');
    this.renderCard(view, pinned, myRatings, actions);
    this.card.classList.toggle('pinned', pinned);
    this.card.setAttribute('role', pinned ? 'dialog' : 'tooltip');
    this.card.setAttribute('aria-label', `Konklaavi: ${headline(view.narrative)}`);
    this.card.classList.remove('hidden');
    this.fitClamps();
    if (visible && key === this.cardKey) return;
    this.cardKey = key;
    const { width, height } = this.card.getBoundingClientRect();
    this.place(this.card, x + 12, y + 16, width, height, y - height - 12);
  }

  /** Re-renders a pinned card in place, as after its narrative was fetched again. */
  updateCard(
    view: CardView,
    myRatings: Record<string, Helpfulness>,
    actions: CardActions,
  ) {
    if (!this.cardPinned) return;
    this.renderCard(view, true, myRatings, actions);
    this.fitClamps();
  }

  /** Drops the "Show more" of clamped text that fits. */
  private fitClamps() {
    for (const p of this.card.querySelectorAll<HTMLElement>('.clamp:not(.open)')) {
      const more = p.nextElementSibling;
      if (more) more.classList.toggle('hidden', p.scrollHeight <= p.clientHeight + 1);
    }
  }

  private renderCard(
    view: CardView,
    pinned: boolean,
    myRatings: Record<string, Helpfulness>,
    actions?: CardActions,
  ) {
    const { narrative } = view;
    this.card.dataset.state = narrative.state;
    const lead = leadNote(narrative, myRatings);
    const spread = narrative.state === 'tracked' ? undefined : spreadLine(narrative.spread);

    if (!pinned) {
      const preview = el('div', 'preview', headlineEl(narrative));
      if (lead) {
        const snippet = el('p', 'text clamp', noteSnippet(lead, PREVIEW_CHARS));
        snippet.style.setProperty('--lines', '3');
        preview.append(snippet);
        const trust = trustLine(lead);
        if (trust) preview.append(el('p', 'meta', trust));
      }
      if (spread) preview.append(el('p', 'meta', spread));
      this.card.replaceChildren(preview);
      return;
    }

    const match = el('span', undefined, MATCH_LABELS[view.matchLevel]);
    if (view.similarity !== undefined) match.title = `Similarity ${view.similarity.toFixed(2)}`;
    const meta = el('p', 'small', ...(spread ? [spread, ' · '] : []), match);
    const head = el('div', 'card-head', el('div', 'card-top', headlineEl(narrative), closeButton(() => this.hideCard())), meta);
    const children: Node[] = [head];

    if (lead) children.push(el('div', 'section', this.leadBox(lead, myRatings, actions)));

    const shown = shownNotes(narrative.notes);
    if (narrative.state === 'controversial') {
      const sides = bothSides(narrative.notes);
      const lines = [
        ['Some readers: ', sides.misleading],
        ['Others: ', sides.not_misleading],
      ] as const;
      const box = el('div', 'sides');
      for (const [who, note] of lines) {
        if (note) box.append(el('p', 'text one-line', el('b', undefined, who), noteSnippet(note, LINE_CHARS)));
      }
      if (sides.misleading && sides.not_misleading) children.push(box);
    }

    const rest = shown.filter((n) => n !== lead);
    if (rest.length) {
      const list = el(
        'ul',
        undefined,
        ...rest.map((n) =>
          el('li', undefined, el('span', 'text one-line', noteSnippet(n, LINE_CHARS)), el('span', 'small', STATUS_WORDS[n.status])),
        ),
      );
      children.push(el('details', 'others', el('summary', undefined, `Other notes (${rest.length})`), list));
    }
    const unhelpful = narrative.notes.length - shown.length;
    if (unhelpful) {
      children.push(el('p', 'small hidden-count', `${unhelpful} note${unhelpful === 1 ? '' : 's'} rated not helpful, hidden`));
    }
    if (!narrative.notes.length) children.push(el('p', 'meta empty', 'No reader has added context yet.'));

    const foot = el('div', 'card-foot');
    if (actions) foot.append(button('Add context', 'btn', () => actions.addNote()));
    // The narrative's title is the first sentence of the passage its first note was written on.
    if (!squash(view.passage).includes(squash(narrative.title))) {
      foot.append(el('details', 'matched', el('summary', undefined, 'Matched text'), el('p', undefined, narrative.title)));
    }
    if (foot.childElementCount) children.push(foot);
    this.card.replaceChildren(...children);
  }

  /** The lead note in full: what it says, its text, sources and, with `actions`, rating and reporting. */
  private leadBox(note: Note, myRatings: Record<string, Helpfulness>, actions?: CardActions): HTMLElement {
    const box = el('article', 'lead', el('p', 'says', SAYS[note.classification]), clamped(note.body, 4));
    const chips = sourceChips(note);
    if (chips) box.append(chips);
    const trust = el('p', 'small', trustLine(note));
    if (!actions) {
      if (trust.textContent) box.append(trust);
      return box;
    }

    const seg = el('div', 'seg');
    seg.setAttribute('role', 'group');
    seg.setAttribute('aria-label', 'Rate this note');
    const mine = myRatings[note.id];
    const youRated = el('p', 'meta', mine ? YOU_RATED[mine] : '');
    youRated.classList.toggle('hidden', !mine);
    const rateButtons = HELPFULNESS_LABELS.map(([value, label]) => {
      const b = button(label, '', async () => {
        rateButtons.forEach((x) => (x.disabled = true));
        try {
          await actions.rate(note.id, value);
          rateButtons.forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
          youRated.textContent = YOU_RATED[value];
          youRated.classList.remove('hidden');
        } finally {
          rateButtons.forEach((x) => (x.disabled = false));
        }
      });
      b.setAttribute('aria-pressed', String(mine === value));
      return b;
    });
    seg.append(...rateButtons);
    const reportBtn = button('Report', 'report-btn', () => {
      reportBtn.classList.add('hidden');
      const reason = el('input');
      reason.type = 'text';
      reason.placeholder = 'What is wrong with this note?';
      reason.setAttribute('aria-label', 'Reason for reporting');
      const send = el('button', 'btn', 'Send');
      send.type = 'submit';
      const form = el('form', 'report-form', reason, send);
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!reason.value.trim()) return reason.focus();
        send.disabled = true;
        try {
          await actions.report(note.id, reason.value.trim());
          form.replaceWith(el('p', 'small', 'Reported. A moderator will review this note.'));
        } catch {
          send.disabled = false;
        }
      });
      box.append(form);
      reason.focus();
    });
    reportBtn.setAttribute('aria-label', 'Report note');
    box.append(el('div', 'rate', el('span', 'rate-label', 'Helpful?'), seg, reportBtn), youRated);
    if (trust.textContent) box.append(trust);
    return box;
  }

  hideCard() {
    this.card.classList.add('hidden');
    this.card.classList.remove('pinned');
    this.cardKey = undefined;
  }

  toast(message: string, kind: 'info' | 'error' = 'info') {
    this.toastEl.textContent = message;
    this.toastEl.classList.toggle('error', kind === 'error');
    this.toastEl.classList.remove('hidden');
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => this.toastEl.classList.add('hidden'), 4000);
  }

  /** Position a fixed element, clamped to the viewport; flips above when it doesn't fit below. */
  private place(node: HTMLElement, left: number, top: number, w: number, h: number, altTop?: number) {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    if (top + h > vh - 8 && altTop !== undefined && altTop >= 8) top = altTop;
    node.style.left = `${Math.max(8, Math.min(left, vw - w - 8))}px`;
    node.style.top = `${Math.max(8, Math.min(top, vh - h - 8))}px`;
  }
}
