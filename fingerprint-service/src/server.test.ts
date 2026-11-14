import assert from 'node:assert/strict';
import type { AddressInfo } from 'node:net';
import { after, before, test } from 'node:test';
import { fingerprintSentences, hashNormalized, splitSentences } from '../../client/lib/fingerprint';
import { bands, minhash } from '../../client/lib/minhash';
import { normalizeText } from '../../client/lib/normalize';
import { createFingerprintServer, type BlockFingerprint } from './server';

const TEXTS = [
  'The election was rigged by foreign agents. Everyone knows the results were changed overnight!',
  'Vaalit vääristettiin ulkomaisten agenttien toimesta. Kaikki tietävät, että tulokset muutettiin yön aikana.',
  'Выборы были сфальсифицированы иностранными агентами. Все знают, что результаты изменили за ночь.',
  'Ｆｕｌｌｗｉｄｔｈ «quotes» and emoji 🚀 survive normalisation in this sentence here.',
  'Too short.',
];

/** Stands in for the model: the vector for a text is [its length]. */
const embedded: string[][] = [];
const server = createFingerprintServer(async (texts) => {
  embedded.push(texts);
  return texts.map((t) => [t.length]);
});

async function post(body: unknown): Promise<BlockFingerprint[]> {
  const res = await fetch(`${baseUrl}/fingerprint`, { method: 'POST', body: JSON.stringify(body) });
  assert.equal(res.status, 200);
  return ((await res.json()) as { blocks: BlockFingerprint[] }).blocks;
}
let baseUrl = '';

before(async () => {
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
after(() => server.close());

test('hashes, MinHash and bands over HTTP equal client/lib', async () => {
  const res = await fetch(`${baseUrl}/fingerprint`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ blocks: TEXTS }),
  });
  assert.equal(res.status, 200);
  const { blocks } = (await res.json()) as { blocks: BlockFingerprint[] };
  assert.equal(blocks.length, TEXTS.length);

  for (const [i, text] of TEXTS.entries()) {
    const sentences = splitSentences(text);
    const expected = await Promise.all(sentences.map((s) => hashNormalized(s.normalized)));
    assert.deepEqual(
      blocks[i].sentences.map((s) => s.hash),
      expected,
    );
    assert.equal(
      blocks[i].contentHash,
      sentences.length > 0 ? (await fingerprintSentences(sentences)).contentHash : null,
    );
    const signature = sentences.length > 0 ? minhash(normalizeText(text)) : null;
    assert.deepEqual(blocks[i].minhash, signature);
    assert.deepEqual(blocks[i].bands, signature && bands(signature));
  }
  assert.equal(blocks[4].contentHash, null);
});

test('sentence offsets point at the sentence text', async () => {
  const res = await fetch(`${baseUrl}/fingerprint`, { method: 'POST', body: JSON.stringify({ blocks: [TEXTS[2]] }) });
  const { blocks } = (await res.json()) as { blocks: BlockFingerprint[] };
  for (const s of blocks[0].sentences) assert.equal(TEXTS[2].slice(s.start, s.end), s.text);
});

test('embeds the blocks that have a content hash only when asked', async () => {
  embedded.length = 0;
  assert.deepEqual(
    (await post({ blocks: TEXTS })).map((b) => b.embedding),
    TEXTS.map(() => null),
  );
  assert.equal(embedded.length, 0);

  const blocks = await post({ blocks: TEXTS, embed: true });
  assert.deepEqual(
    blocks.map((b) => b.embedding),
    TEXTS.map((t, i) => (i === 4 ? null : [t.length])),
  );
});

test('rejects a body without a string array of blocks', async () => {
  const res = await fetch(`${baseUrl}/fingerprint`, { method: 'POST', body: JSON.stringify({ blocks: [1] }) });
  assert.equal(res.status, 400);
});
