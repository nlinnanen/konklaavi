import { describe, expect, it } from 'vitest';
import { canonicalPageUrl } from './domain';

describe('canonicalPageUrl', () => {
  const own = ['konklaavi.tech'];

  it('maps a page the demo proxy shows to the page itself', () => {
    const target = 'https://www.is.fi/uutiset/art-1.html?x=1';
    for (const path of ['/demo/view', '/demo/frame'])
      expect(canonicalPageUrl(`https://konklaavi.tech${path}?url=${encodeURIComponent(target)}&test=1`, own)).toBe(target);
  });

  it('leaves other pages unchanged', () => {
    for (const url of ['https://www.is.fi/demo/view?url=https%3A%2F%2Fexample.com%2F', 'https://konklaavi.tech/api/admin'])
      expect(canonicalPageUrl(url, own)).toBe(url);
  });
});
