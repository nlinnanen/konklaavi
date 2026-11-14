import { embeddable, type LookupBlock, type NarrativeBlock } from '@/lib/api';
import { fingerprintSentences, hashNormalized, splitSentences, type Sentence } from '@/lib/fingerprint';
import { callBackground } from '@/lib/messages';
import { bands, minhash } from '@/lib/minhash';
import { normalizeText } from '@/lib/normalize';
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
  /** Content hash and LSH bands of the whole block; null when it has no sentence. */
  fingerprint: LookupBlock | null;
  /** The normalized block text. */
  normalized: string;
}

/** The block's content hash and bands, computed as the fingerprint service does for a block. */
async function blockFingerprint(normalized: string, sentences: Sentence[]): Promise<LookupBlock | null> {
  if (!sentences.length) return null;
  const { contentHash } = await fingerprintSentences(sentences);
  return { hash: contentHash, bands: bands(minhash(normalized)) };
}

/** A match of the whole block: an edited copy, or the same claim in other words. */
const isBlockLevel = (result: LookupResult) =>
  result.signature?.matchLevel === 'variant' || result.signature?.matchLevel === 'narrative';

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
  /** Block hashes already matched against narratives. */
  private narrativeChecked = new Set<string>();
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
    else {
      this.results.clear();
      this.narrativeChecked.clear();
    }
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
      const split = splitSentences(model.text).slice(0, budget);
      const sentences = await Promise.all(
        split.map(async (s) => ({ hash: await hashNormalized(s.normalized), start: s.start, end: s.end, text: s.text })),
      );
      budget -= sentences.length;
      const normalized = normalizeText(model.text);
      const scanned = { model, sentences, normalized, fingerprint: await blockFingerprint(normalized, split) };
      this.blocks.set(block, scanned);
      touched.push(scanned);
    }
    for (const block of this.blocks.keys()) if (!block.isConnected) this.blocks.delete(block);
    this.highlighter.prune();
    if (!touched.length) return;

    const hashes = [...new Set(touched.flatMap((b) => b.sentences.map((s) => s.hash)))].filter((h) => !this.results.has(h));
    const blocks = [
      ...new Map(touched.flatMap((b) => (b.fingerprint ? [[b.fingerprint.hash, b.fingerprint] as const] : []))).values(),
    ].filter((b) => !this.results.has(b.hash));
    if (hashes.length || blocks.length) {
      const found = await callBackground('lookup', { hashes, blocks });
      [...hashes, ...blocks.map((b) => b.hash)].forEach((h) => this.results.set(h, null));
      found.forEach((r) => this.results.set(r.hash, r));
    }

    for (const block of touched) this.highlighter.setBlock(block.model.block, this.flaggedRanges(block));
    void this.matchNarratives(touched);
  }

  /**
   * Matches the blocks that nothing matched exactly or as a variant against
   * narratives. It runs after the exact and variant highlights are painted,
   * since the first embedding waits for the model to download, and a failure
   * leaves those highlights alone.
   */
  private async matchNarratives(touched: ScannedBlock[]) {
    const pending = new Map<string, NarrativeBlock>();
    for (const { fingerprint, sentences, normalized } of touched) {
      if (!fingerprint || this.narrativeChecked.has(fingerprint.hash) || !embeddable(normalized)) continue;
      if (this.results.get(fingerprint.hash) || sentences.some((s) => this.results.get(s.hash))) continue;
      pending.set(fingerprint.hash, { hash: fingerprint.hash, text: normalized });
    }
    if (!pending.size) return;
    for (const hash of pending.keys()) this.narrativeChecked.add(hash);
    let found: LookupResult[];
    try {
      found = await callBackground('matchNarratives', [...pending.values()]);
    } catch (e) {
      console.warn('[trust-verifier] narrative matching failed', e);
      return;
    }
    if (!found.length) return;
    found.forEach((r) => this.results.set(r.hash, r));
    const matched = new Set(found.map((r) => r.hash));
    for (const block of this.blocks.values()) {
      if (block.fingerprint && matched.has(block.fingerprint.hash)) {
        this.highlighter.setBlock(block.model.block, this.flaggedRanges(block));
      }
    }
  }

  /**
   * Exact sentence matches, or, when the block has none, the whole block if
   * it matches a narrative as an edited copy or in other words.
   */
  private flaggedRanges({ model, sentences, fingerprint }: ScannedBlock): FlaggedRange[] {
    const flagged: FlaggedRange[] = [];
    for (const s of sentences) {
      const result = this.results.get(s.hash);
      if (result?.flagged && !isBlockLevel(result)) {
        flagged.push({ range: rangeFor(model, s.start, s.end), text: s.text, result });
      }
    }
    const block = fingerprint && this.results.get(fingerprint.hash);
    if (!flagged.length && block?.flagged && isBlockLevel(block)) {
      const start = sentences[0]!.start;
      const end = sentences.at(-1)!.end;
      flagged.push({ range: rangeFor(model, start, end), text: model.text.slice(start, end), result: block });
    }
    return flagged;
  }

  get sentenceCount(): number {
    let n = 0;
    for (const b of this.blocks.values()) n += b.sentences.length;
    return n;
  }
}
