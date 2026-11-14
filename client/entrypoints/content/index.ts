import { browser } from 'wxt/browser';
import { cleanUrl } from '@/lib/domain';
import { fingerprintSentences, splitSentences, type Sentence } from '@/lib/fingerprint';
import { BackgroundError, callBackground, type ContentRequest, type PageStatus } from '@/lib/messages';
import { getSettings, onSettingsChanged } from '@/lib/settings';
import { INDEX_REFRESH_MS } from '@/lib/api';
import type { Helpfulness, Narrative, Settings } from '@/lib/types';
import { Highlighter, highlightSupported, type FlaggedRange } from './highlighter';
import { PageScanner } from './scanner';
import { blockFor, buildModel, offsetsOf, textNodesUnder } from './textModel';
import { OverlayUi, type CardActions } from './ui';

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
    // Remote lookups answer from the backend at the time; the local index pushes its changes instead.
    const resultAge = (s: Settings) => (s.backendMode === 'remote' ? INDEX_REFRESH_MS : Infinity);
    scanner.maxResultAge = resultAge(settings);
    let backendKey = `${settings.backendMode}|${settings.backendUrl}`;
    onSettingsChanged((s) => {
      highlighter.setEnabled(s.highlight);
      scanner.maxResultAge = resultAge(s);
      const key = `${s.backendMode}|${s.backendUrl}`;
      if (key !== backendKey) {
        backendKey = key;
        void scanner.refresh();
      }
    });

    scanner.start();
    const notes = new NoteFlow(ui, scanner);
    setupSelection(ui, notes);
    setupCards(ui, highlighter, notes);

    browser.runtime.onMessage.addListener((msg: any, _sender, sendResponse: (r: unknown) => void) => {
      const req = msg as ContentRequest;
      if (req?.target !== 'content') return false;
      if (req.type === 'pageStatus') {
        const status: PageStatus = {
          scannedSentences: scanner.sentenceCount,
          highlightSupported,
          matches: highlighter.all().map((f) => ({
            text: f.text,
            style: f.style,
            matchLevel: f.result.matchLevel,
            narrative: f.result.narrative,
          })),
        };
        sendResponse(status);
      } else if (req.type === 'rescan') {
        void scanner.refresh().then(() => sendResponse(null));
        return true;
      } else if (req.type === 'indexChanged') {
        if (req.added || scanner.shows(req.narrativeIds)) void scanner.refresh();
      }
      return false;
    });
  },
});

/** Sentences (snapped to full sentence boundaries) that the range touches, across blocks. */
function sentencesInRange(range: Range): Sentence[] {
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

const message = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** Writing a note on a passage: sign-in first, then the form, then the posted note. */
class NoteFlow {
  constructor(
    private ui: OverlayUi,
    private scanner: PageScanner,
  ) {}

  private async open(range: Range) {
    const sentences = sentencesInRange(range);
    if (!sentences.length) {
      this.ui.hideAddButton();
      this.ui.toast('Select at least one full sentence of 5 or more words.', 'error');
      return;
    }
    const anchor = range.getBoundingClientRect();
    let verified: boolean;
    try {
      verified = (await callBackground('me', null)).verified;
    } catch (err) {
      this.ui.toast(`Could not check your account: ${message(err)}`, 'error');
      return;
    }
    if (!verified) {
      this.ui.openSignIn(anchor, async (action, credentials) => {
        await callBackground(action, credentials);
        await this.open(range);
      });
      return;
    }
    const text = sentences.map((s) => s.text).join(' ');
    const { contentHash, sentenceHashes } = await fingerprintSentences(sentences);
    this.ui.openNoteForm(anchor, text, async (note) => {
      try {
        await callBackground('addNote', {
          ...note,
          passage: { text, url: cleanUrl(location.href), contentHash, sentenceHashes },
        });
      } catch (err) {
        if (err instanceof BackgroundError && err.status === 403) throw new Error('Sign in from the HashTrust popup first.');
        throw err;
      }
      window.getSelection()?.removeAllRanges();
      this.ui.toast('Note submitted. It is shown once it passes review and readers rate it.');
      await this.scanner.refresh([contentHash, ...sentenceHashes]);
    });
  }

  /** Opens the note form, or the sign-in prompt, for the sentences the range touches. */
  openFor(range: Range) {
    void this.open(range).catch((e) => console.warn('[hashtrust] opening the note form failed', e));
  }
}

function setupSelection(ui: OverlayUi, notes: NoteFlow) {
  let pending: Range | null = null;

  const onSelectionDone = (e: Event) => {
    if (ui.owns(e) || ui.panelOpen) return;
    // Let the browser finalize the selection first.
    setTimeout(() => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || sel.rangeCount === 0 || sel.toString().trim().length < 10) {
        pending = null;
        ui.hideAddButton();
        return;
      }
      pending = sel.getRangeAt(0).cloneRange();
      const rects = pending.getClientRects();
      const rect = rects[rects.length - 1] ?? pending.getBoundingClientRect();
      ui.showAddButton(rect, () => {
        if (pending) notes.openFor(pending);
      });
    }, 0);
  };

  document.addEventListener('mouseup', onSelectionDone);
  document.addEventListener('keyup', (e) => {
    if (e.key === 'Escape') {
      ui.closePanel();
      ui.hideAddButton();
    } else if (e.shiftKey || e.key === 'Shift') onSelectionDone(e);
  });
  document.addEventListener('mousedown', (e) => {
    if (ui.owns(e)) return;
    ui.hideAddButton();
    if (ui.panelOpen) ui.closePanel();
  });
  window.addEventListener('scroll', () => ui.hideAddButton(), { passive: true });
}

