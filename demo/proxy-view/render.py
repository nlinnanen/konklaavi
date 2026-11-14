"""Renders a page in headless Chromium, so content its JavaScript loads (comments, lazy sections) is there.

The page gets a few seconds to settle: the cookie banner is dismissed, the page is scrolled to the
end so lazy content loads, and buttons that reveal comments or replies are clicked a few rounds.
Then the DOM is frozen into static HTML, with CSSOM-only styles and current image sources written
out, and its visible text is collected block by block.
"""

import asyncio
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.async_api import Browser, Error, Page, async_playwright

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# Consent buttons, in the languages of the sites we demo on.
CONSENT = re.compile(
    r"^\s*(hyväksy( kaikki)?( evästeet)?|hyväksyn|salli( kaikki)?( evästeet)?|"
    r"accept( all| all cookies| cookies| and continue| & continue)?|agree( and close| & close)?|i agree|"
    r"yes,? i.m happy|allow all( cookies)?|got it|ok|godkänn( alla)?|tillåt alla|akzeptieren|"
    r"alle akzeptieren|zustimmen)\s*$",
    re.IGNORECASE,
)
# Buttons that reveal more comments or replies.
EXPAND = re.compile(
    r"näytä vastaukset|näytä lisää kommentteja|lataa lisää|näytä kommentit|lue kommentit|"
    r"show (more )?(replies|comments)|view (more )?(replies|comments)|load more (comments|replies)|"
    r"more replies|see more comments|\d+ (replies|vastausta)",
    re.IGNORECASE,
)

# Requests the snapshot doesn't need. Ads and trackers keep the network busy, so without blocking them
# the page never goes quiet and every wait runs to its limit; their scripts are stripped anyway.
BLOCKED_HOSTS = re.compile(
    r"(^|\.)("
    r"doubleclick\.net|googlesyndication\.com|googleadservices\.com|googletagmanager\.com|"
    r"googletagservices\.com|google-analytics\.com|adservice\.google\.[a-z.]+|adnxs\.com|"
    r"amazon-adsystem\.com|criteo\.(com|net)|rubiconproject\.com|pubmatic\.com|openx\.net|"
    r"casalemedia\.com|smartadserver\.com|adform\.net|adsafeprotected\.com|moatads\.com|"
    r"scorecardresearch\.com|chartbeat\.(com|net)|cxense\.com|hotjar\.com|facebook\.(com|net)|"
    r"connect\.facebook\.net|twitter\.com|ads-twitter\.com|tiktok\.com|linkedin\.com|"
    r"taboola\.com|outbrain\.com|teads\.tv|improvedigital\.com|yieldlab\.net|gemius\.pl|"
    r"permutive\.(com|app)|tealiumiq\.com|segment\.(com|io)|newrelic\.com|nr-data\.net|"
    r"sentry\.io|bing\.com|clarity\.ms|parsely\.com|kilkaya\.com|lytics\.io|alma\.fi"
    r")$"
    r"|(^|[.-])(ads?|adserver|analytics|tracking|tracker|metrics|stats|pixel)[0-9]*\.",
    re.IGNORECASE,
)
BLOCKED_TYPES = {"media", "websocket", "eventsource", "beacon", "ping"}


async def block_noise(route, request) -> None:
    host = urlparse(request.url).hostname or ""
    if request.resource_type in BLOCKED_TYPES or BLOCKED_HOSTS.search(host):
        await route.abort()
    else:
        await route.continue_()


