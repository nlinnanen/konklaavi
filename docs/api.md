# Contracts

The contracts every workstream builds against. Only the integrator changes this file. The TypeScript types in `client/lib/types.ts` are authoritative for request and response bodies.

## HTTP API

FastAPI on `API_PORT`, CORS open to all origins.

- `GET /health` returns `{ok: true}`.
- `POST /votes` takes a `VoteRequest` and returns `{ok: true}`. A repeat vote by the same `userId` on the same `contentHash` replaces the earlier one, keeping its `narrativeId` when the new vote has none. A report (no `narrativeId`) enqueues a `fetch` job for its URL and an `analyze` job; `analyze` does nothing for a `trustworthy` vote. A feedback vote, cast from a highlight's tooltip, carries the matched `narrativeId` and the highlight's `matchLevel` and no `excerpt`: it enqueues no job and moves its narrative's state at once. A feedback vote with an `excerpt`, a `matchLevel` without `narrativeId`, or an unknown `narrativeId` gets a 422. Its `contentHash` is the matched content's hash: the sentence hash for an exact match, the block hash for a variant match, the unit hash for a narrative or related match.
- `POST /lookup` takes `{hashes: string[], blocks?: LookupBlock[]}`, at most 500 hashes and 500 blocks, and returns `{results: LookupResult[]}` for the hashes that have votes or a signature. `hashes` are sentence hashes and match at the exact level. `blocks` carry a block's content hash and its 32 LSH bands. A block matches at the variant level when it shares at least 2 bands, at the same band index, with a fingerprint in an `active` or `under_review` narrative; the result is keyed by the block hash, has `matchLevel: "variant"` and zero vote counts. When a hash is both a sentence hash and a block hash, the exact signature wins.
- `GET /domains/{domain}` returns `DomainStats`.
- `POST /check` takes `{text?: string, url?: string}`, fingerprints the text (or the fetched page) through the fingerprint service, and returns `{matches: {text: string, result: LookupResult}[]}`. A match is a sentence that matches exactly, or a whole block, with the block text as `text`, that matches only at the variant level, or an embedding unit of a block with no exact or variant match that is related to a narrative or closer (`matchLevel` `narrative` or `related`, with `similarity`, zero vote counts, the unit text as `text`). Every matching unit is returned, so a sentence and a 3-sentence run holding it can both appear; a consumer shows the strongest tier per range. The dashboard and judges use it without the extension.
- `GET /index?since={version}` returns an `IndexResponse`: the signatures of the shipped narratives with their sentence hashes and fingerprints, in full when `since` is missing or newer than the server's version. `version` is an opaque increasing number the client sends back as `since`. Index signatures leave out `matchLevel`. A fingerprint's `bands[i]` is band i, null where it has none; a block matches at the variant level when it shares at least 2 bands at the same index with one fingerprint, as in `/lookup`. In a delta, each narrative in `signatures` comes with all its sentences and fingerprints and replaces the client's entries for it, and `removed` lists the narrative ids no longer shipped. `narrativeThreshold` is the cosine from `NARRATIVE_MATCH_THRESHOLD` and `relatedThreshold` the one from `RELATED_MATCH_THRESHOLD`. Index signatures carry `settled` and, for report narratives, `reports`; the narratives shipped are those in state `active`, `under_review` and `disputed`. `narrativeVectors` holds `{narrativeId, vectors}` for every narrative in `signatures`, the claim vector first, with an empty list when it has none; in a delta an entry replaces the client's vectors for that narrative.

Narrative-level matching runs only where the text stays with its owner: in the extension's local index mode, which embeds on the device, and in `POST /check`. `/lookup` matches at the exact and variant levels only. Page text leaves the browser only in an explicit report (a vote with an excerpt).

What is embedded is the embedding unit. A block's sentences come from `splitSentences` in `client/lib/fingerprint.ts` (which keeps sentences of 5+ words; the fingerprint service returns the same `sentences`). The units are every sentence, plus every run of 3 consecutive sentences of that list (stride 1) when there are 3 or more. A unit's text is its sentences' `text` joined by one space, and its hash is `fingerprintSentences` over its sentences, so a one-sentence unit's hash is the sentence hash. Its highlight range covers its sentences. `embeddingUnits` in `client/lib/embed.ts` and `units` in `backend/app/api/core.py` build the same units. Blocks with an exact or variant match are not embedded.

A unit matches a narrative at the `narrative` level when its maximum cosine to that narrative's vectors is at least `narrativeThreshold`, and at the `related` level when it is at least `relatedThreshold` and below `narrativeThreshold`; the cosine goes on `similarity`. When several narratives match, the stronger level wins, then an `active` narrative (the extension: the stronger tier), then the highest cosine.


