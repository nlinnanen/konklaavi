/** URL without fragment; the part sent with a note. */
export function cleanUrl(href: string): string {
  const u = new URL(href);
  u.hash = '';
  return u.toString();
}
