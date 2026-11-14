export function domainOf(hostname: string): string {
  return hostname.toLowerCase().replace(/^www\./, '');
}

/** URL without fragment; the part we store with a vote. */
export function cleanUrl(href: string): string {
  const u = new URL(href);
  u.hash = '';
  return u.toString();
}
