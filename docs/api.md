# Contracts

The contracts every workstream builds against. Only the integrator changes this file. The TypeScript types in `client/lib/types.ts` are authoritative for request and response bodies.

## HTTP API

FastAPI on `API_PORT`, CORS open to all origins.

- `GET /health` returns `{ok: true}`.
- `POST /votes` takes a `VoteRequest` and returns `{ok: true}`. A repeat vote by the same `userId` on the same `contentHash` replaces the earlier one. Each vote enqueues a `fetch` job for its URL and an `analyze` job; `analyze` does nothing for a `trustworthy` vote.
- `POST /lookup` takes `{hashes: string[], blocks?: LookupBlock[]}`, at most 500 hashes and 500 blocks, and returns `{results: LookupResult[]}` for the hashes that have votes or a signature. `hashes` are sentence hashes and match at the exact level. `blocks` carry a block's content hash and its 32 LSH bands. A block matches at the variant level when it shares at least 2 bands, at the same band index, with a fingerprint in an `active` or `under_review` narrative; the result is keyed by the block hash, has `matchLevel: "variant"` and zero vote counts. When a hash is both a sentence hash and a block hash, the exact signature wins.
- `GET /domains/{domain}` returns `DomainStats`.
- `POST /check` takes `{text?: string, url?: string}`, fingerprints the text (or the fetched page) through the fingerprint service, and returns `{matches: {text: string, result: LookupResult}[]}`. A match is a sentence that matches exactly, or a whole block, with the block text as `text`, that matches only at the variant level. The dashboard and judges use it without the extension.
- `GET /index?since={version}` returns an `IndexResponse`: the signatures of the shipped narratives with their sentence hashes and fingerprints, in full when `since` is missing or newer than the server's version. `version` is an opaque increasing number the client sends back as `since`. Index signatures leave out `matchLevel`. A fingerprint's `bands[i]` is band i, null where it has none; a block matches at the variant level when it shares at least 2 bands at the same index with one fingerprint, as in `/lookup`. In a delta, each narrative in `signatures` comes with all its sentences and fingerprints and replaces the client's entries for it, and `removed` lists the narrative ids no longer shipped.
- `POST /hits` takes `{hits: Hit[]}`, at most 500, and returns `{ok: true}`. `narrativeId` is a numeric string and `url` must be http(s). A hit records a detection observation when its narrative holds the hash as a sentence or content hash; hits on unknown narratives are dropped. Each URL gets at most one `fetch` job `{url, reason: "hit", narrative_id}` per day.

A `LookupResult` is flagged when its reputation-weighted votes flag it (see `backend/app/trust.py`), or when it belongs to a narrative in state `active`. `counts` and `total` count distinct users per verdict. Its `topVerdict` is the negative verdict with the most weight when votes flag it, the narrative's verdict when only the narrative flags it, and the verdict with the most weight otherwise. It carries a `signature` when it belongs to a narrative in state `active`, `under_review` or `disputed`; a `disputed` narrative ships as `under_review`:

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

`minhash`, `bands` and `embedding` are computed over `normalizeText(block)` for blocks that have a `contentHash`. MinHash uses MurmurHash3 x86_32 with seeds 0..127 over word 5-grams (one shingle for a text under 5 words), takes the minimum over the unsigned hashes and stores it as a signed 32-bit integer. Band `i` is MurmurHash3 with seed 0 over 20 little-endian bytes, `i` as uint32 followed by the band's 4 values as int32, written as 8 lowercase hex characters. The extension must reproduce bands exactly for `/lookup`.

The embedding model is `Xenova/paraphrase-multilingual-MiniLM-L12-v2` with q8 weights, run with transformers.js in both Node and the browser, mean-pooled and normalized.

## Jobs

Rows in the `job` table, claimed with `SELECT ... FOR UPDATE SKIP LOCKED`. A handler is `def handle(conn, payload: dict) -> None`, registered with `@register("<kind>")` in `backend/app/jobs/`. An exception retries the job with backoff, up to 5 attempts, then marks it `failed` with the error. A job `running` for more than 10 minutes is claimed again and the reclaim counts as an attempt; one that has used all its attempts is marked `failed` with "lease expired".

