import { hashNormalized, splitSentences } from '@/lib/fingerprint';
import { callBackground } from '@/lib/messages';
import type { LookupResult } from '@/lib/types';
import type { Highlighter, FlaggedRange } from './highlighter';
import { buildModel, collectBlocks, rangeFor, type BlockModel } from './textModel';

interface ScannedSentence {
  hash: string;
  start: number;
  end: number;
  text: string;
}

interface ScannedBlock {
  model: BlockModel;
  sentences: ScannedSentence[];
}

/** Cap per scan pass so huge pages don't stall the tab. */
const MAX_SENTENCES_PER_PASS = 5000;

/**
 * Splits page text into sentences, hashes them, looks the hashes up via the
 * background worker and hands flagged sentences to the highlighter.
 */
export class PageScanner {
  private blocks = new Map<Element, ScannedBlock>();
  /** hash -> result, or null when the backend has no votes for it. */
  private results = new Map<string, LookupResult | null>();
  private observer?: MutationObserver;
  private dirty = new Set<Node>();
  private timer?: ReturnType<typeof setTimeout>;
  private running: Promise<void> = Promise.resolve();

  constructor(private highlighter: Highlighter) {}

  start() {
    void this.scan([document.body]);
    this.observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.type === 'characterData') this.dirty.add(m.target);
        else {
          this.dirty.add(m.target);
          m.addedNodes.forEach((n) => this.dirty.add(n));
        }
      }
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.flushDirty(), 800);
    });
    this.observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  private flushDirty() {
    const roots = [...this.dirty];
    this.dirty.clear();
    void this.scan(roots);
  }

  /** Scan (or rescan) the blocks containing the given roots. Serialized so passes don't interleave. */
  scan(roots: Iterable<Node>, force = false): Promise<void> {
    const list = [...roots];
    this.running = this.running.then(() => this.doScan(list, force)).catch((e) => console.warn('[trust-verifier] scan failed', e));
    return this.running;
  }

  /** Forget cached results (e.g. after voting or switching backend) and rescan everything. */
  refresh(hashes?: string[]): Promise<void> {
    if (hashes) hashes.forEach((h) => this.results.delete(h));
    else this.results.clear();
    return this.scan([document.body], true);
  }

  private async doScan(roots: Node[], force: boolean) {
    const touched: ScannedBlock[] = [];
    let budget = MAX_SENTENCES_PER_PASS;
    for (const [block, nodes] of collectBlocks(roots)) {
      if (budget <= 0) break;
      const model = buildModel(block, nodes);
      const prev = this.blocks.get(block);
      if (prev && !force && prev.model.text === model.text) continue;
      const sentences = await Promise.all(
        splitSentences(model.text)
          .slice(0, budget)
          .map(async (s) => ({ hash: await hashNormalized(s.normalized), start: s.start, end: s.end, text: s.text })),
      );
      budget -= sentences.length;
      const scanned = { model, sentences };
      this.blocks.set(block, scanned);
      touched.push(scanned);
    }
    for (const block of this.blocks.keys()) if (!block.isConnected) this.blocks.delete(block);
    this.highlighter.prune();
    if (!touched.length) return;

    const unknown = [...new Set(touched.flatMap((b) => b.sentences.map((s) => s.hash)))].filter((h) => !this.results.has(h));
    if (unknown.length) {
      const found = await callBackground('lookup', unknown);
      unknown.forEach((h) => this.results.set(h, null));
      found.forEach((r) => this.results.set(r.hash, r));
    }

    for (const { model, sentences } of touched) {
      const flagged: FlaggedRange[] = [];
      for (const s of sentences) {
        const result = this.results.get(s.hash);
        if (result?.flagged) flagged.push({ range: rangeFor(model, s.start, s.end), text: s.text, result });
      }
      this.highlighter.setBlock(model.block, flagged);
    }
  }

  get sentenceCount(): number {
    let n = 0;
    for (const b of this.blocks.values()) n += b.sentences.length;
    return n;
  }
}