/**
 * Hovering a highlight shows its card; clicking one pins it, fetches the
 * narrative's current notes and offers rating, reporting and a new note.
 * Escape, a click elsewhere or scrolling unpins it.
 */
function setupCards(ui: OverlayUi, highlighter: Highlighter, notes: NoteFlow) {
  let frame = 0;
  let myRatings: Record<string, Helpfulness> = {};
  const loadRatings = () =>
    callBackground('myRatings', null).then(
      (r) => (myRatings = r),
      () => myRatings,
    );
  void loadRatings();

  const actionsFor = (item: FlaggedRange, refresh: () => Promise<void>): CardActions => ({
    async rate(noteId, helpfulness) {
      try {
        await callBackground('rateNote', { noteId, helpfulness });
        myRatings = { ...myRatings, [noteId]: helpfulness };
      } catch (err) {
        ui.toast(`Could not save your rating: ${message(err)}`, 'error');
        throw err;
      }
      void refresh();
    },
    async report(noteId, reason) {
      try {
        await callBackground('reportNote', { noteId, reason });
      } catch (err) {
        ui.toast(`Could not report the note: ${message(err)}`, 'error');
        throw err;
      }
    },
    addNote() {
      ui.hideCard();
      notes.openFor(item.range);
    },
  });

  const view = (item: FlaggedRange, narrative: Narrative = item.result.narrative) => ({
    narrative,
    matchLevel: item.result.matchLevel,
    similarity: item.result.similarity,
  });

  const pin = (x: number, y: number, item: FlaggedRange) => {
    const refresh = async () => {
      try {
        const detail = await callBackground('narrative', item.result.narrative.narrativeId);
        ui.updateCard(view(item, detail), myRatings, actions);
      } catch (e) {
        console.warn('[hashtrust] loading the narrative failed', e);
      }
    };
    const actions = actionsFor(item, refresh);
    ui.showCard(x, y, view(item), true, myRatings, actions);
    void loadRatings().then(refresh);
  };

  document.addEventListener(
    'mousemove',
    (e) => {
      if (frame || ui.cardPinned) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (ui.cardPinned) return; // pinned by a click since the move
        const hit = highlighter.hitTest(e.clientX, e.clientY);
        if (hit) ui.showCard(e.clientX, e.clientY, view(hit), false, myRatings);
        else ui.hideCard();
      });
    },
    { passive: true },
  );
  document.addEventListener('click', (e) => {
    if (ui.owns(e)) return;
    const hit = window.getSelection()?.isCollapsed !== false ? highlighter.hitTest(e.clientX, e.clientY) : undefined;
    if (hit) pin(e.clientX, e.clientY, hit);
    else if (ui.cardPinned) ui.hideCard();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && ui.cardPinned) ui.hideCard();
  });
  window.addEventListener('scroll', () => ui.hideCard(), { passive: true });
}
