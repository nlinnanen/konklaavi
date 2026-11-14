import { TIER_STYLES, TIERS, type Tier } from '@/lib/tier';
import type { LookupResult } from '@/lib/types';

export interface FlaggedRange {
  range: Range;
  text: string;
  result: LookupResult;
  tier: Tier;
  /** The content a vote from the tooltip is about: the matched sentence, block or unit. */
  contentHash: string;
  sentenceHashes: string[];
}

export const highlightSupported = typeof CSS !== 'undefined' && 'highlights' in CSS;

const STYLE_ID = 'hashtrust-highlight-style';

/** Registered highlight name per tier. */
export const highlightName = (tier: Tier) => `tv-${tier}`;

const STYLES = TIERS.map((t) => `::highlight(${highlightName(t)}) { ${TIER_STYLES[t]} }`);

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
    for (const t of TIERS) this.highlights.set(highlightName(t), new Highlight());
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
    return this.highlights.get(highlightName(item.tier));
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
