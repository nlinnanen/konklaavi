/** URL without fragment; the part sent with a note. */
export function cleanUrl(href: string): string {
  const u = new URL(href);
  u.hash = '';
  return u.toString();
}

/** Pages of the demo proxy (demo/proxy-view), such as `/demo/view` or `/frame`, carry the page they show in `?url=`. */
const PROXY_PATH = /\/(view|frame)$/;

/**
 * The page a URL shows: the original target of a page the demo proxy on one of `ownHosts` renders, the
 * URL unchanged otherwise. `backend/app/jobs/fetch.py` has the same rule as `canonical_page_url`.
 */
export function canonicalPageUrl(href: string, ownHosts: Iterable<string>): string {
  let u: URL;
  try {
    u = new URL(href);
  } catch {
    return href;
  }
  const hosts = new Set([...ownHosts].map((h) => h.toLowerCase()));
  const target = u.searchParams.get('url');
  if (!hosts.has(u.hostname.toLowerCase()) || !PROXY_PATH.test(u.pathname) || !target) return href;
  // The proxy opens a URL without a scheme over https.
  const withScheme = /^https?:\/\//i.test(target) ? target : 'https://' + target;
  try {
    return new URL(withScheme).toString();
  } catch {
    return href;
  }
}