- `fetch` `{url, reason: "seed" | "vote" | "hit" | "eval", narrative_id?: int, vote_id?: int}`: fetch the page anonymously, extract text, fingerprint and embed it, store page, fingerprints, sentences, bands and observations, attach the blocks relevant to the narrative (cosine >= 0.5 to its title and claim, and always the closest block) to it, detach boilerplate (see `boilerplate` in `backend/app/jobs/variants.py`: a block that is not prose, or whose near-duplicates appear on 2+ pages of a domain it appears on), and enqueue `match_variants` for the new fingerprints. A narrative left without blocks gets the most relevant non-boilerplate block of its fetched pages (`narrative_page`). A `hit` fetch is skipped for a page fetched in the last 24 hours. Owner: pipeline.
- `match_variants` `{fingerprint_ids: int[]}`: confirm candidates sharing at least 2 bands at estimated Jaccard >= 0.5, join a fingerprint that has no variant and is not boilerplate to the closest candidate's variant, and write a `DERIVED_FROM` edge `{jaccard, dt_seconds}` to the closest earlier-seen candidate. Owner: pipeline.
- `analyze` `{vote_id}`: LLM analysis of a reported excerpt into claim, techniques and explanation, creating or extending a narrative. Owner: core.
- `lifecycle` `{}`, every 10 minutes: move every shipped narrative to the state its votes call for under current reputations. Owner: core.
- `cluster` `{}`, every 5 minutes: cluster the embeddings of unassigned, non-boilerplate (`boilerplate` in `variants.py`) fingerprints into candidate narratives or new variants of existing ones, write `EVOLVED_INTO` edges, set every `narrative.centroid` to the mean embedding of its fingerprints, and record the run in a `cluster_run` row. Owner: data.

`fetch` and `match_variants` also keep `narrative.centroid` current for the narratives they change.

## Configuration

Environment variables, read in `backend/app/config.py`: `DATABASE_URL`, `FP_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANALYSIS_MODEL`. Compose reads `COMPOSE_PROJECT_NAME`, `DB_PORT`, `API_PORT`, `FP_PORT` and `DASH_PORT` from the worktree's `.env`. The fingerprint service caches the model in `MODEL_CACHE_DIR`. The dashboard reads `DATABASE_URL`, `API_URL` and `EVAL_RESULTS`. Production (`deploy/compose.prod.yaml`) also needs `DOMAIN` and `POSTGRES_PASSWORD` in the VPS `.env`.

## File ownership

- integrator: `docs/`, `compose.yaml`, `backend/migrations/000_*`, `backend/app/{config,db,migrate,worker}.py`, `backend/app/jobs/__init__.py`, `backend/app/api/main.py`, `backend/tests/conftest.py`, `client/lib/types.ts`.
- ext: `client/entrypoints/`, `client/lib/api.ts`, `client/lib/settings.ts`, `client/lib/extensionSettings.ts`, `client/wxt.config.ts`, `client/lib/messages.ts`, `client/e2e/`, `client/package.json`, `client/README.md`, `client/.gitignore`.
- pipeline: `client/lib/{normalize,fingerprint,minhash,embed}.ts`, `fingerprint-service/`, `backend/app/fpclient.py`, `backend/app/jobs/{fetch,variants}.py`, migrations `2xx`.
- core: `backend/app/api/core.py`, `backend/app/jobs/analyze.py`, `backend/app/trust.py`, `backend/Dockerfile`, `deploy/`, migrations `1xx`.
- data: `backend/app/seed/`, `backend/app/jobs/cluster.py`, `backend/app/eval/`, `dashboard/`, `simulation/`, migrations `3xx`.

Tests live next to their owner's code: `backend/tests/<workstream>/`, and `*.test.ts` beside TypeScript sources.
