import { displayVerdict } from '@/lib/messages';
import { NEGATIVE_VERDICTS, type LookupResult, type NegativeVerdict } from '@/lib/types';

export interface FlaggedRange {
  range: Range;
  text: string;
  result: LookupResult;
}

export const highlightSupported = typeof CSS !== 'undefined' && 'highlights' in CSS;

const STYLE_ID = 'trust-verifier-highlight-style';
const COLORS: Record<NegativeVerdict, [string, string]> = {
  misinformation: ['rgba(239, 68, 68, 0.22)', '#dc2626'],
  manipulative: ['rgba(168, 85, 247, 0.22)', '#9333ea'],
  misleading: ['rgba(245, 158, 11, 0.25)', '#d97706'],
};

/** Registered highlight name: exact matches as `tv-<verdict>`, edited copies as `tv-<verdict>-variant`. */
function highlightName(result: LookupResult): string {
  const verdict = displayVerdict(result) as NegativeVerdict;
  return result.signature?.matchLevel === 'variant' ? `tv-${verdict}-variant` : `tv-${verdict}`;
}

const STYLES = NEGATIVE_VERDICTS.flatMap((v) => {
  const [bg, line] = COLORS[v];
  return [
    `::highlight(tv-${v}) { background-color: ${bg}; text-decoration: underline wavy ${line}; text-decoration-thickness: 1px; }`,
    `::highlight(tv-${v}-variant) { text-decoration: underline dashed ${line}; text-decoration-thickness: 2px; }`,
  ];
});

/**
 * Highlights flagged text with the CSS Custom Highlight API, which paints
 * ranges without touching the page DOM, so it can't break site scripts.
 */
export class Highlighter {
  private highlights = new Map<string, Highlight>();
  private byBlock = new Map<Element, FlaggedRange[]>();
  private enabled = true;

  constructor() {
    if (!highlightSupported) return;
    for (const v of NEGATIVE_VERDICTS) {
      this.highlights.set(`tv-${v}`, new Highlight());
      this.highlights.set(`tv-${v}-variant`, new Highlight());
    }
    this.injectStyle();
    this.register();
  }

  private injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = STYLES.join('\n');
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
    for (const old of this.byBlock.get(block) ?? []) this.highlightFor(old)?.delete(old.range);
    if (items.length) this.byBlock.set(block, items);
    else this.byBlock.delete(block);
    for (const item of items) this.highlightFor(item)?.add(item.range);
  }

  private highlightFor(item: FlaggedRange): Highlight | undefined {
    return this.highlights.get(highlightName(item.result));
  }
  /** Drop blocks that were removed from the page. */
  prune() {
    for (const block of this.byBlock.keys()) if (!block.isConnected) this.setBlock(block, []);
  }

  all(): FlaggedRange[] {
    return [...this.byBlock.values()].flat();
  }

  /** Flagged range under the given viewport point, if any. */
  hitTest(x: number, y: number): FlaggedRange | undefined {
    if (!this.enabled) return undefined;
    for (const item of this.all()) {
      for (const r of item.range.getClientRects()) {
        if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return item;
      }
    }
    return undefined;
  }
}
