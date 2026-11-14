import { browser } from 'wxt/browser';
import { cleanUrl, domainOf } from '@/lib/domain';
import { fingerprintSentences, splitSentences, type Sentence } from '@/lib/fingerprint';
import { callBackground, displayVerdict, type ContentRequest, type PageStatus } from '@/lib/messages';
import { getSettings, onSettingsChanged } from '@/lib/settings';
import { Highlighter, highlightSupported, type FlaggedRange } from './highlighter';
import { PageScanner } from './scanner';
import { OverlayUi, type FeedbackVerdict } from './ui';
import { blockFor, buildModel, offsetsOf, textNodesUnder } from './textModel';

export default defineContentScript({
  matches: ['<all_urls>'],
  runAt: 'document_idle',
  async main() {
    if (!document.body) return;
    // Fingerprints are SHA-256 from crypto.subtle, which only secure contexts (https, localhost) have.
    if (!globalThis.crypto?.subtle) return;

    const ui = new OverlayUi();
    const highlighter = new Highlighter();
    const scanner = new PageScanner(highlighter);

    const settings = await getSettings();
    highlighter.setEnabled(settings.highlight);
    let backendKey = `${settings.backendMode}|${settings.backendUrl}`;
    onSettingsChanged((s) => {
      highlighter.setEnabled(s.highlight);
      const key = `${s.backendMode}|${s.backendUrl}`;
      if (key !== backendKey) {
        backendKey = key;
        void scanner.refresh();
      }
    });

    scanner.start();
    setupSelectionVoting(ui, scanner);
    setupTooltips(ui, highlighter, scanner);

    browser.runtime.onMessage.addListener((msg: any, _sender, sendResponse: (r: unknown) => void) => {
      const req = msg as ContentRequest;
      if (req?.target !== 'content') return false;
      if (req.type === 'pageStatus') {
        const status: PageStatus = {
          domain: domainOf(location.hostname),
          scannedSentences: scanner.sentenceCount,
          highlightSupported,
          flagged: highlighter.all().map((f) => ({
            text: f.text,
            verdict: displayVerdict(f.result),
            tier: f.tier,
            total: f.result.total,
            signature: f.result.signature,
          })),
        };
        sendResponse(status);
      } else if (req.type === 'rescan') {
        void scanner.refresh().then(() => sendResponse(null));
        return true;
      }
      return false;
    });
  },
});

/** Sentences (snapped to full sentence boundaries) that the selection touches, across blocks. */
function sentencesInSelection(range: Range): Sentence[] {
  const container = range.commonAncestorContainer;
  const blocks = new Set<Element>();
  for (const t of textNodesUnder(blockFor(container))) {
    if (range.intersectsNode(t)) blocks.add(blockFor(t));
  }
  const out: Sentence[] = [];
  for (const block of blocks) {
    const model = buildModel(block);
    const offsets = offsetsOf(model, range);
    if (!offsets) continue;
    const [lo, hi] = offsets;
    out.push(...splitSentences(model.text).filter((s) => s.start < hi && s.end > lo));
  }
  return out;
}

