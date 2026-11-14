import { VERDICTS, VERDICT_LABELS, type LookupResult, type Verdict } from '@/lib/types';
import { UI_HOST_TAG } from './textModel';

const CSS_TEXT = `
:host { all: initial; }
* { box-sizing: border-box; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
.layer { position: fixed; z-index: 2147483647; color: #111827; font-size: 13px; line-height: 1.4; }
.hidden { display: none !important; }

.rate-btn {
  background: #111827; color: #fff; border: 0; border-radius: 999px; padding: 6px 12px;
  font-size: 12px; font-weight: 600; cursor: pointer; box-shadow: 0 4px 14px rgba(0,0,0,.25);
}
.rate-btn:hover { background: #1f2937; }

.panel {
  width: 320px; background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 14px;
  box-shadow: 0 12px 32px rgba(0,0,0,.18);
}
.panel h2 { margin: 0 0 8px; font-size: 14px; font-weight: 700; }
.excerpt {
  margin: 0 0 12px; padding: 8px 10px; background: #f9fafb; border-left: 3px solid #d1d5db;
  border-radius: 4px; color: #374151; max-height: 96px; overflow: auto; font-size: 12px;
}
.verdicts { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 10px; }
.verdict {
  border: 1px solid #d1d5db; background: #fff; border-radius: 8px; padding: 8px; cursor: pointer;
  font-size: 12px; font-weight: 600; color: #374151; text-align: left;
}
.verdict:hover { border-color: #9ca3af; }
.verdict[aria-pressed="true"] { color: #fff; border-color: transparent; }
.verdict[data-v="misinformation"][aria-pressed="true"] { background: #dc2626; }
.verdict[data-v="manipulative"][aria-pressed="true"] { background: #9333ea; }
.verdict[data-v="misleading"][aria-pressed="true"] { background: #d97706; }
.verdict[data-v="trustworthy"][aria-pressed="true"] { background: #16a34a; }
textarea {
  width: 100%; min-height: 56px; resize: vertical; border: 1px solid #d1d5db; border-radius: 8px;
  padding: 6px 8px; font-size: 12px; margin-bottom: 10px; color: #111827; background: #fff;
}
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.btn { border-radius: 8px; padding: 6px 12px; font-size: 12px; font-weight: 600; cursor: pointer; border: 1px solid #d1d5db; background: #fff; color: #374151; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn:disabled { opacity: .5; cursor: default; }

.tooltip {
  max-width: 280px; background: #111827; color: #f9fafb; border-radius: 8px; padding: 8px 10px;
  font-size: 12px; pointer-events: none; box-shadow: 0 6px 20px rgba(0,0,0,.3);
}
.tooltip strong { display: block; margin-bottom: 4px; font-size: 13px; }
.tooltip .row { display: flex; justify-content: space-between; gap: 12px; color: #d1d5db; }

.toast {
  bottom: 20px; right: 20px; background: #111827; color: #fff; border-radius: 8px; padding: 10px 14px;
  font-size: 13px; box-shadow: 0 6px 20px rgba(0,0,0,.3);
}
.toast.error { background: #b91c1c; }
`;

export interface PickerResult {
  verdict: Verdict;
  comment?: string;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

/** All in-page UI, isolated in a closed shadow root so page CSS can't affect it (and vice versa). */
export class OverlayUi {
  readonly host: HTMLElement;
  private root: ShadowRoot;
  private rateBtn: HTMLButtonElement;
  private panel: HTMLDivElement;
  private tooltip: HTMLDivElement;
  private toastEl: HTMLDivElement;
  private toastTimer?: ReturnType<typeof setTimeout>;

  constructor() {
    this.host = document.createElement(UI_HOST_TAG);
    // Open in dev builds so e2e tests and devtools can reach the UI.
    this.root = this.host.attachShadow({ mode: import.meta.env.DEV ? 'open' : 'closed' });
    const style = el('style');
    style.textContent = CSS_TEXT;

    this.rateBtn = el('button', 'layer rate-btn hidden', '⚑ Rate content');
    this.rateBtn.addEventListener('mousedown', (e) => e.preventDefault()); // keep the page selection
    this.panel = el('div', 'layer panel hidden');
    this.panel.setAttribute('role', 'dialog');
    this.tooltip = el('div', 'layer tooltip hidden');
    this.toastEl = el('div', 'layer toast hidden');
    this.toastEl.setAttribute('role', 'status');

    this.root.append(style, this.rateBtn, this.panel, this.tooltip, this.toastEl);
    document.documentElement.append(this.host);
  }

