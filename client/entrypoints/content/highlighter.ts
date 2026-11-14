import { HIGHLIGHT_STYLES, type HighlightStyle } from '@/lib/state';
import type { LookupResult } from '@/lib/types';

export interface FlaggedRange {
  range: Range;
  text: string;
  result: LookupResult;
  style: HighlightStyle;
  /** The matched content: the sentence, block or unit hash and its sentences, sent as the passage of a note. */
  contentHash: string;
  sentenceHashes: string[];
}

export const highlightSupported = typeof CSS !== 'undefined' && 'highlights' in CSS;

const STYLE_ID = 'konklaavi-highlight-style';
const STYLES = Object.keys(HIGHLIGHT_STYLES) as HighlightStyle[];

/** Registered highlight name per style. */
export const highlightName = (style: HighlightStyle) => `ht-${style}`;

/**
 * Highlights matched text with the CSS Custom Highlight API, which paints
 * ranges without touching the page DOM, so it can't break site scripts.
 */
export class Highlighter {
  private highlights = new Map<string, Highlight>();
  private byBlock = new Map<Element, FlaggedRange[]>();
  private enabled = true;

  constructor() {
    if (!highlightSupported) return;
    for (const s of STYLES) this.highlights.set(highlightName(s), new Highlight());
    this.injectStyle();
    this.register();
  }

  private injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = STYLES.map((s) => `::highlight(${highlightName(s)}) { ${HIGHLIGHT_STYLES[s]} }`).join('\n');
    (document.head ?? document.documentElement).append(style);
  }

  private register() {
    for (const [name, h] of this.highlights) CSS.highlights.set(name, h);
  }

  setEnabled(enabled: boolean) {
    if (!highlightSupported || enabled === this.enabled) return;
    this.enabled = enabled;
    if (enabled) this.register();
    else for (const name of this.highlights.keys()) CSS.highlights.delete(name);
  }

  setBlock(block: Element, items: FlaggedRange[]) {
    for (const old of this.byBlock.get(block) ?? []) this.highlights.get(highlightName(old.style))?.delete(old.range);
    if (items.length) this.byBlock.set(block, items);
    else this.byBlock.delete(block);
    for (const item of items) this.highlights.get(highlightName(item.style))?.add(item.range);
  }

  /** Drop blocks that were removed from the page. */
  prune() {
    for (const block of this.byBlock.keys()) if (!block.isConnected) this.setBlock(block, []);
  }

  all(): FlaggedRange[] {
    return [...this.byBlock.values()].flat();
  }

  /**
   * The range under the given viewport point. Only the ranges of the blocks
   * around the caret position at that point are measured, so a page with many
   * highlights costs no more per pointer move than one with a few.
   */
  hitTest(x: number, y: number): FlaggedRange | undefined {
    if (!this.enabled || !this.byBlock.size) return undefined;
    const node = caretNode(x, y);
    for (let el = node instanceof Element ? node : node?.parentElement; el; el = el.parentElement) {
      const items = this.byBlock.get(el);
      if (!items) continue;
      for (const item of items) {
        for (const r of item.range.getClientRects()) {
          if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return item;
        }
      }
    }
    return undefined;
  }
}

/** The DOM node at a viewport point, at text granularity where the browser offers it. */
function caretNode(x: number, y: number): Node | null {
  if (document.caretPositionFromPoint) return document.caretPositionFromPoint(x, y)?.offsetNode ?? null;
  if (document.caretRangeFromPoint) return document.caretRangeFromPoint(x, y)?.startContainer ?? null;
  return document.elementFromPoint(x, y);
}