A narrative's vectors are unit vectors from the embedding model: up to 5 of its attached prose, non-boilerplate blocks closest to their mean, plus the embedding of its claim text (title and claim), so a narrative with no fetched blocks still has one, plus LLM paraphrases of its claim worded as an article would assert it. They live in `narrative_vector(narrative_id, kind 'block' | 'claim' | 'paraphrase', fingerprint_id, text, embedding vector(384))`, where `text` is the paraphrase. On the wire each is 384 int8 values `floor(v * 127 + 0.5)` clamped to +-127 (halves rounded up), base64-encoded; `quantize`, `dequantize` and `cosine` are in `client/lib/embed.ts` with a Python twin in `backend/app/vectors.py`.
- `POST /hits` takes `{hits: Hit[]}`, at most 500, and returns `{ok: true}`. `narrativeId` is a numeric string and `url` must be http(s). A hit records a detection observation when its narrative holds the hash as a sentence or content hash; hits on unknown narratives are dropped. Each URL gets at most one `fetch` job `{url, reason: "hit", narrative_id}` per day.
- `POST /auth/magic-link` takes a `MagicLinkRequest` `{userId, email}` and returns `{ok: true}`. It stores a random single-use token as its SHA-256 hash with a 30-minute expiry and emails the link `{PUBLIC_API_URL}/auth/verify?token=...` through `SMTP_URL` from `MAIL_FROM`. Without `SMTP_URL` the link goes to the API log; it is never in the response. Each email and each `userId` gets at most 5 links an hour, then 429.
- `GET /auth/verify?token=...` consumes the token, links its install to the account for the token's email (creating the account) and returns an HTML page "Signed in as <email>, you can close this tab". An invalid, used or expired token returns a 400 page.
- `GET /me?userId=...` returns a `MeResponse`: the linked email or null, `reputation` in [0, 1], `weight` (equal to `reputation`, since burst division is per content), the `agreed` and `disagreed` counts, and the reports of every install of the account. A report is a `source = 'report'` narrative that one of those installs voted negative on; its `outcome` is `rejected` when the narrative is retired, `confirmed` when `settled_by` is set, `pending` otherwise. `settledBy` is the confirming narrative `{source, title, sourceUrl}`, `sourceUrl` possibly null. An unknown `userId` gets a new voter's numbers and no reports.

A `LookupResult` is flagged when its reputation-weighted votes flag it (see `backend/app/trust.py`), or when it matches a narrative in state `active` at any level but `related`. `counts` and `total` count distinct users per verdict. Its `topVerdict` is the negative verdict with the most weight when votes flag it, the narrative's verdict when only the narrative flags it, and the verdict with the most weight otherwise. It carries a `signature` when it belongs to a narrative in state `active`, `under_review` or `disputed`:

```ts
export interface Signature {
  narrativeId: string;
  title: string;
  verdict: NegativeVerdict;
  techniques: string[];
  explanation: string | null;
  source: 'euvsdisinfo' | 'report' | 'cluster' | 'network' | 'factcheck';
  sourceUrl: string | null;
  state: 'under_review' | 'active' | 'disputed';
  matchLevel: 'exact' | 'variant' | 'narrative' | 'related';
  /** Active narratives from euvsdisinfo, factcheck or network, and reports with settledBy set. */
  settled: boolean;
  /** Report narratives: distinct negative voters and their summed vote weight. */
  reports?: { count: number; weight: number };
  /** Cosine of a narrative or related match. */
  similarity?: number;
}
```

`matchLevel: 'narrative'` or `'related'` means the unit is semantically close to a narrative but its block shares no sentence and no 2 bands with it.

A feedback vote counts toward its narrative's tally (`narrative_tallies` in `backend/app/trust.py`) like any vote attached to it, so weighted `trustworthy` feedback disputes and then retires a narrative under the usual lifecycle rules, and once retired those votes count as agreements in reputation. `vote.narrative_id` and `vote.match_level` (text, migration 103) store it; the trigger `index_change_vote` from the same migration marks a report narrative changed in the index when a vote attaches to it.

### Tiers

The client decides a highlight's tier from the `LookupResult` (`tierOf` in `client/lib/tier.ts`; `dashboard/tiers.py` mirrors it). Strongest first:

