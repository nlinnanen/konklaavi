import type { LookupBlock, NarrativeBlock } from '@/lib/api';
import { embeddingUnits } from '@/lib/embed';
import { fingerprintSentences, hashNormalized, splitSentences, type Sentence } from '@/lib/fingerprint';
import { callBackground, ContextInvalidatedError } from '@/lib/messages';
import { bands, minhash } from '@/lib/minhash';
import { normalizeText } from '@/lib/normalize';
import { tierOf, tierRank, type Tier } from '@/lib/tier';
import type { LookupResult } from '@/lib/types';
import type { Highlighter, FlaggedRange } from './highlighter';
import { buildModel, collectBlocks, rangeFor, type BlockModel } from './textModel';

interface ScannedSentence {
  hash: string;
  start: number;
  end: number;
  text: string;
}

/** An embedding unit: sentences `from` to `to` (exclusive) of its block. */
interface ScannedUnit {
  from: number;
  to: number;
  hash: string;
  text: string;
}

interface ScannedBlock {
  model: BlockModel;
  sentences: ScannedSentence[];
  /** Content hash and LSH bands of the whole block; null when it has no sentence. */
  fingerprint: LookupBlock | null;
  units: ScannedUnit[];
}

/** A shown match over sentences `from` to `to` (exclusive) of a block, voted on by `hash`. */
interface Match {
  from: number;
  to: number;
  hash: string;
  result: LookupResult;
  tier: Tier;
}

/** The block's content hash and bands, computed as the fingerprint service does for a block. */
async function blockFingerprint(normalized: string, sentences: Sentence[]): Promise<LookupBlock | null> {
  if (!sentences.length) return null;
  const { contentHash } = await fingerprintSentences(sentences);
  return { hash: contentHash, bands: bands(minhash(normalized)) };
}

/** The block's embedding units, each hashed as `fingerprintSentences` hashes its sentences. */
function scannedUnits(sentences: Sentence[], hashes: string[]): Promise<ScannedUnit[]> {
  return Promise.all(
    embeddingUnits(sentences).map(async ({ from, to, text }) => ({
      from,
      to,
      text,
      hash:
        to - from === 1
          ? hashes[from]!
          : await hashNormalized(sentences.slice(from, to).map((s) => s.normalized).join(' ')),
    })),
  );
}

/** Cap per scan pass so huge pages don't stall the tab. */
const MAX_SENTENCES_PER_PASS = 5000;
/** Units embedded per scan pass; the rest wait for a later pass over their block. */
const MAX_UNITS_PER_PASS = 400;
/** Units per embedding request, so highlights appear while the rest embed. */
const UNIT_BATCH = 32;

/**
 * Splits page text into sentences, hashes them, looks the hashes up via the
 * background worker and hands flagged sentences to the highlighter.
 */
export class PageScanner {
  private blocks = new Map<Element, ScannedBlock>();
  /** hash -> result, or null when the backend has no votes for it. */
  private results = new Map<string, LookupResult | null>();
  /** Unit hash -> narrative-level or related result, or null when the unit matched nothing. */
  private unitResults = new Map<string, LookupResult | null>();
  private observer?: MutationObserver;
  private dirty = new Set<Node>();
  private timer?: ReturnType<typeof setTimeout>;
  private running: Promise<void> = Promise.resolve();
  private stopped = false;

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

  /** Stops watching the page for good, as when the extension was reloaded under this script. */
  stop() {
    this.stopped = true;
    this.observer?.disconnect();
    clearTimeout(this.timer);
    this.dirty.clear();
  }

  /** Logs a failed pass, or stops quietly when the extension runtime is gone. */
  private failed(what: string, e: unknown) {
    if (e instanceof ContextInvalidatedError) this.stop();
    else console.warn(`[hashtrust] ${what} failed`, e);
  }

  private flushDirty() {
    const roots = [...this.dirty];
    this.dirty.clear();
    void this.scan(roots);
  }

  /** Scan (or rescan) the blocks containing the given roots. Serialized so passes don't interleave. */
  scan(roots: Iterable<Node>, force = false): Promise<void> {
    const list = [...roots];
    this.running = this.running
      .then(() => (this.stopped ? undefined : this.doScan(list, force)))
      .catch((e) => this.failed('scan', e));
    return this.running;
  }

