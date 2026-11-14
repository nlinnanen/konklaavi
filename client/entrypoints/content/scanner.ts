import type { LookupBlock, NarrativeBlock } from '@/lib/api';
import { embeddingUnits } from '@/lib/embed';
import { fingerprintSentences, hashNormalized, splitSentences, type Sentence } from '@/lib/fingerprint';
import { callBackground, ContextInvalidatedError } from '@/lib/messages';
import { bands, minhash } from '@/lib/minhash';
import { normalizeText } from '@/lib/normalize';
import { highlightStyle, matchRank } from '@/lib/state';
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

/** A shown match over sentences `from` to `to` (exclusive) of a block, matched by `hash`. */
interface Match {
  from: number;
  to: number;
  hash: string;
  result: LookupResult;
  rank: number;
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
/** Main-thread work per slice before the scanner lets the page handle input. */
const SLICE_MS = 8;

/** Resolves at once while the current slice lasts, else after the page has had a turn. */
function slicer(): () => Promise<void> {
  let start = performance.now();
  return async () => {
    if (performance.now() - start < SLICE_MS) return;
    await new Promise((resolve) => setTimeout(resolve, 0));
    start = performance.now();
  };
}

/** Resolves when the page is idle, so on-device embedding never competes with the page's own work. */
function idle(): Promise<void> {
  return new Promise((resolve) =>
    'requestIdleCallback' in window ? requestIdleCallback(() => resolve(), { timeout: 2000 }) : setTimeout(resolve, 50),
  );
}

/**
 * Splits page text into sentences, hashes them, looks the hashes up via the
 * background worker and hands matched sentences to the highlighter.
 */
export class PageScanner {
  private blocks = new Map<Element, ScannedBlock>();
  /** hash -> result, or null when it belongs to no narrative. */
  private results = new Map<string, LookupResult | null>();
  /** Unit hash -> narrative-level or related result, or null when the unit matched nothing. */
  private unitResults = new Map<string, LookupResult | null>();
  private observer?: MutationObserver;
  private dirty = new Set<Node>();
  private timer?: ReturnType<typeof setTimeout>;
  private running: Promise<void> = Promise.resolve();
  private stopped = false;
  /** When the cached results were last cleared. */
  private resultsSince = Date.now();
  /** How old the cached results get before the next scan pass forgets them and looks every block up again. */
  maxResultAge = Infinity;

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

  /** Runs passes one after another, so they don't interleave. */
  private enqueue(pass: () => Promise<void>): Promise<void> {
    this.running = this.running.then(() => (this.stopped ? undefined : pass())).catch((e) => this.failed('scan', e));
    return this.running;
  }

  /** Scan (or rescan) the blocks containing the given roots. */
  scan(roots: Iterable<Node>): Promise<void> {
    const list = [...roots];
    return this.enqueue(() => this.doScan(list));
  }

  /**
   * Forgets the results of the given hashes, or all of them (after a note or a
   * backend switch), and looks the scanned blocks up again. Fingerprints are
   * kept, since the text they come from has not changed.
   */
  refresh(hashes?: string[]): Promise<void> {
    if (hashes) {
      for (const h of hashes) {
        this.results.delete(h);
        this.unitResults.delete(h);
      }
    } else {
      this.forgetResults();
    }
    return this.enqueue(() => this.lookupBlocks([...this.blocks.values()]));
  }

  private forgetResults() {
    this.results.clear();
    this.unitResults.clear();
    this.resultsSince = Date.now();
  }

  /** True when one of the narratives is matched on this page. */
  shows(narrativeIds: Iterable<string>): boolean {
    const shown = new Set<string>();
    for (const r of [...this.results.values(), ...this.unitResults.values()]) if (r) shown.add(r.narrative.narrativeId);
    for (const id of narrativeIds) if (shown.has(id)) return true;
    return false;
  }

  private async doScan(roots: Node[]) {
    const stale = Date.now() - this.resultsSince > this.maxResultAge;
    if (stale) this.forgetResults();
    const touched: ScannedBlock[] = [];
    let budget = MAX_SENTENCES_PER_PASS;
    const pause = slicer();
    for (const [block, nodes] of collectBlocks(roots)) {
      if (budget <= 0) break;
      await pause();
      const model = buildModel(block, nodes);
      const prev = this.blocks.get(block);
      if (prev && prev.model.text === model.text) continue;
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
    await this.lookupBlocks(stale ? [...this.blocks.values()] : touched);
  }

  /** Looks up the blocks' hashes without a cached result and repaints them. */
  private async lookupBlocks(touched: ScannedBlock[]) {
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

    const pause = slicer();
    for (const block of touched) {
      await pause();
      this.highlighter.setBlock(block.model.block, this.flaggedRanges(block));
    }
    this.matchNarratives(touched).catch((e) => this.failed('narrative matching', e));
  }

  /**
   * Embeds the units of blocks that nothing matched as a variant, leaving out
   * units with an exactly matched sentence, and matches them against
   * narratives. It runs after the exact and variant highlights are painted,
   * since the first embedding waits for the model to download, and a failure
   * leaves those highlights alone. Each batch waits for the page to be idle.
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
      await idle();
      if (this.stopped) return;
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
   * sentence takes the strongest match (`matchRank`) among its exact match,
   * the block's variant match and the units that cover it, the smallest of
   * those on a tie.
   */
  private flaggedRanges({ model, sentences, fingerprint, units }: ScannedBlock): FlaggedRange[] {
    const matches: Match[] = [];
    const add = (from: number, to: number, hash: string, result: LookupResult | null | undefined) => {
      if (result) matches.push({ from, to, hash, result, rank: matchRank(result.matchLevel, result.narrative.state) });
    };
    sentences.forEach((s, i) => add(i, i + 1, s.hash, this.results.get(s.hash)));
    if (fingerprint) add(0, sentences.length, fingerprint.hash, this.results.get(fingerprint.hash));
    for (const u of units) add(u.from, u.to, u.hash, this.unitResults.get(u.hash));
    if (!matches.length) return [];

    const stronger = (a: Match, b: Match | undefined) =>
      !b || a.rank > b.rank || (a.rank === b.rank && a.to - a.from < b.to - b.from);
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
          style: highlightStyle(match.result.narrative.state, match.result.matchLevel),
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