| Tier | Rule | Style (`TIER_STYLES`) |
| --- | --- | --- |
| confirmed | exact or variant match to an `active`, `settled` narrative | solid red background `#dc2626`, white text |
| likely | `narrative` match to an `active` narrative, or an exact or variant match to an `active` narrative that is not settled | orange background `rgba(249,115,22,0.3)` |
| reported | any match to a report narrative in `under_review`, an `under_review` match the votes flag, or content flagged by votes alone | faint purple background `rgba(168,85,247,0.15)` |
| related | `related` match to an `active` narrative | 2px dotted yellow underline `rgba(234,179,8,0.9)` |
| disputed | any match to a `disputed` narrative | grey background `rgba(156,163,175,0.25)` |

An `under_review` narrative that is not a report and is not flagged by votes gets no tier. Each sentence takes the strongest tier among the matches covering it, the smallest unit on a tie; consecutive sentences with the same match form one range, so highlights never overlap. The popup shows a legend. Every tooltip has the buttons "Misinformation" and "Not misinformation", which send a feedback vote.

## Accounts, settlement and reputation

The extension's install id (`userId`, a random UUID sent with votes) is the voter identity. An optional account links installs to an email: `user_account(id uuid, email text unique, created_at)`, `install(id uuid = userId, account_id uuid null references user_account, created_at)` and `login_token(token_hash, install_id, email, expires_at, used_at, created_at)` from migration 102. Reputation is computed per account when an install is linked, from the votes of all its installs with one vote per account per content, and per install otherwise.

Settled-bad content is the narratives with source `euvsdisinfo` or `factcheck` in state `active`, and confirmed report narratives. Settled-good content is the retired narratives and the pages of reliable outlets: `backend/app/reliable.py` `is_reliable_url(url)` matches a host, `www.` stripped, or a parent domain against the domains of `backend/app/eval/negatives.txt` plus outlets the evaluation cannot fetch.

`settle_reports` settles open report narratives. A report whose reported page (`narrative.source_url`) is on a reliable outlet is rejected. Otherwise it is confirmed when an active `euvsdisinfo` or `factcheck` narrative matches it: a shared sentence hash, a near-duplicate block, or a cosine >= `NARRATIVE_MATCH_THRESHOLD` between any of their narrative vectors. Confirmation sets state `active`, `narrative.settled_by` (the matching narrative) and `settled_at`; rejection sets state `retired` and `settled_at` (migration 203). Votes move neither `euvsdisinfo` nor `factcheck` narratives nor narratives with `settled_at` set.

The reputation rules are in the docstring of `backend/app/trust.py`. A negative vote on settled-bad content or a trustworthy vote on settled-good content agrees; the opposite disagrees. Agreement counts once per narrative, or once per content hash on a reliable page. A negative vote received before its narrative's `settled_at` counts as 2 agreements.

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

`minhash` and `bands` are computed over `normalizeText(block)` for blocks that have a `contentHash`. `embedding` is computed over the raw block text for the same blocks. MinHash uses MurmurHash3 x86_32 with seeds 0..127 over word 5-grams (one shingle for a text under 5 words), takes the minimum over the unsigned hashes and stores it as a signed 32-bit integer. Band `i` is MurmurHash3 with seed 0 over 20 little-endian bytes, `i` as uint32 followed by the band's 4 values as int32, written as 8 lowercase hex characters. The extension must reproduce bands exactly for `/lookup`.

The embedding model is `Xenova/paraphrase-multilingual-MiniLM-L12-v2` with q8 weights, run with transformers.js in both Node and the browser, mean-pooled and normalized.

## Jobs

Rows in the `job` table, claimed with `SELECT ... FOR UPDATE SKIP LOCKED`. A handler is `def handle(conn, payload: dict) -> None`, registered with `@register("<kind>")` in `backend/app/jobs/`. An exception retries the job with backoff, up to 5 attempts, then marks it `failed` with the error. A job `running` for more than 10 minutes is claimed again and the reclaim counts as an attempt; one that has used all its attempts is marked `failed` with "lease expired".

