import assert from 'node:assert/strict';
import type { AddressInfo } from 'node:net';
import { after, before, test } from 'node:test';
import { fingerprintSentences, hashNormalized, splitSentences } from '../../client/lib/fingerprint';
import { createFingerprintServer, type BlockFingerprint } from './server';

const TEXTS = [
  'The election was rigged by foreign agents. Everyone knows the results were changed overnight!',
  'Vaalit vääristettiin ulkomaisten agenttien toimesta. Kaikki tietävät, että tulokset muutettiin yön aikana.',
  'Выборы были сфальсифицированы иностранными агентами. Все знают, что результаты изменили за ночь.',
  'Ｆｕｌｌｗｉｄｔｈ «quotes» and emoji 🚀 survive normalisation in this sentence here.',
  'Too short.',
];

const server = createFingerprintServer();
let baseUrl = '';

before(async () => {
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
after(() => server.close());

test('sentence and content hashes over HTTP equal client/lib', async () => {
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
  }
  assert.equal(blocks[4].contentHash, null);
});

test('sentence offsets point at the sentence text', async () => {
  const res = await fetch(`${baseUrl}/fingerprint`, { method: 'POST', body: JSON.stringify({ blocks: [TEXTS[2]] }) });
  const { blocks } = (await res.json()) as { blocks: BlockFingerprint[] };
  for (const s of blocks[0].sentences) assert.equal(TEXTS[2].slice(s.start, s.end), s.text);
});

test('rejects a body without a string array of blocks', async () => {
  const res = await fetch(`${baseUrl}/fingerprint`, { method: 'POST', body: JSON.stringify({ blocks: [1] }) });
  assert.equal(res.status, 400);
});
