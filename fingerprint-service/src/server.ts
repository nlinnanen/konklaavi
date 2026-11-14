import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { pathToFileURL } from 'node:url';
import { FINGERPRINT_VERSION, fingerprintSentences, hashNormalized, splitSentences } from '../../client/lib/fingerprint';

export interface BlockFingerprint {
  index: number;
  contentHash: string | null;
  sentences: { text: string; start: number; end: number; hash: string }[];
  minhash: number[] | null;
  bands: string[] | null;
  embedding: number[] | null;
}

export async function fingerprintBlock(text: string, index: number): Promise<BlockFingerprint> {
  const sentences = splitSentences(text);
  const hashes = await Promise.all(sentences.map((s) => hashNormalized(s.normalized)));
  return {
    index,
    contentHash: sentences.length > 0 ? (await fingerprintSentences(sentences)).contentHash : null,
    sentences: sentences.map((s, i) => ({ text: s.text, start: s.start, end: s.end, hash: hashes[i] })),
    minhash: null,
    bands: null,
    embedding: null,
  };
}

const MAX_BODY_BYTES = 10 * 1024 * 1024;

class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function readJson(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw new HttpError(413, 'body too large');
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new HttpError(400, 'body is not JSON');
  }
}

function send(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(JSON.stringify(body));
}

async function route(req: IncomingMessage, res: ServerResponse): Promise<void> {
  if (req.method === 'GET' && req.url === '/health') return send(res, 200, { ok: true });
  if (req.method === 'POST' && req.url === '/fingerprint') {
    const body = (await readJson(req)) as { blocks?: unknown };
    const blocks = body?.blocks;
    if (!Array.isArray(blocks) || !blocks.every((b) => typeof b === 'string')) {
      throw new HttpError(400, 'blocks must be an array of strings');
    }
    const fingerprints = await Promise.all(blocks.map((text, i) => fingerprintBlock(text, i)));
    return send(res, 200, { version: FINGERPRINT_VERSION, blocks: fingerprints });
  }
  throw new HttpError(404, 'not found');
}

export function createFingerprintServer(): Server {
  return createServer((req, res) => {
    route(req, res).catch((err: unknown) => {
      const status = err instanceof HttpError ? err.status : 500;
      if (status === 500) console.error(err);
      send(res, status, { error: err instanceof Error ? err.message : String(err) });
    });
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const port = Number(process.env.PORT ?? 8100);
  createFingerprintServer().listen(port, () => console.log(`fingerprint service on :${port}`));
}