- `fetch` `{url, reason: "seed" | "vote" | "hit" | "eval", narrative_id?: int, vote_id?: int}`: fetch the page anonymously, extract text, fingerprint and embed it, store page, fingerprints, sentences, bands and observations, attach the blocks relevant to the narrative (cosine >= 0.5 to its title and claim, and always the closest block) to it, detach boilerplate (see `boilerplate` in `backend/app/jobs/variants.py`: a block that is not prose, or whose near-duplicates appear on 2+ pages of a domain it appears on), and enqueue `match_variants` for the new fingerprints. A narrative left without blocks gets the most relevant non-boilerplate block of its fetched pages (`narrative_page`). A `hit` fetch is skipped for a page fetched in the last 24 hours. Owner: pipeline.
- `match_variants` `{fingerprint_ids: int[]}`: confirm candidates sharing at least 2 bands at estimated Jaccard >= 0.5, join a fingerprint that has no variant and is not boilerplate to the closest candidate's variant, and write a `DERIVED_FROM` edge `{jaccard, dt_seconds}` to the closest earlier-seen candidate. Owner: pipeline.
- `analyze` `{vote_id}`: LLM analysis of a reported excerpt into claim, techniques and explanation, creating or extending a narrative. Owner: core.
- `settle_reports` `{}`, every 5 minutes: confirm or reject open report narratives (states `under_review` and `disputed`) as in "Accounts, settlement and reputation", and set `settled_at` on report narratives that votes retired. Owner: pipeline.
- `lifecycle` `{}`, every 10 minutes: move every shipped narrative to the state its votes call for under current reputations. Owner: core.
- `cluster` `{}`, every 5 minutes: cluster the embeddings of unassigned, non-boilerplate (`boilerplate` in `variants.py`) fingerprints into candidate narratives or new variants of existing ones, write `EVOLVED_INTO` edges, set every `narrative.centroid` to the mean embedding of its fingerprints, and record the run in a `cluster_run` row. Owner: data.
- `narrative_vectors` `{narrative_ids?: int[]}`, every 5 minutes without ids: refresh the vectors of the given narratives, or of every narrative not retired or rejected. Block rows are rewritten only when the central set changes and the claim vector is computed only when missing, so unchanged narratives cause no `/index` delta. `fetch` and `match_variants` enqueue it for the narratives they change. Owner: pipeline.
- `paraphrases` `{}`, every 10 minutes: for up to 60 shipped narratives without paraphrase vectors, ask `PARAPHRASE_MODEL` for 6 paraphrases of each claim (2 English, 1 each Russian, Spanish, French, German), 10 narratives per call, and store their embeddings as `paraphrase` vectors. Owner: core.

`fetch` and `match_variants` also keep `narrative.centroid` current for the narratives they change.

## Configuration

Environment variables, read in `backend/app/config.py`: `DATABASE_URL`, `FP_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANALYSIS_MODEL`, `NARRATIVE_MATCH_THRESHOLD` (cosine, default 0.825), `RELATED_MATCH_THRESHOLD` (cosine, default 0.70), `SMTP_URL` (`smtp[s]://user:pass@host:port`; `smtps` uses implicit TLS, `smtp` STARTTLS when offered), `MAIL_FROM`, `PUBLIC_API_URL` (base of the sign-in link, default `http://localhost:API_PORT`). Compose reads `COMPOSE_PROJECT_NAME`, `DB_PORT`, `API_PORT`, `FP_PORT` and `DASH_PORT` from the worktree's `.env`. The fingerprint service caches the model in `MODEL_CACHE_DIR`. The dashboard reads `DATABASE_URL`, `API_URL` and `EVAL_RESULTS`. In local index mode the extension downloads the embedding model's weights from the Hugging Face hub on first use. Production (`deploy/compose.prod.yaml`) also needs `DOMAIN` and `POSTGRES_PASSWORD` in the VPS `.env`.

## File ownership

- integrator: `docs/`, `compose.yaml`, `backend/migrations/000_*`, `backend/app/{config,db,migrate,worker}.py`, `backend/app/jobs/__init__.py`, `backend/app/api/main.py`, `backend/tests/conftest.py`, `client/lib/types.ts`.
- ext: `client/entrypoints/`, `client/lib/api.ts`, `client/lib/tier.ts`, `client/lib/account.ts`, `client/lib/settings.ts`, `client/lib/extensionSettings.ts`, `client/wxt.config.ts`, `client/lib/messages.ts`, `client/e2e/`, `client/package.json`, `client/README.md`, `client/.gitignore`.
- pipeline: `client/lib/{normalize,fingerprint,minhash,embed}.ts`, `fingerprint-service/`, `backend/app/fpclient.py`, `backend/app/jobs/{fetch,variants,settle}.py`, migrations `2xx`.
- core: `backend/app/api/{core,account}.py`, `backend/app/jobs/analyze.py`, `backend/app/{trust,reliable,mail}.py`, `backend/Dockerfile`, `deploy/`, migrations `1xx`.
- data: `backend/app/seed/`, `backend/app/jobs/cluster.py`, `backend/app/eval/`, `dashboard/`, `simulation/`, migrations `3xx`.

Tests live next to their owner's code: `backend/tests/<workstream>/`, and `*.test.ts` beside TypeScript sources.
