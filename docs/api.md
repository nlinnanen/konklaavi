# Contracts

The contracts every workstream builds against. Only the integrator changes this file. The TypeScript types in `client/lib/types.ts` are authoritative for request and response bodies.

## HTTP API

FastAPI on `API_PORT`, CORS open to all origins.

- `GET /health` returns `{ok: true}`.
- `POST /votes` takes a `VoteRequest` and returns `{ok: true}`. A repeat vote by the same `userId` on the same `contentHash` replaces the earlier one. Each vote enqueues a `fetch` job for its URL and an `analyze` job; `analyze` does nothing for a `trustworthy` vote.
- `POST /lookup` takes `{hashes: string[], blocks?: {hash: string, bands: string[]}[]}`, at most 500 hashes, and returns `{results: LookupResult[]}` for the hashes that have votes or a signature. `hashes` are sentence hashes and match at the exact level. `blocks` carry a block's content hash and its LSH bands and match at the variant level, with the result keyed by the block hash.
- `GET /domains/{domain}` returns `DomainStats`.
- `POST /check` takes `{text?: string, url?: string}`, fingerprints the text (or the fetched page) through the fingerprint service, and returns `{matches: {text: string, result: LookupResult}[]}`. The dashboard and judges use it without the extension.
- `GET /index?since={version}` returns the signature index, in full when `since` is missing or too old: `{version, full, signatures: Signature[], sentences: {hash, narrativeId}[], bands: {band, hash, narrativeId}[], removed: string[]}`.
- `POST /hits` takes `{hits: {hash: string, narrativeId: string, url: string, seenAt: string}[]}` and returns `{ok: true}`. Each hit records a detection observation and enqueues a `fetch` job for the URL.

A `LookupResult` is flagged when it has at least 2 votes and at least half of them are negative, or when it belongs to a narrative in state `active`. Its `topVerdict` is the most-voted negative verdict when votes flag it, the narrative's verdict when only the narrative flags it, and the most-voted verdict otherwise. It carries a `signature` when it belongs to a narrative in state `active` or `under_review`:

```ts
export interface Signature {
  narrativeId: string;
  title: string;
  verdict: NegativeVerdict;
  techniques: string[];
  explanation: string | null;
  source: 'euvsdisinfo' | 'report' | 'cluster';
  sourceUrl: string | null;
  state: 'under_review' | 'active';
  matchLevel: 'exact' | 'variant';
}
```

## Fingerprint service

Node on `FP_PORT`. It runs the fingerprint code in `client/lib/`, so its output is identical to the extension's.

- `GET /health` returns `{ok: true}`.
- `POST /fingerprint` takes `{blocks: string[], embed?: boolean}` and returns:

```ts
{
  version: string;
  blocks: {
    index: number;
    contentHash: string | null;          // null when the block has no sentence of 5+ words
    sentences: { text: string; start: number; end: number; hash: string }[];
    minhash: number[] | null;           // 128 values
    bands: string[] | null;             // 32 hex strings, bands[i] is band i
    embedding: number[] | null;         // 384 floats, only when embed is true
  }[];
}
```

The embedding model is `Xenova/paraphrase-multilingual-MiniLM-L12-v2`, run with transformers.js in both Node and the browser.

## Jobs

Rows in the `job` table, claimed with `SELECT ... FOR UPDATE SKIP LOCKED`. A handler is `def handle(conn, payload: dict) -> None`, registered with `@register("<kind>")` in `backend/app/jobs/`. An exception retries the job with backoff, up to 5 attempts, then marks it `failed` with the error.

- `fetch` `{url, reason: "seed" | "vote" | "hit", narrative_id?: int, vote_id?: int}`: fetch the page anonymously, extract text, fingerprint it, store page, fingerprints, sentences, bands and observations, attach the fingerprints that carry the narrative to it, and enqueue `match_variants`. Owner: pipeline.
- `match_variants` `{fingerprint_ids: int[]}`: assign variants through LSH bands and containment and write `DERIVED_FROM` edges. Owner: pipeline.
- `analyze` `{vote_id}`: LLM analysis of a reported excerpt into claim, techniques and explanation, creating or extending a narrative. Owner: core.
- `cluster` `{}`, periodic: cluster fingerprint embeddings into narratives and write `EVOLVED_INTO` edges. Owner: data.

## Configuration

Environment variables, read in `backend/app/config.py`: `DATABASE_URL`, `FP_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANALYSIS_MODEL`. Compose reads `COMPOSE_PROJECT_NAME`, `DB_PORT`, `API_PORT`, `FP_PORT` and `DASH_PORT` from the worktree's `.env`.

## File ownership

- integrator: `docs/`, `compose.yaml`, `backend/migrations/000_*`, `backend/app/{config,db,migrate,worker}.py`, `backend/app/jobs/__init__.py`, `backend/app/api/main.py`, `backend/tests/conftest.py`, `client/lib/types.ts`.
- ext: `client/entrypoints/`, `client/lib/api.ts`, `client/lib/messages.ts`, `client/e2e/`, `client/package.json`, `client/README.md`, `client/.gitignore`.
- pipeline: `client/lib/{normalize,fingerprint,minhash,embed}.ts`, `fingerprint-service/`, `backend/app/fpclient.py`, `backend/app/jobs/{fetch,variants}.py`, migrations `2xx`.
- core: `backend/app/api/core.py`, `backend/app/jobs/analyze.py`, `backend/Dockerfile`, `deploy/`, migrations `1xx`.
- data: `backend/app/seed/`, `backend/app/jobs/cluster.py`, `backend/app/eval/`, `dashboard/`, `simulation/`, migrations `3xx`.

Tests live next to their owner's code: `backend/tests/<workstream>/`, and `*.test.ts` beside TypeScript sources.