# Freezes the page and returns its text blocks. A block is the text of the nearest block-level
# ancestor of each visible text node, so inline markup (links, emphasis) stays inside its sentence.
SNAPSHOT_JS = r"""
() => {
  // Without scripts, <noscript> fallbacks ("this site requires JavaScript") would show.
  for (const el of document.querySelectorAll('noscript')) el.remove();
  // Consent managers whose dialog was not dismissed: their iframe is dropped, but the container
  // would still cover the page and block clicks.
  const CMP = '[id^=sp_message_container],#onetrust-consent-sdk,#usercentrics-root,#didomi-host,' +
    '#CybotCookiebotDialog,#qc-cmp2-container,.fc-consent-root,#cmpbox,#cmpbox2,.cmp-root,#truste-consent-track';
  for (const el of document.querySelectorAll(CMP)) el.remove();
  // A modal or the consent dialog may have locked scrolling: by a class on <html> or <body> (Sourcepoint's
  // sp-message-open pins the body with position: fixed), by overflow: hidden, or by a fixed body.
  const LOCK = /^(sp-message-open|modal-open|no-?scroll|noScroll|overflow-hidden|scroll-?lock(ed)?|is-locked|locked|didomi-popup-open|ot-overflow-hidden|disable-?scroll|has-modal|fc-consent-open)$/i;
  for (const el of [document.documentElement, document.body]) {
    for (const c of [...el.classList]) if (LOCK.test(c)) el.classList.remove(c);
    const style = getComputedStyle(el);
    if (style.overflowY === 'hidden') el.style.setProperty('overflow-y', 'auto', 'important');
    if (style.position === 'fixed') {
      el.style.setProperty('position', 'static', 'important');
      el.style.setProperty('top', 'auto', 'important');
    }
  }
  // Empty ad slots leave large blank boxes.
  const AD = /(^|[\s_-])(ad|ads|advert|advertisement|ad-slot|adslot|ad-container|banner-ad)([\s_-]|$)/i;
  for (const el of document.querySelectorAll('[class*=ad i],[id*=ad i]')) {
    if ((AD.test(el.className?.baseVal ?? el.className) || AD.test(el.id)) && (el.textContent || '').trim().length < 40 && !el.querySelector('article,p')) el.remove();
  }
  for (const img of document.querySelectorAll('img')) {
    if (img.currentSrc) img.setAttribute('src', img.currentSrc);
    img.removeAttribute('loading');
  }
  // Styles inserted through the CSSOM (CSS-in-JS) are not in the HTML; write them out.
  const css = (sheet) => { try { return [...sheet.cssRules].map((r) => r.cssText).join('\n'); } catch { return ''; } };
  for (const s of document.querySelectorAll('style')) {
    if (s.sheet && !s.textContent.trim()) s.textContent = css(s.sheet);
  }
  for (const sheet of document.adoptedStyleSheets || []) {
    const s = document.createElement('style');
    s.textContent = css(sheet);
    document.head.append(s);
  }

  const SKIP = 'script,style,noscript,template,svg,button,select,textarea,nav,[role=navigation],[aria-hidden=true]';
  const INLINE = new Set(['inline', 'inline-block', 'contents']);
  const blockOf = new Map();
  const nearestBlock = (el) => {
    const chain = [];
    let b = el;
    while (b && b !== document.body && !blockOf.has(b)) {
      chain.push(b);
      const cs = getComputedStyle(b);
      // A floated drop cap ("I" of "In 4 July") belongs to its paragraph.
      const dropCap = cs.float !== 'none' && b.textContent.trim().length <= 2;
      if (!INLINE.has(cs.display) && !dropCap) break;
      b = b.parentElement;
    }
    const block = blockOf.has(b) ? blockOf.get(b) : b;
    for (const c of chain) blockOf.set(c, block);
    return block;
  };
  const texts = new Map();
  const add = (block, t) => texts.set(block, (texts.get(block) || '') + t);
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  for (let n; (n = walker.nextNode()); ) {
    // A line break separates sentences: "...peliin.<br>Totta" must not become "peliin.Totta".
    if (n.nodeType === 1) {
      if (n.tagName === 'BR' && n.parentElement) add(nearestBlock(n.parentElement), ' ');
      continue;
    }
    const el = n.parentElement;
    if (!el || !n.nodeValue.trim() || el.closest(SKIP)) continue;
    const block = nearestBlock(el);
    if (!block.checkVisibility || !block.checkVisibility()) continue;
    const prev = texts.get(block) || '';
    // Inline pieces that end a sentence and start the next with a capital ("Räisänen.Video").
    const gap = /[.!?”"]$/.test(prev) && /^[A-ZÅÄÖ]/.test(n.nodeValue) ? ' ' : '';
    add(block, gap + n.nodeValue);
  }
  const out = [];
  const seen = new Set();
  for (const t of texts.values()) {
    const s = t.replace(/\s+/g, ' ').trim();
    if (s.length < 25 || s.split(' ').length < 4 || seen.has(s)) continue;
    seen.add(s);
    out.push(s);
  }
  return out;
}
"""