  /** True if the event originated inside our UI. */
  owns(e: Event): boolean {
    return e.composedPath().includes(this.host);
  }

  get pickerOpen(): boolean {
    return !this.panel.classList.contains('hidden');
  }

  showRateButton(rect: DOMRect, onClick: () => void) {
    this.rateBtn.onclick = onClick;
    this.rateBtn.classList.remove('hidden');
    const { width, height } = this.rateBtn.getBoundingClientRect();
    this.place(this.rateBtn, rect.right - width, rect.bottom + 8, width, height);
  }

  hideRateButton() {
    this.rateBtn.classList.add('hidden');
  }

  openPicker(anchor: DOMRect, excerpt: string, onSubmit: (r: PickerResult) => Promise<void>) {
    this.hideRateButton();
    this.panel.replaceChildren();
    let selected: Verdict | null = null;

    const title = el('h2', undefined, 'Rate this content');
    const quote = el('blockquote', 'excerpt', excerpt.length > 400 ? excerpt.slice(0, 400) + '…' : excerpt);
    const grid = el('div', 'verdicts');
    const comment = el('textarea');
    comment.placeholder = 'Why? Add a source or note (optional)';
    comment.maxLength = 500;
    const cancel = el('button', 'btn', 'Cancel');
    const submit = el('button', 'btn primary', 'Submit');
    submit.disabled = true;

    for (const v of VERDICTS) {
      const b = el('button', 'verdict', VERDICT_LABELS[v]);
      b.dataset.v = v;
      b.setAttribute('aria-pressed', 'false');
      b.onclick = () => {
        selected = v;
        grid.querySelectorAll('.verdict').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
        submit.disabled = false;
      };
      grid.append(b);
    }

    cancel.onclick = () => this.closePicker();
    submit.onclick = async () => {
      if (!selected) return;
      submit.disabled = true;
      submit.textContent = 'Submitting…';
      try {
        await onSubmit({ verdict: selected, comment: comment.value.trim() || undefined });
        this.closePicker();
      } catch {
        submit.disabled = false;
        submit.textContent = 'Submit';
      }
    };

    const actions = el('div', 'actions');
    actions.append(cancel, submit);
    this.panel.append(title, quote, grid, comment, actions);
    this.panel.classList.remove('hidden');
    const { width, height } = this.panel.getBoundingClientRect();
    this.place(this.panel, anchor.left, anchor.bottom + 8, width, height, anchor.top - height - 8);
  }

  closePicker() {
    this.panel.classList.add('hidden');
    this.panel.replaceChildren();
  }

  showTooltip(x: number, y: number, text: string, result: LookupResult) {
    this.tooltip.replaceChildren();
    const share = Math.round((result.counts[result.topVerdict] / result.total) * 100);
    this.tooltip.append(el('strong', undefined, `⚠ Flagged as ${VERDICT_LABELS[result.topVerdict].toLowerCase()}`));
    this.tooltip.append(el('div', undefined, `${share}% of ${result.total} vote${result.total === 1 ? '' : 's'}`));
    for (const v of VERDICTS) {
      if (!result.counts[v]) continue;
      const row = el('div', 'row');
      row.append(el('span', undefined, VERDICT_LABELS[v]), el('span', undefined, String(result.counts[v])));
      this.tooltip.append(row);
    }
    this.tooltip.title = text;
    this.tooltip.classList.remove('hidden');
    const { width, height } = this.tooltip.getBoundingClientRect();
    this.place(this.tooltip, x + 12, y + 16, width, height, y - height - 12);
  }

  hideTooltip() {
    this.tooltip.classList.add('hidden');
  }

  toast(message: string, kind: 'info' | 'error' = 'info') {
    this.toastEl.textContent = message;
    this.toastEl.classList.toggle('error', kind === 'error');
    this.toastEl.classList.remove('hidden');
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => this.toastEl.classList.add('hidden'), 3500);
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
