/**
 * Maps page DOM to plain-text "blocks" so that sentences can be hashed and
 * mapped back to DOM Ranges. A block is the nearest non-inline ancestor of a
 * text node; its text is the concatenation of its own text nodes.
 *
 * Notes and lookup use this same model, so a sentence hashes identically
 * whether it is written on or scanned.
 */

const SKIP_TAGS = new Set([
  'script', 'style', 'noscript', 'template', 'textarea', 'input', 'select', 'option',
  'code', 'pre', 'svg', 'canvas', 'iframe', 'object', 'head', 'nav',
]);

export const UI_HOST_TAG = 'hashtrust-ui';

const blockCache = new WeakMap<Element, boolean>();

function isBlockElement(el: Element): boolean {
  let cached = blockCache.get(el);
  if (cached === undefined) {
    const display = getComputedStyle(el).display;
    cached = !(display.startsWith('inline') || display === 'contents');
    blockCache.set(el, cached);
  }
  return cached;
}

export function blockFor(node: Node): Element {
  let el = node instanceof Element ? node : node.parentElement;
  while (el && el !== document.body && !isBlockElement(el)) el = el.parentElement;
  return el ?? document.body;
}

function isSkipped(el: Element): boolean {
  return SKIP_TAGS.has(el.localName) || el.localName === UI_HOST_TAG || (el as HTMLElement).isContentEditable;
}

/** Visible-ish text nodes under root, skipping code, inputs, editors and our own UI. */
export function textNodesUnder(root: Node): Text[] {
  const out: Text[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (node.nodeType === Node.ELEMENT_NODE) {
        return isSkipped(node as Element) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP;
      }
      return /\S/.test((node as Text).data) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
    },
  });
  while (walker.nextNode()) out.push(walker.currentNode as Text);
  return out;
}

/** Group text nodes under the given roots by block. Every block returned is complete. */
export function collectBlocks(roots: Iterable<Node>): Map<Element, Text[]> {
  const blocks = new Map<Element, Text[]>();
  const seenRoots = new Set<Element>();
  for (const root of roots) {
    if (!root.isConnected) continue;
    // Widen to the enclosing block so that partial subtrees yield complete blocks.
    const start = blockFor(root);
    if (seenRoots.has(start)) continue;
    seenRoots.add(start);
    for (const t of textNodesUnder(start)) {
      const b = blockFor(t);
      const list = blocks.get(b);
      if (list) {
        if (!list.includes(t)) list.push(t);
      } else blocks.set(b, [t]);
    }
  }
  // Nested roots may have been visited out of order; restore document order.
  for (const list of blocks.values()) {
    list.sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
  }
  return blocks;
}

export interface BlockModel {
  block: Element;
  nodes: Text[];
  starts: number[];
  text: string;
}

export function buildModel(block: Element, nodes?: Text[]): BlockModel {
  const own = nodes ?? textNodesUnder(block).filter((t) => blockFor(t) === block);
  const starts: number[] = [];
  let text = '';
  for (const n of own) {
    starts.push(text.length);
    text += n.data;
  }
  return { block, nodes: own, starts, text };
}

function locate(model: BlockModel, offset: number, preferEnd: boolean): [Text, number] {
  for (const [i, node] of model.nodes.entries()) {
    const start = model.starts[i]!;
    const end = start + node.data.length;
    if (preferEnd ? offset > start && offset <= end : offset >= start && offset < end) {
      return [node, offset - start];
    }
  }
  const last = model.nodes.at(-1)!;
  return [last, last.data.length];
}

/** DOM Range for text offsets [start, end) within a block model. */
export function rangeFor(model: BlockModel, start: number, end: number): Range {
  const range = document.createRange();
  const [sn, so] = locate(model, start, false);
  const [en, eo] = locate(model, end, true);
  range.setStart(sn, so);
  range.setEnd(en, eo);
  return range;
}

/** Offsets [start, end) of a DOM Range within a block model, or null if they don't overlap. */
export function offsetsOf(model: BlockModel, range: Range): [number, number] | null {
  let lo = Infinity;
  let hi = -Infinity;
  model.nodes.forEach((node, i) => {
    if (!range.intersectsNode(node)) return;
    const s = node === range.startContainer ? range.startOffset : 0;
    const e = node === range.endContainer ? range.endOffset : node.data.length;
    if (e <= s) return;
    lo = Math.min(lo, model.starts[i]! + s);
    hi = Math.max(hi, model.starts[i]! + e);
  });
  return lo < hi ? [lo, hi] : null;
}