@dataclass
class Rendered:
    url: str
    status: int
    html: str
    blocks: list[str]


_browser: Browser | None = None
_lock = asyncio.Lock()


async def browser() -> Browser:
    """One shared browser; Chromium is downloaded on first use if Playwright has none."""
    global _browser
    async with _lock:
        if _browser is None or not _browser.is_connected():
            pw = await async_playwright().start()
            try:
                _browser = await pw.chromium.launch()
            except Error:
                print("Downloading Chromium for Playwright (one time)…", flush=True)
                await asyncio.to_thread(
                    subprocess.run, [sys.executable, "-m", "playwright", "install", "chromium"], check=True
                )
                _browser = await pw.chromium.launch()
    return _browser


async def click_all(page: Page, pattern: re.Pattern, limit: int, timeout: int = 1500) -> int:
    """Clicks visible buttons and links whose text matches, in every frame. Returns how many."""
    clicked = 0
    for frame in page.frames:
        try:
            # Only controls that stay on the page: a real link would navigate away.
            candidates = frame.locator(
                "button, [role=button], a:not([href]), a[href^='#'], a[href^='javascript']"
            ).filter(has_text=pattern)
            count = min(await candidates.count(), limit - clicked)
            for i in range(count):
                el = candidates.nth(i)
                if await el.is_visible():
                    text = (await el.inner_text(timeout=timeout)).strip()
                    if pattern.search(text):
                        await el.click(timeout=timeout, no_wait_after=True)
                        clicked += 1
        except Error:
            continue
        if clicked >= limit:
            break
    return clicked


async def scroll_to_end(page: Page, rounds: int = 12) -> None:
    last = 0
    for _ in range(rounds):
        height = await page.evaluate("document.documentElement.scrollHeight")
        await page.mouse.wheel(0, 2500)
        await page.wait_for_timeout(250)
        at = await page.evaluate("window.scrollY + window.innerHeight")
        if at >= height and height == last:
            break
        last = height


async def settle(page: Page, ms: int) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=ms)
    except Error:
        pass


async def render(url: str) -> Rendered:
    context = await (await browser()).new_context(
        user_agent=UA, locale="fi-FI", viewport={"width": 1280, "height": 900}, java_script_enabled=True
    )
    await context.route("**/*", block_noise)
    page = await context.new_page()
    t0 = time.monotonic()
    marks: list[str] = []
    mark = lambda name: marks.append(f"{name} {time.monotonic() - t0:.1f}s")
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        status = response.status if response else 0
        mark("dom")
        await settle(page, 4000)
        mark("settle")
        await click_all(page, CONSENT, 1)
        mark("consent")
        await scroll_to_end(page)
        mark("scroll")
        for _ in range(3):
            if not await click_all(page, EXPAND, 15):
                break
            await settle(page, 2000)
        mark("expand")
        await scroll_to_end(page, rounds=3)
        await settle(page, 1500)
        mark("scroll2")
        blocks = await page.evaluate(SNAPSHOT_JS)
        html = await page.content()
        mark("snapshot")
        print(f"render {url}: " + ", ".join(marks), flush=True)
        return Rendered(url=page.url, status=status, html=html, blocks=blocks)
    finally:
        await context.close()