  /** Forget cached results (e.g. after voting or switching backend) and rescan everything. */
  refresh(hashes?: string[]): Promise<void> {
    if (hashes) {
      for (const h of hashes) {
        this.results.delete(h);
        this.unitResults.delete(h);
      }
    } else {
      this.results.clear();
      this.unitResults.clear();
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
      const hashes = await Promise.all(split.map((s) => hashNormalized(s.normalized)));
      const sentences = split.map((s, i) => ({ hash: hashes[i]!, start: s.start, end: s.end, text: s.text }));
      budget -= sentences.length;
      const scanned = {
        model,
        sentences,
        fingerprint: await blockFingerprint(normalizeText(model.text), split),
        units: await scannedUnits(split, hashes),
      };
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
    this.matchNarratives(touched).catch((e) => this.failed('narrative matching', e));
  }

  /**
   * Embeds the units of blocks that nothing matched as a variant, leaving out
   * units with an exactly matched sentence, and matches them against
   * narratives. It runs after the exact and variant highlights are painted,
   * since the first embedding waits for the model to download, and a failure
   * leaves those highlights alone.
   */
  private async matchNarratives(touched: ScannedBlock[]) {
    const pending = new Map<string, NarrativeBlock>();
    for (const { fingerprint, sentences, units } of touched) {
      if (fingerprint && this.results.get(fingerprint.hash)) continue;
      for (const u of units) {
        if (pending.size >= MAX_UNITS_PER_PASS) break;
        if (this.unitResults.has(u.hash) || sentences.slice(u.from, u.to).some((s) => this.results.get(s.hash))) continue;
        pending.set(u.hash, { hash: u.hash, text: u.text });
      }
    }
    const queue = [...pending.values()];
    for (let i = 0; i < queue.length; i += UNIT_BATCH) {
      const batch = queue.slice(i, i + UNIT_BATCH);
      batch.forEach((u) => this.unitResults.set(u.hash, null));
      let found: LookupResult[];
      try {
        found = await callBackground('matchNarratives', batch);
      } catch (e) {
        batch.forEach((u) => this.unitResults.delete(u.hash));
        throw e;
      }
      if (!found.length) continue;
      found.forEach((r) => this.unitResults.set(r.hash, r));
      const matched = new Set(found.map((r) => r.hash));
      for (const block of touched) {
        if (this.blocks.get(block.model.block) === block && block.units.some((u) => matched.has(u.hash))) {
          this.highlighter.setBlock(block.model.block, this.flaggedRanges(block));
        }
      }
    }
  }

  /**
   * One range per run of sentences whose strongest match is the same: each
   * sentence takes the strongest tier among its exact match, the block's
   * variant match and the units that cover it, the smallest of those on a tie.
   */
  private flaggedRanges({ model, sentences, fingerprint, units }: ScannedBlock): FlaggedRange[] {
    const matches: Match[] = [];
    const add = (from: number, to: number, hash: string, result: LookupResult | null | undefined) => {
      const tier = result && tierOf(result);
      if (tier) matches.push({ from, to, hash, result, tier });
    };
    sentences.forEach((s, i) => add(i, i + 1, s.hash, this.results.get(s.hash)));
    if (fingerprint) add(0, sentences.length, fingerprint.hash, this.results.get(fingerprint.hash));
    for (const u of units) add(u.from, u.to, u.hash, this.unitResults.get(u.hash));
    if (!matches.length) return [];

    const stronger = (a: Match, b: Match | undefined) =>
      !b || tierRank(a.tier) > tierRank(b.tier) || (a.tier === b.tier && a.to - a.from < b.to - b.from);
    const best = sentences.map((_, i) =>
      matches.reduce<Match | undefined>((top, m) => (m.from <= i && i < m.to && stronger(m, top) ? m : top), undefined),
    );
    const flagged: FlaggedRange[] = [];
    for (let i = 0; i < sentences.length; ) {
      const match = best[i];
      let j = i + 1;
      while (j < sentences.length && best[j] === match) j++;
      if (match) {
        const start = sentences[i]!.start;
        const end = sentences[j - 1]!.end;
        flagged.push({
          range: rangeFor(model, start, end),
          text: model.text.slice(start, end),
          result: match.result,
          tier: match.tier,
          contentHash: match.hash,
          sentenceHashes: [...new Set(sentences.slice(match.from, match.to).map((s) => s.hash))],
        });
      }
      i = j;
    }
    return flagged;
  }

  get sentenceCount(): number {
    let n = 0;
    for (const b of this.blocks.values()) n += b.sentences.length;
    return n;
  }
}
