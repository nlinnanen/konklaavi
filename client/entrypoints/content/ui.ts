import { MAX_NOTE_LENGTH, noteProblem, safeHref } from '@/lib/api';
import { featuredNote, spreadLine, STATE_LABELS } from '@/lib/state';
import type {
  Classification,
  Helpfulness,
  MatchLevel,
  Narrative,
  NarrativeState,
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

/* State chip */
.chip {
  display: inline-flex; align-items: center; gap: 6px; height: 22px; padding: 0 9px 0 8px; border-radius: 999px;
  font-size: 11.5px; font-weight: 650; letter-spacing: .01em; white-space: nowrap;
  color: var(--c); background: var(--c-soft);
}
.chip::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
[data-state="misleading"] { --c: var(--misleading); --c-soft: var(--misleading-soft); }
[data-state="context"] { --c: var(--context); --c-soft: var(--context-soft); }
[data-state="controversial"] { --c: var(--controversial); --c-soft: var(--controversial-soft); }
[data-state="needs_ratings"] { --c: var(--needs_ratings); --c-soft: var(--needs_ratings-soft); }
[data-state="tracked"] { --c: var(--tracked); --c-soft: var(--tracked-soft); }

/* Card: the tooltip of a highlight, compact on hover and complete once pinned */
.card {
  width: 340px; max-height: min(560px, calc(100vh - 16px)); overflow: auto; overscroll-behavior: contain;
  background: var(--paper); border: 1px solid var(--line); border-radius: 14px; box-shadow: var(--shadow);
  pointer-events: none;
}
.card.pinned { width: 380px; pointer-events: auto; }
.card-head { padding: 14px 16px 12px; }
.card-top { display: flex; align-items: center; gap: 8px; min-height: 28px; }
.match { margin-left: auto; font-size: 11.5px; color: var(--ink-3); white-space: nowrap; }
.card-top .icon-btn { margin-right: -6px; }
.spread { margin-top: 4px; font-size: 12px; color: var(--ink-2); }
.card > .spread { margin: 0; padding: 2px 16px 10px; }
.notes { padding: 0 8px 8px; display: grid; gap: 6px; }
.note { padding: 12px; border-radius: 10px; background: var(--paper-2); }
.note.lead { background: var(--c-soft); }
.note-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 11.5px; color: var(--ink-3); }
.status { font-weight: 650; color: var(--ink-2); }
.note.lead .status { color: var(--c); }
.context-label { margin-bottom: 6px; font-size: 12px; font-weight: 650; color: var(--c); }
.note-status { margin-top: 8px; font-size: 11.5px; }
.body { font-size: 13px; line-height: 1.5; white-space: pre-line; overflow-wrap: anywhere; }
.note.lead .body { font-size: 13.5px; }
.sources { display: flex; flex-wrap: wrap; gap: 4px 10px; margin-top: 8px; font-size: 12px; }
.sources a { color: var(--ink-2); text-decoration: underline; text-decoration-color: var(--line); text-underline-offset: 2px; }
.sources a:hover { color: var(--ink); text-decoration-color: currentColor; }
.rate { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
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
.count { font-size: 11.5px; color: var(--ink-3); }
.report-btn {
  margin-left: auto; border: 0; background: none; padding: 4px; cursor: pointer; font: inherit; font-size: 11.5px;
  color: var(--ink-3); text-decoration: underline; text-decoration-color: transparent; text-underline-offset: 2px;
}
.report-btn:hover { color: var(--danger); text-decoration-color: currentColor; }
.report-form { display: flex; gap: 6px; margin-top: 8px; }
.report-form input { flex: 1; min-width: 0; }
.empty { padding: 4px 16px 4px; font-size: 12.5px; color: var(--ink-2); }
.card-foot {
  display: flex; align-items: center; gap: 10px; padding: 10px 16px 14px; border-top: 1px solid var(--line);
  font-size: 11.5px; color: var(--ink-3);
}
.card-foot .btn { margin-left: auto; flex: none; }
.hint { padding: 0 16px 12px; font-size: 11.5px; color: var(--ink-3); }
.others { padding: 4px 8px 2px; font-size: 11.5px; font-weight: 650; color: var(--ink-3); }
.matched { margin: 0 16px 12px; font-size: 11.5px; color: var(--ink-3); }
.matched summary { cursor: pointer; width: fit-content; }
.matched summary:hover { color: var(--ink-2); }
.matched p { margin-top: 6px; padding: 8px 10px; border-radius: 8px; background: var(--paper-2); color: var(--ink-2); font-size: 12px; line-height: 1.5; }

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

const STATUS_LABELS: Record<NoteStatus, string> = {
  helpful: 'Rated helpful',
  not_helpful: 'Rated not helpful',
  controversial: 'Raters disagree',
  needs_more_ratings: 'Needs ratings',
};

const CLASSIFICATION_LABELS: Record<Classification, string> = {
  misleading: 'Misleading',
  not_misleading: 'Not misleading',
};

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

function chip(state: NarrativeState): HTMLElement {
  const c = el('span', 'chip', STATE_LABELS[state]);
  c.dataset.state = state;
  return c;
}

/** The text cut at a word boundary to at most `max` characters, with an ellipsis when cut. */
function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const space = cut.lastIndexOf(' ');
  return (space > max * 0.6 ? cut.slice(0, space) : cut).replace(/[\s.,;:!?-]+$/, '') + '…';
}