function setupSelectionVoting(ui: OverlayUi, scanner: PageScanner) {
  let pending: Range | null = null;

  const onSelectionDone = (e: Event) => {
    if (ui.owns(e) || ui.pickerOpen) return;
    // Let the browser finalize the selection first.
    setTimeout(() => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || sel.rangeCount === 0 || sel.toString().trim().length < 10) {
        pending = null;
        ui.hideRateButton();
        return;
      }
      pending = sel.getRangeAt(0).cloneRange();
      const rects = pending.getClientRects();
      const rect = rects[rects.length - 1] ?? pending.getBoundingClientRect();
      ui.showRateButton(rect, () => {
        openPicker().catch((e) => console.warn('[hashtrust] opening the rating picker failed', e));
      });
    }, 0);
  };

  const openPicker = async () => {
    if (!pending) return;
    const range = pending;
    const sentences = sentencesInSelection(range);
    if (!sentences.length) {
      ui.hideRateButton();
      ui.toast('Select at least one full sentence (5+ words) to rate.', 'error');
      return;
    }
    const excerpt = sentences.map((s) => s.text).join(' ');
    const { contentHash, sentenceHashes } = await fingerprintSentences(sentences);

    ui.openPicker(range.getBoundingClientRect(), excerpt, async ({ verdict, comment }) => {
      try {
        await callBackground('vote', {
          contentHash,
          sentenceHashes,
          verdict,
          comment,
          excerpt: excerpt.slice(0, 2000),
          url: cleanUrl(location.href),
          domain: domainOf(location.hostname),
        });
        ui.toast('Thanks! Your vote was recorded.');
        window.getSelection()?.removeAllRanges();
        await scanner.refresh(sentenceHashes);
      } catch (err) {
        ui.toast(`Could not submit vote: ${err instanceof Error ? err.message : err}`, 'error');
        throw err;
      }
    });
  };

  document.addEventListener('mouseup', onSelectionDone);
  document.addEventListener('keyup', (e) => {
    if (e.key === 'Escape') {
      ui.closePicker();
      ui.hideRateButton();
    } else if (e.shiftKey || e.key === 'Shift') onSelectionDone(e);
  });
  document.addEventListener('mousedown', (e) => {
    if (ui.owns(e)) return;
    ui.hideRateButton();
    if (ui.pickerOpen) ui.closePicker();
  });
  window.addEventListener('scroll', () => ui.hideRateButton(), { passive: true });
}

/**
 * Votes on a highlight from its tooltip, about the matched sentence, block or
 * unit, without its text, then rechecks that content.
 */
async function voteOn(ui: OverlayUi, scanner: PageScanner, item: FlaggedRange, verdict: FeedbackVerdict) {
  try {
    await callBackground('vote', {
      contentHash: item.contentHash,
      sentenceHashes: item.sentenceHashes,
      verdict,
      url: cleanUrl(location.href),
      domain: domainOf(location.hostname),
      narrativeId: item.result.signature?.narrativeId,
      matchLevel: item.result.signature?.matchLevel,
    });
  } catch (err) {
    ui.toast(`Could not submit vote: ${err instanceof Error ? err.message : err}`, 'error');
    throw err;
  }
  ui.toast('Thanks! Your vote was recorded.');
  void scanner.refresh([item.contentHash, ...item.sentenceHashes]);
}

/**
 * Hovering a highlight shows its tooltip; clicking one pins the tooltip so its
 * source link and feedback buttons can be clicked. Escape, a click elsewhere
 * or scrolling unpins it.
 */
function setupTooltips(ui: OverlayUi, highlighter: Highlighter, scanner: PageScanner) {
  let frame = 0;
  let pinned = false;
  const unpin = () => {
    pinned = false;
    ui.hideTooltip();
  };
  const show = (x: number, y: number, item: FlaggedRange, pin: boolean) =>
    ui.showTooltip(x, y, item, pin, (verdict) => voteOn(ui, scanner, item, verdict));

  document.addEventListener(
    'mousemove',
    (e) => {
      if (frame || pinned) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const hit = highlighter.hitTest(e.clientX, e.clientY);
        if (hit) show(e.clientX, e.clientY, hit, false);
        else ui.hideTooltip();
      });
    },
    { passive: true },
  );
  document.addEventListener('click', (e) => {
    if (ui.owns(e)) return;
    const hit = window.getSelection()?.isCollapsed !== false ? highlighter.hitTest(e.clientX, e.clientY) : undefined;
    if (hit) {
      pinned = true;
      show(e.clientX, e.clientY, hit, true);
    } else if (pinned) unpin();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && pinned) unpin();
  });
  window.addEventListener('scroll', unpin, { passive: true });
}
