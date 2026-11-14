// Injected into the proxied page: sends its main text to /check and highlights the matches.
(() => {
  const cfg = JSON.parse(document.getElementById('hashtrust-data').textContent);

  // Same tiers and colours as client/lib/tier.ts.
  const TIER_STYLE = {
    confirmed: 'background:#dc2626!important;color:#fff!important;',
    likely: 'background:rgba(249,115,22,.3)!important;',
    reported: 'background:rgba(168,85,247,.15)!important;',
    related: 'background:none!important;text-decoration:underline dotted rgba(234,179,8,.9)!important;text-decoration-thickness:2px!important;',
    disputed: 'background:rgba(156,163,175,.25)!important;',
  };
  const TIER_LABEL = { confirmed: 'Confirmed', likely: 'Likely', reported: 'Reported', related: 'Related', disputed: 'Disputed' };
  const TIER_COLOR = { confirmed: '#dc2626', likely: '#f97316', reported: '#a855f7', related: '#eab308', disputed: '#9ca3af' };

  function tierOf(result) {
    const s = result.signature;
    if (!s) return result.flagged ? 'reported' : null;
    if (s.state === 'disputed') return 'disputed';
    if (s.state === 'under_review') return s.source === 'report' || result.flagged ? 'reported' : null;
    if (s.matchLevel === 'related') return 'related';
    if (s.matchLevel === 'narrative') return 'likely';
    return s.settled ? 'confirmed' : 'likely';
  }

  const style = document.createElement('style');
  style.textContent =
    'mark.ht-mark{color:inherit;cursor:help;border-radius:2px;padding:0 1px;}' +
    Object.entries(TIER_STYLE).map(([t, css]) => `mark.ht-${t}{${css}}`).join('') +
    'mark.ht-flash{outline:3px solid #2563eb!important;outline-offset:2px;}';
  document.head.append(style);

  // ---- Panel and tooltip live in a shadow root so site CSS can't touch them.
  // In the /view shell (same origin) the panel goes in the parent, beside the frame.
  const shell = window !== window.top ? window.top.document : null;
  const doc = shell || document;
  doc.getElementById('ht-host')?.remove();
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

  // ---- Find text in the DOM across element boundaries, ignoring whitespace differences.
  function textIndex() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) =>
        n.parentElement && !n.parentElement.closest('style,script,noscript,template,textarea') && host !== n.parentElement
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT,
    });
    let flat = '';
    const map = []; // flat index -> [node, offset]
    let lastSpace = true;
    for (let n; (n = walker.nextNode()); ) {
      const t = n.nodeValue;
      for (let i = 0; i < t.length; i++) {
        let c = t[i];
        if (/\s/.test(c)) {
          if (lastSpace) continue;
          c = ' ';
          lastSpace = true;
        } else lastSpace = false;
        flat += c;
        map.push([n, i]);
      }
    }
    return { flat, lower: flat.toLowerCase(), map };
  }
  const norm = (s) => s.replace(/\s+/g, ' ').trim();
  const fold = (s) => s.replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/[–—]/g, '-');

  function wrap(idx, start, end, tier, id) {
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
      if (!node.nodeValue.slice(s, e).trim()) continue;
      if (e < node.nodeValue.length) node.splitText(e);
      if (s > 0) node = node.splitText(s);
      const mark = document.createElement('mark');
      mark.className = `ht-mark ht-${tier}`;
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
  statusEl.innerHTML = cfg.blocks.length
    ? `Extracted <b>${cfg.blocks.length}</b> text blocks from <b>${esc(cfg.host)}</b>. Checking…`
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
      const seen = new Set();
      for (const m of matches) {
        const tier = tierOf(m.result);
        if (!tier || seen.has(m.text)) continue;
        seen.add(m.text);
        const id = String(items.length);
        const hit = locate(m.text);
        const marks = hit ? wrap(hit.idx, hit.start, hit.end, tier, id) : [];
        items.push({ id, tier, text: m.text, sig: m.result.signature, result: m.result, marks });
      }
      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      const shown = items.filter((i) => i.marks.length).length;
      statusEl.innerHTML = `<b>${cfg.blocks.length}</b> blocks checked in ${secs}s · <b>${items.length}</b> flagged` +
        (items.length !== shown ? ` (${items.length - shown} not located in page)` : '');
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
    const levels = [['exact', true], ['narrative', false], ['related', false]];
    return pick.map((text, i) => ({
      text,
      result: { flagged: true, total: 0, signature: { title: 'TEST highlight (not a real match)', verdict: 'misleading', matchLevel: levels[i][0], settled: levels[i][1], state: 'active', source: 'test', explanation: 'Injected by test mode to check that highlighting works on this site.', techniques: [] } },
    }));
  }

  function render() {
    if (!items.length) {
      listEl.innerHTML = '<div class="empty">Nothing on this page matches a known narrative.</div>';
      return;
    }
    listEl.innerHTML = items
      .map((i) => `<div class="item" data-id="${i.id}" style="border-color:${TIER_COLOR[i.tier]}">
        <div class="tier" style="color:${TIER_COLOR[i.tier]}">${TIER_LABEL[i.tier]}${i.sig ? ' · ' + esc(i.sig.verdict) : ''}</div>
        <div class="title">${esc(i.sig?.title || 'Reported by users')}</div>
        <div class="quote">“${esc(i.text.length > 180 ? i.text.slice(0, 180) + '…' : i.text)}”</div>
        ${i.marks.length ? '' : '<div class="miss">Not located in the rendered page</div>'}
      </div>`)
      .join('');
    listEl.querySelectorAll('.item').forEach((el) =>
      el.addEventListener('click', () => {
        const item = items[+el.dataset.id];
        if (!item.marks.length) return;
        item.marks[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
        item.marks.forEach((m) => m.classList.add('ht-flash'));
        setTimeout(() => item.marks.forEach((m) => m.classList.remove('ht-flash')), 1600);
      }),
    );
  }

  // ---- Tooltip on hover.
  document.addEventListener('mouseover', (e) => {
    const mark = e.target.closest?.('mark.ht-mark');
    if (!mark) return (tip.style.display = 'none');
    const item = items[+mark.dataset.ht];
    const s = item.sig;
    tip.innerHTML = `<div class="tier" style="color:${TIER_COLOR[item.tier]}">${TIER_LABEL[item.tier]}${s ? ' · ' + esc(s.verdict) + ' · ' + esc(s.matchLevel) + ' match' : ''}</div>
      <div class="title">${esc(s?.title || 'Reported by users')}</div>
      ${s?.explanation ? `<p>${esc(s.explanation.length > 400 ? s.explanation.slice(0, 400) + '…' : s.explanation)}</p>` : ''}
      ${s?.techniques?.length ? `<p>Techniques: ${s.techniques.map(esc).join(', ')}</p>` : ''}
      <div class="src">${s ? 'Source: ' + esc(s.source) : `${item.result.total} user votes`}</div>`;
    const r = mark.getBoundingClientRect();
    tip.style.display = 'block';
    const left = Math.min(r.left, window.innerWidth - 370);
    tip.style.left = Math.max(8, left) + 'px';
    tip.style.top = (r.bottom + 8 + 200 > window.innerHeight ? Math.max(8, r.top - tip.offsetHeight - 8) : r.bottom + 8) + 'px';
  });
})();
