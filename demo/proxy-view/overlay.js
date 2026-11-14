// Injected into the proxied page: sends its main text to /check and highlights the matches.
(() => {
  const cfg = JSON.parse(document.getElementById('hashtrust-data').textContent);

  // Same states, labels and highlight styles as client/lib/state.ts.
  const STATE_LABEL = { misleading: 'Misleading', context: 'Context added', controversial: 'Raters disagree', needs_ratings: 'Needs ratings', tracked: 'Tracked' };
  const STATE_COLOR = { misleading: 'oklch(0.7 0.19 35)', context: 'oklch(0.74 0.11 205)', controversial: 'oklch(0.72 0.015 260)', needs_ratings: 'oklch(0.82 0.11 85)', tracked: 'oklch(0.7 0.03 260)' };
  const STYLE = {
    misleading: 'background-color:oklch(0.68 0.19 35 / 0.36);text-decoration:underline solid oklch(0.6 0.2 32);text-decoration-thickness:2px;',
    'misleading-weak': 'background-color:oklch(0.68 0.19 35 / 0.14);text-decoration:underline dotted oklch(0.6 0.2 32 / 0.8);text-decoration-thickness:2px;',
    context: 'background-color:oklch(0.74 0.1 200 / 0.24);text-decoration:underline solid oklch(0.6 0.11 205);text-decoration-thickness:2px;',
    'context-weak': 'background-color:oklch(0.74 0.1 200 / 0.1);text-decoration:underline dotted oklch(0.6 0.11 205 / 0.8);text-decoration-thickness:2px;',
    controversial: 'background-color:oklch(0.6 0.015 260 / 0.26);',
    'controversial-weak': 'background-color:oklch(0.6 0.015 260 / 0.12);',
    needs_ratings: 'background-color:oklch(0.82 0.09 85 / 0.2);',
    'needs_ratings-weak': 'background-color:oklch(0.82 0.09 85 / 0.1);',
    tracked: 'background:none;text-decoration:underline dotted oklch(0.58 0.03 260 / 0.75);text-decoration-thickness:1.5px;',
    'tracked-weak': 'background:none;text-decoration:underline dotted oklch(0.58 0.03 260 / 0.4);text-decoration-thickness:1px;',
  };
  const important = (css) => css.replace(/;/g, '!important;');
  const isWeak = (level) => level === 'narrative' || level === 'related';
  const styleOf = (result) => (isWeak(result.matchLevel) ? `${result.narrative.state}-weak` : result.narrative.state);

  /** The helpful note a misleading or context narrative is shown with, else its first visible note. */
  function leadNote(n) {
    const wanted = { misleading: 'misleading', context: 'not_misleading' }[n.state];
    return n.notes.find((x) => x.status === 'helpful' && x.classification === wanted) || n.notes[0];
  }
  function spreadLine(spread) {
    if (!spread) return '';
    const sites = `${spread.sites} site${spread.sites === 1 ? '' : 's'}`;
    if (!spread.firstSeen) return `Tracked on ${sites}`;
    return `Seen on ${sites} since ${new Date(spread.firstSeen).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}`;
  }
  function levelLine(result) {
    const sim = result.similarity != null ? ` (${Math.round(result.similarity * 100)}% similar)` : '';
    return `${result.matchLevel} match${sim}`;
  }

  const style = document.createElement('style');
  style.textContent =
    'mark.ht-mark{color:inherit;cursor:help;border-radius:2px;padding:0 1px;}' +
    Object.entries(STYLE).map(([k, css]) => `mark.ht-${k}{${important(css)}}`).join('') +
    'mark.ht-flash{outline:3px solid #2563eb!important;outline-offset:2px;}';
  document.head.append(style);

  // ---- Panel and tooltip live in a shadow root so site CSS can't touch them.
  // In the /view shell (same origin) the panel goes in the parent, beside the frame.
  const shell = window !== window.top ? window.top.document : null;
  const doc = shell || document;
  doc.getElementById('ht-host')?.remove();
  doc.getElementById('ht-loading')?.remove();
  if (shell) shell.title = document.title || cfg.host;
  const host = doc.createElement('div');
  host.id = 'ht-host';
  host.style.cssText = 'all:initial;position:fixed;top:0;right:0;bottom:0;width:340px;z-index:2147483647;';
  doc.body.append(host);
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `<style>
    :host{all:initial}
    *{box-sizing:border-box}
    .panel{position:absolute;inset:0;background:#0f172a;color:#e2e8f0;font:14px/1.45 system-ui,sans-serif;display:flex;flex-direction:column;border-left:1px solid #1e293b}
    header{padding:14px 16px;border-bottom:1px solid #1e293b}
    h1{font-size:15px;margin:0 0 6px;font-weight:650;letter-spacing:.01em}
    h1 span{color:#94a3b8;font-weight:400}
    form{display:flex;gap:6px;margin-top:8px}
    input{flex:1;min-width:0;background:#1e293b;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:6px 8px;font:inherit;font-size:12px}
    button{background:#2563eb;color:#fff;border:0;border-radius:6px;padding:6px 10px;font:inherit;font-size:12px;cursor:pointer}
    .status{font-size:12px;color:#94a3b8;margin-top:8px}
    .status b{color:#e2e8f0}
    .list{overflow:auto;flex:1;padding:8px}
    .item{background:#1e293b;border-radius:8px;padding:10px 12px;margin:0 0 8px;cursor:pointer;border-left:4px solid}
    .item:hover{background:#273449}
    .tier{font-size:11px;text-transform:uppercase;letter-spacing:.06em;font-weight:650}
    .title{font-weight:600;margin:2px 0 4px}
    .quote{color:#cbd5e1;font-size:12px;font-style:italic}
    .miss{color:#f59e0b;font-size:11px;margin-top:4px}
    .empty{color:#94a3b8;padding:12px 8px}
    .err{color:#fca5a5}
    .toggle{float:right;background:#1e293b;padding:2px 9px;font-size:14px;margin:-2px -4px 0 0}
    :host(.collapsed) .panel header>*:not(.toggle),:host(.collapsed) .list{display:none}
    :host(.collapsed) .toggle{float:none;margin:0}
    :host(.collapsed) header{padding:10px 6px;border:0}
    .tip{position:fixed;max-width:360px;background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:8px;padding:10px 12px;font:13px/1.45 system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.35);pointer-events:none;display:none}
    .tip p{margin:4px 0 0;color:#cbd5e1}
    .tip a{color:#93c5fd}
    .src{font-size:11px;color:#94a3b8;margin-top:6px}
  </style>
  <div class="panel">
    <header>
      <button class="toggle" title="Collapse panel">»</button>
      <h1>HashTrust <span>proxy view</span></h1>
      <form target="_top"><input name="url" type="url" required><button>Scan</button></form>
      <div class="status"></div>
    </header>
    <div class="list"></div>
  </div>
  <div class="tip"></div>`;
  const $ = (s) => root.querySelector(s);
  $('input').value = cfg.url;
  $('form').action = cfg.proxy + 'view';
  $('.toggle').addEventListener('click', () => {
    const collapsed = host.classList.toggle('collapsed');
    host.style.width = collapsed ? '44px' : '340px';
    doc.body.classList.toggle('ht-collapsed', collapsed);
    $('.toggle').textContent = collapsed ? '«' : '»';
  });
  const statusEl = $('.status');
  const listEl = $('.list');
  const tip = $('.tip');
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

  // ---- Find text in the DOM across element boundaries. Whitespace is ignored entirely, since
  // extracted text adds spaces at line breaks and block edges that the DOM may not have.
  function textIndex() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) =>
        n.parentElement && !n.parentElement.closest('style,script,noscript,template,textarea') && host !== n.parentElement
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT,
    });
    let flat = '';
    const map = []; // flat index -> [node, offset]
    for (let n; (n = walker.nextNode()); ) {
      const t = n.nodeValue;
      for (let i = 0; i < t.length; i++) {
        if (/\s/.test(t[i])) continue;
        flat += t[i];
        map.push([n, i]);
      }
    }
    return { flat, lower: flat.toLowerCase(), map };
  }
  const norm = (s) => s.replace(/\s+/g, '');
  const fold = (s) => s.replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/[–—]/g, '-');

  function wrap(idx, start, end, style, id) {
    const range = document.createRange();
    const [sn, so] = idx.map[start];
    const [en, eo] = idx.map[end - 1];
    range.setStart(sn, so);
    range.setEnd(en, eo + 1);
    // Wrap each text node piece separately so the page structure stays intact.
    const nodes = [];
    const walker = document.createTreeWalker(range.commonAncestorContainer.nodeType === 3 ? range.commonAncestorContainer.parentNode : range.commonAncestorContainer, NodeFilter.SHOW_TEXT);
    for (let n; (n = walker.nextNode()); ) if (range.intersectsNode(n)) nodes.push(n);
    const marks = [];
    for (const n of nodes) {
      let node = n;
      const s = node === sn ? so : 0;
      const e = node === en ? eo + 1 : node.nodeValue.length;
      // A stronger match already highlights this text: keep it, so highlights never nest.
      if (!node.nodeValue.slice(s, e).trim() || node.parentElement.closest('mark.ht-mark')) continue;
      if (e < node.nodeValue.length) node.splitText(e);
      if (s > 0) node = node.splitText(s);
      const mark = document.createElement('mark');
      mark.className = `ht-mark ht-${style}`;
      mark.dataset.ht = id;
      node.parentNode.insertBefore(mark, node);
      mark.append(node);
      marks.push(mark);
    }
    return marks;
  }

  function locate(text) {
    // Re-index each time: earlier wraps split text nodes.
    const idx = textIndex();
    const needle = norm(text);
    let at = idx.flat.indexOf(needle);
    if (at < 0) at = fold(idx.lower).indexOf(fold(needle.toLowerCase()));
    return at < 0 ? null : { idx, start: at, end: at + needle.length };
  }

  // ---- Run the scan.
  const items = [];
  const groups = new Map(); // narrativeId -> { narrative, best, items }
  statusEl.innerHTML = cfg.blocks.length
    ? `Extracted <b>${cfg.blocks.length}</b> text blocks from <b>${esc(cfg.host)}</b> (${esc(cfg.mode)}). Checking…`
    : `<span class="err">No article text extracted from ${esc(cfg.host)}: the content is probably rendered by JavaScript.</span>`;
  if (!cfg.blocks.length) return;

  const t0 = performance.now();
  fetch(cfg.proxy + 'check', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: cfg.blocks.join('\n\n') }) })
    .then(async (r) => {
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
      return data;
    })
    .then(({ matches }) => {
      if (cfg.test) matches = matches.concat(testMatches());
      // Strongest first, so where matches overlap the stronger one is highlighted.
      const LEVEL_RANK = { exact: 3, variant: 3, narrative: 2, related: 1 };
      matches = matches
        .filter((m) => m.result?.narrative)
        .sort((a, b) => (LEVEL_RANK[b.result.matchLevel] || 0) - (LEVEL_RANK[a.result.matchLevel] || 0) || (b.result.similarity ?? 1) - (a.result.similarity ?? 1));
      const seen = new Set();
      for (const m of matches) {
        if (seen.has(m.text)) continue;
        seen.add(m.text);
        const id = String(items.length);
        const hit = locate(m.text);
        const marks = hit ? wrap(hit.idx, hit.start, hit.end, styleOf(m.result), id) : [];
        const n = m.result.narrative;
        const item = { id, state: n.state, text: m.text, narrative: n, result: m.result, marks, located: !!hit };
        items.push(item);
        if (!groups.has(n.narrativeId)) groups.set(n.narrativeId, { narrative: n, best: item, items: [] });
        groups.get(n.narrativeId).items.push(item);
      }
      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      const missing = items.filter((i) => !i.located).length;
      statusEl.innerHTML = `<b>${cfg.blocks.length}</b> blocks (${esc(cfg.mode)}) checked in ${secs}s · <b>${groups.size}</b> narrative${groups.size === 1 ? '' : 's'}, <b>${items.length}</b> passage${items.length === 1 ? '' : 's'}` +
        (missing ? ` (${missing} not located in page)` : '');
      render();
    })
    .catch((e) => {
      statusEl.innerHTML = `<span class="err">Check failed: ${esc(e.message)}</span>`;
    });

  // ?test=1: fake matches on real sentences, to check highlighting on a site without a real hit.
  function testMatches() {
    let sentences = cfg.blocks.flatMap((b) => b.split(/(?<=[.!?])\s+/)).map((s) => s.trim()).filter((s) => s.length > 60);
    if (sentences.length < 3) sentences.push(...cfg.blocks.filter((b) => b.length > 30));
    const pick = [sentences[1], sentences[Math.floor(sentences.length / 2)], sentences[sentences.length - 2]].filter(Boolean);
    const cases = [['exact', 'misleading', 'misleading'], ['narrative', 'context', 'not_misleading'], ['related', 'needs_ratings', 'misleading']];
    return pick.map((text, i) => {
      const [matchLevel, state, classification] = cases[i];
      return {
        text,
        result: {
          matchLevel,
          similarity: matchLevel === 'exact' ? undefined : 0.86 - i * 0.05,
          narrative: {
            narrativeId: `test-${i}`,
            title: 'TEST highlight (not a real match)',
            state,
            notes: [{ id: `t${i}`, classification, body: 'Injected by test mode to check that highlighting works on this site.', sources: [], status: state === 'needs_ratings' ? 'needs_more_ratings' : 'helpful', ratings: { helpful: 0, somewhat: 0, not_helpful: 0 }, createdAt: new Date().toISOString() }],
            spread: { sites: 0, firstSeen: '' },
          },
        },
      };
    });
  }

  const clip = (t, max) => (t.length > max ? t.slice(0, max) + '…' : t);

  function render() {
    if (!groups.size) {
      listEl.innerHTML = '<div class="empty">Nothing on this page matches a tracked narrative.</div>';
      return;
    }
    const list = [...groups.values()];
    listEl.innerHTML = list
      .map((g, i) => {
        const n = g.narrative;
        const count = g.items.length;
        return `<div class="item" data-group="${i}" style="border-color:${STATE_COLOR[n.state]}">
        <div class="tier" style="color:${STATE_COLOR[n.state]}">${STATE_LABEL[n.state] || esc(n.state)} · ${esc(levelLine(g.best.result))}</div>
        <div class="title">${esc(clip(n.title, 140))}</div>
        <div class="quote">“${esc(clip(g.best.text, 160))}”</div>
        <div class="src">${count} passage${count === 1 ? '' : 's'} on this page${n.notes.length ? ` · ${n.notes.length} note${n.notes.length === 1 ? '' : 's'}` : ''}</div>
        ${g.items.some((x) => x.located) ? '' : '<div class="miss">Not located in the rendered page</div>'}
      </div>`;
      })
      .join('');
    listEl.querySelectorAll('.item').forEach((el) =>
      el.addEventListener('click', () => {
        const marks = list[+el.dataset.group].items.flatMap((x) => x.marks);
        if (!marks.length) return;
        marks[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
        marks.forEach((m) => m.classList.add('ht-flash'));
        setTimeout(() => marks.forEach((m) => m.classList.remove('ht-flash')), 1600);
      }),
    );
  }

  // ---- Tooltip on hover.
  document.addEventListener('mouseover', (e) => {
    const mark = e.target.closest?.('mark.ht-mark');
    if (!mark) return (tip.style.display = 'none');
    const item = items[+mark.dataset.ht];
    const n = item.narrative;
    const note = leadNote(n);
    tip.innerHTML = `<div class="tier" style="color:${STATE_COLOR[item.state]}">${STATE_LABEL[item.state] || esc(item.state)} · ${esc(levelLine(item.result))}</div>
      <div class="title">${esc(clip(n.title, 200))}</div>
      ${note ? `<p>${note.classification === 'misleading' ? 'Note: misleading. ' : 'Note: '}${esc(clip(note.body, 400))}</p>` : '<p>No notes yet.</p>'}
      ${note?.sources?.length ? `<p>Sources: ${note.sources.map((u) => esc(clip(u, 60))).join(', ')}</p>` : ''}
      <div class="src">${esc(spreadLine(n.spread))}${n.notes.length > 1 ? ` · ${n.notes.length} notes` : ''}</div>`;
    const r = mark.getBoundingClientRect();
    tip.style.display = 'block';
    const left = Math.min(r.left, window.innerWidth - 370);
    tip.style.left = Math.max(8, left) + 'px';
    tip.style.top = (r.bottom + 8 + 200 > window.innerHeight ? Math.max(8, r.top - tip.offsetHeight - 8) : r.bottom + 8) + 'px';
  });
})();