const squash = (text: string) => text.replace(/\s+/g, ' ').trim().toLowerCase();

/** What readers say about the passage, as the lead note is introduced. */
const contextLabel = (note: Note) =>
  `Readers added context: ${CLASSIFICATION_LABELS[note.classification].toLowerCase()}`;

/** Host name of a source link, without www. */
function sourceLabel(href: string): string {
  return new URL(href).hostname.replace(/^www\./, '');
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

    const fine = el('p', 'fine', 'Your note and this passage are sent to HashTrust. Readers who usually disagree decide together whether it is shown.');
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
    this.card.setAttribute('aria-label', `HashTrust: ${STATE_LABELS[view.narrative.state]}`);
    this.card.classList.remove('hidden');
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
  }

  private renderCard(
    view: CardView,
    pinned: boolean,
    myRatings: Record<string, Helpfulness>,
    actions?: CardActions,
  ) {
    const { narrative } = view;
    this.card.dataset.state = narrative.state;
    const match = el('span', 'match', MATCH_LABELS[view.matchLevel]);
    if (view.similarity !== undefined) match.title = `Similarity ${view.similarity.toFixed(2)}`;
    const top = el('div', 'card-top', chip(narrative.state), match);
    if (pinned) top.append(closeButton(() => this.hideCard()));
    const head = el('div', 'card-head', top);
    const children: Node[] = [head];
    const featured = featuredNote(narrative);
    const spread = featured
      ? spreadLine(narrative.spread)
      : `Part of a narrative ${spreadLine(narrative.spread).replace(/^[ST]/, (c) => c.toLowerCase())}`;

    if (!pinned) {
      if (featured) children.push(el('div', 'notes', this.note(featured, true, false, myRatings)));
      children.push(el('p', 'spread', spread));
      children.push(el('p', 'hint', featured ? 'Click to rate the notes' : 'Click to add context'));
      this.card.replaceChildren(...children);
      return;
    }

    head.append(el('p', 'spread', spread));
    const rest = narrative.notes.filter((n) => n !== featured);
    if (featured) children.push(el('div', 'notes', this.note(featured, true, true, myRatings, actions)));
    if (rest.length) {
      if (featured) children.push(el('p', 'others', `Other notes (${rest.length})`));
      children.push(el('div', 'notes', ...rest.map((n) => this.note(n, false, true, myRatings, actions))));
    }
    if (!narrative.notes.length) children.push(el('p', 'empty', 'No reader has added context yet.'));
    // The narrative's title is the first sentence of the passage its first note was written on.
    if (!squash(view.passage).includes(squash(narrative.title))) {
      children.push(el('details', 'matched', el('summary', undefined, 'Matched text'), el('p', undefined, narrative.title)));
    }

    if (actions) {
      const foot = el('div', 'card-foot', 'Ratings from readers who usually disagree decide what is shown.');
      foot.append(button('Add a note', 'btn', () => actions.addNote()));
      children.push(foot);
    }
    this.card.replaceChildren(...children);
  }

  private note(note: Note, lead: boolean, pinned: boolean, myRatings: Record<string, Helpfulness>, actions?: CardActions): HTMLElement {
    const box = el('article', lead ? 'note lead' : 'note');
    const total = note.ratings.helpful + note.ratings.somewhat + note.ratings.not_helpful;
    if (lead) box.append(el('p', 'context-label', contextLabel(note)), el('p', 'body', pinned ? note.body : truncate(note.body, 220)));
    else {
      const meta = el(
        'div',
        'note-meta',
        el('span', 'status', STATUS_LABELS[note.status]),
        el('span', undefined, `Says: ${CLASSIFICATION_LABELS[note.classification].toLowerCase()}`),
      );
      box.append(meta, el('p', 'body', note.body));
    }

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
    if (links.length) box.append(el('div', 'sources', ...links));
    if (lead) box.append(el('p', 'note-status status', STATUS_LABELS[note.status]));
    if (!pinned || !actions) return box;

    const seg = el('div', 'seg');
    seg.setAttribute('role', 'group');
    seg.setAttribute('aria-label', 'Rate this note');
    const mine = myRatings[note.id];
    const rateButtons = HELPFULNESS_LABELS.map(([value, label]) => {
      const b = button(label, '', async () => {
        rateButtons.forEach((x) => (x.disabled = true));
        try {
          await actions.rate(note.id, value);
          rateButtons.forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
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
          form.replaceWith(el('p', 'count', 'Reported. A moderator will review this note.'));
        } catch {
          send.disabled = false;
        }
      });
      box.append(form);
      reason.focus();
    });
    reportBtn.setAttribute('aria-label', 'Report note');
    const rate = el('div', 'rate', el('span', 'rate-label', 'Helpful?'), seg, reportBtn);
    box.append(rate);
    if (total) box.append(el('p', 'count', `${total} rating${total === 1 ? '' : 's'}, ${note.ratings.helpful} helpful`));
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
