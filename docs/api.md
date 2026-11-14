# Contracts

The contracts every workstream builds against. Only the integrator changes this file. The TypeScript types in `client/lib/types.ts` are authoritative for request and response bodies.

## HTTP API

FastAPI on `API_PORT`, CORS open to all origins. Ids are strings on the wire. Errors carry FastAPI's `{detail: string}`, which the extension shows to the user.

### Narratives and matching

- `GET /health` returns `{ok: true}`.
- `POST /lookup` takes `{hashes: string[], blocks?: LookupBlock[]}`, at most 500 of each, and returns `{results: LookupResult[]}` for the hashes that belong to a shipped narrative. `hashes` are sentence hashes and match at the `exact` level. `blocks` carry a block's content hash and its 32 LSH bands; a block matches at the `variant` level when it shares at least 2 bands, at the same band index, with a fingerprint of a shipped narrative, and its result is keyed by the block hash. `/lookup` matches at the exact and variant levels only.
- `POST /check` takes `{text?: string, url?: string}`, fingerprints the text (or the fetched page) through the fingerprint service, and returns `{matches: {text: string, result: LookupResult}[]}`: each sentence that matches exactly, each whole block that matches only at the variant level, and each embedding unit of a block with neither that matches at the `narrative` or `related` level. A consumer shows the strongest match per range. The dashboard uses it without the extension.
- `GET /index?since={version}` returns an `IndexResponse`: every shipped narrative with its sentence hashes, fingerprints (`bands[i]` is band i, null where it has none) and vectors, in full when `since` is missing or newer than the server's version. `version` is opaque and increasing; the client sends it back as `since`. The extension's local index pulls a delta every 30 seconds, and a page showing a changed narrative, or any page when a delta adds one, is looked up again. In a delta each narrative in `narratives` comes with all its sentences, fingerprints and vectors and replaces the client's entries for it, and `removed` lists the narrative ids no longer shipped. `narrativeThreshold` is `NARRATIVE_MATCH_THRESHOLD` and `relatedThreshold` is `RELATED_MATCH_THRESHOLD`. `narrativeVectors` holds `{narrativeId, vectors}` for every narrative in `narratives`, the claim vector first.
- `GET /narratives/{id}` returns the narrative as lookups ship it, with `spread.pages` added; 404 when it is not shipped.
- `POST /hits` takes `{hits: Hit[]}`, at most 500, and returns `{ok: true}`. `narrativeId` is a numeric string and `url` must be http(s). A hit records a detection observation when its narrative holds the hash as a sentence or content hash; hits on unknown narratives are dropped. Each URL gets at most one `fetch` job `{url, reason: "hit", narrative_id}` per day.

A shipped narrative is one with `source = 'note'` that has at least one note not hidden. Narratives with `source = 'cluster'` are internal candidates of the `cluster` job and never ship.

A `LookupResult` is `{hash, matchLevel, similarity?, narrative}`. `matchLevel` says how strongly the passage matched: `exact` (a shared sentence hash), `variant` (a near-duplicate block), `narrative` or `related` (embedding cosine, with `similarity`). When a hash matches several narratives, the strongest level wins, then the more urgent display state, then the closest. `narrative` is `{narrativeId, title, state, notes, spread: {sites, firstSeen}}`; `firstSeen` is the first observation, or the narrative's creation when nothing has been observed. `notes` are the narrative's notes shown to readers: visible and not rated not helpful, ordered helpful misleading, helpful not misleading, controversial, then undecided, oldest first within each. A note is `{id, classification, body, sources, status, ratings: {helpful, somewhat, not_helpful}, createdAt}`.

Page text leaves the browser only as the passage a user submits with a note. Narrative-level matching runs only where the text stays with its owner: in the extension's local index mode, which embeds on the device, and in `POST /check`.

What is embedded is the embedding unit. A block's sentences come from `splitSentences` in `client/lib/fingerprint.ts` (which keeps sentences of 5+ words; the fingerprint service returns the same `sentences`). The units are every sentence, plus every run of 3 consecutive sentences of that list (stride 1) when there are 3 or more. A unit's text is its sentences' `text` joined by one space, and its hash is `fingerprintSentences` over its sentences, so a one-sentence unit's hash is the sentence hash. Its highlight range covers its sentences. `embeddingUnits` in `client/lib/embed.ts` and `units` in `backend/app/api/core.py` build the same units. Blocks with an exact or variant match are not embedded.

A unit matches a narrative at the `narrative` level when its maximum cosine to that narrative's vectors is at least `narrativeThreshold`, and at the `related` level when it is at least `relatedThreshold` and below `narrativeThreshold`; the cosine goes on `similarity`. When several narratives match, the stronger level wins, then the more urgent display state, then the highest cosine.

A narrative's vectors are unit vectors from the embedding model: the embedding of its claim (the note passage), and up to 5 of its attached prose, non-boilerplate blocks closest to their mean. They live in `narrative_vector(narrative_id, kind 'block' | 'claim', fingerprint_id, embedding vector(384))`. On the wire each is 384 int8 values `floor(v * 127 + 0.5)` clamped to +-127 (halves rounded up), base64-encoded; `quantize`, `dequantize` and `cosine` are in `client/lib/embed.ts` with a Python twin in `backend/app/vectors.py`.

### Display states and highlights

A narrative's `state` follows its notes shown to readers (`display_state` in `backend/app/api/narratives.py`, `displayState` in `client/lib/state.ts`, the `NARRATIVES` query in `dashboard/queries.py`):

| State | Rule | Highlight (`HIGHLIGHT_STYLES` in `client/lib/state.ts`) |
| --- | --- | --- |
| misleading | a helpful note classified misleading | strong warm fill and solid underline; the card leads with that note |
| controversial | otherwise, a controversial note | grey fill; "Raters disagree" |
| context | otherwise, a helpful note classified not misleading | calm blue-green fill and underline; the card leads with that note |
| needs_ratings | otherwise, notes waiting for ratings | faint fill |
| tracked | no note shown | subtle dotted underline; "Part of a narrative seen on N sites since <date>" |

The table lists states in order of urgency, which breaks ties between matches. Each sentence takes its strongest match: match level first (exact and variant above narrative above related), then urgency. Narrative and related matches use a lighter variant of their state's style. Consecutive sentences with the same match form one range, so highlights never overlap. The popup shows a legend.

### Notes, ratings and moderation

- `POST /notes` takes `{userId, passage: {text (<= 5000), url (http/https), contentHash, sentenceHashes (1-200)}, classification: 'misleading' | 'not_misleading', body (1-1000), sources?: url[] (<= 10)}` and returns `{noteId, narrativeId}`. 403 unless the install is linked to an account. When one of the passage's sentence hashes or its content hash belongs to a note narrative, the note joins it. Otherwise a narrative is created with `source = 'note'`, title the passage's first sentence and claim the passage; the passage is stored as a fingerprint (`content_hash = contentHash`) with its sentence rows, moving it from a cluster candidate if it held it, and `fetch` `{url, reason: "note", narrative_id}` and `narrative_vectors` are enqueued. The note starts in `pending_review` and `moderate_note` is enqueued.
- `POST /notes/{id}/ratings` takes `{userId, helpfulness: 'helpful' | 'somewhat' | 'not_helpful'}` and returns `{ok: true}`. A rating is stored for any install, one per install and note, a repeat replacing it; only ratings from installs linked to an account count in scoring. 403 for the note's author, 404 for an unknown note. A new or changed rating that counts queues `score_notes`.
- `POST /notes/{id}/report` takes `{userId, reason (1-500)}` and returns `{ok: true}`; 403 unless the install is linked to an account. It sends the note back to `pending_review` with `moderation_reason` "reported: <reason>" and enqueues `moderate_note` and `score_notes`.
- `GET /moderation/queue?userId=` returns `{notes: [{id, narrativeId, title, passage, url, classification, body, sources, reason, createdAt}]}`, the notes in `pending_review`. `POST /moderation/notes/{id}` takes `{userId, action: 'approve' | 'hide'}` and returns `{ok: true}`; approve makes the note visible, hide hides it, and the decision holds until the next report. Either queues `score_notes`. Both return 403 unless the install's account is a moderator: its email is in `MODERATOR_EMAILS` or its reputation is above `MODERATOR_REPUTATION`.

`note(id, narrative_id, author_install uuid, account_id, classification, body (<= 1000), sources text[], status 'needs_more_ratings' | 'helpful' | 'not_helpful' | 'controversial', intercept, factor, decided_at, moderation 'visible' | 'pending_review' | 'hidden', moderation_reason, created_at)` and `note_rating(note_id, install uuid, account_id null, helpfulness, created_at, primary key (note_id, install))` are in migration 104. A change to a note's status, moderation or text, and every rating, marks its narrative changed in `/index`.

### Scoring

`score_notes` fits the Community Notes matrix factorisation (https://communitynotes.x.com/guide, https://github.com/twitter/communitynotes) with one factor dimension: each counted rating r (helpful 1, somewhat 0.5, not helpful 0) is `r ~ mu + i_user + i_note + f_user * f_note`, with L2 regularisation 0.15 on intercepts and 0.03 on factors, fitted by alternating least squares. A rating counts when its install is linked to an account, once per account and note (its latest). A note with fewer than `NOTE_MIN_RATINGS` counted ratings needs more ratings; otherwise it is `helpful` at `i_note >= 0.40` and `|f_note| < 0.50`, `not_helpful` at `i_note <= -0.05 - 0.8 * |f_note|`, `controversial` at `|f_note| >= 0.50`, and `needs_more_ratings` in between. The job stores `intercept`, `factor` and, when the status becomes helpful or not helpful, `decided_at`. The rules and their reasons are in the docstring of `backend/app/jobs/score.py`.

### Accounts and reputation

The extension's install id (`userId`, a random UUID) identifies the reader. An account links installs to an email and a password: `user_account(id uuid, email text unique, password_hash text, created_at)` and `install(id uuid = userId, account_id uuid null, created_at)`. The password is stored as an argon2 hash. The email is not verified. A verified install is one linked to an account, which is what writing notes, counted ratings and moderation check.

- `POST /auth/register` takes `{userId, email, password}`, creates the account for the lowercased email and links the install to it. The email must look like an address and the password must have at least 8 characters, else 422. An email whose account has a password returns 409; an account without one, made before sign-in had passwords, takes the new password.
- `POST /auth/login` takes `{userId, email, password}` and links the install to the account. An unknown email or a wrong password returns 401.
- Both record the attempt in `auth_attempt`; each email and each `userId` gets at most 10 attempts an hour, then 429. On success both return what `GET /me` returns for the install.
- `POST /auth/logout` takes `{userId}`, unlinks the install from its account and returns what `GET /me` returns.
- `GET /me?userId=...` returns a `MeResponse`: `{userId, email, verified, reputation, moderator, notes: [{id, narrativeId, title, classification, body, status, moderation, createdAt}], ratings: {count, agreed}}`. `reputation` is in [0, 1] and null for an install without an account. `notes` are the account's notes, newest first; `ratings.agreed` counts ratings cast before their note was decided that agree with the decision.

Reputation belongs to an account: `(good + 1) / (good + bad + 4)`. A note that becomes helpful adds 2 to good and one that becomes not helpful 2 to bad; a rating cast before its note was decided adds 1 to good when it agrees with the decision and 1 to bad when it doesn't. The reasons are in the docstring of `backend/app/trust.py`.

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

Rows in the `job` table, claimed with `SELECT ... FOR UPDATE SKIP LOCKED`. A handler is `def handle(conn, payload: dict) -> None`, registered with `@register("<kind>")` in `backend/app/jobs/`. An exception retries the job with backoff, up to 5 attempts, then marks it `failed` with the error. Due `moderate_note` and `score_notes` jobs are claimed before any other kind, oldest first within each group; the worker runs one job at a time, so they wait for at most the job already running. `score_notes` is queued only when none is queued, so a burst of ratings coalesces into one run. A job `running` for more than 10 minutes is claimed again and the reclaim counts as an attempt; one that has used all its attempts is marked `failed` with "lease expired".

- `fetch` `{url, reason: "note" | "hit" | "seed" | "eval", narrative_id?: int}`: fetch the page anonymously, extract text, fingerprint and embed it, store page, fingerprints, sentences, bands and observations, and detach boilerplate (`boilerplate` in `backend/app/jobs/variants.py`: a block that is not prose, or whose near-duplicates appear on 2+ pages of a domain it appears on). With a `narrative_id` it attaches every non-boilerplate block that carries the narrative: one sharing a sentence hash with a narrative fingerprint, an LSH near-duplicate of one, or one whose cosine to the passage or to a narrative vector is at least `NARRATIVE_MATCH_THRESHOLD`. It fills the minhash, embedding and bands of the stored passage fingerprint, even when the page cannot be fetched, and enqueues `match_variants` for the new fingerprints. A `hit` fetch is skipped for a page fetched in the last 24 hours. `seed` and `eval` are the offline benchmark's. Owner: pipeline.
- `match_variants` `{fingerprint_ids: int[]}`: confirm candidates sharing at least 2 bands at estimated Jaccard >= 0.5, join a fingerprint that has no variant and is not boilerplate to the closest candidate's variant, and write a `DERIVED_FROM` edge `{jaccard, dt_seconds}` to the closest earlier-seen candidate. Owner: pipeline.
- `narrative_vectors` `{narrative_ids?: int[]}`, every 5 minutes without ids: refresh the claim and block vectors of the given `note` narratives, or of all of them. Block rows are rewritten only when the central set changes and the claim vector is computed only when missing, so unchanged narratives cause no `/index` delta. `fetch`, `match_variants` and `POST /notes` enqueue it. Owner: pipeline.
- `cluster` `{}`, every 5 minutes: cluster the embeddings of unassigned, non-boilerplate fingerprints into candidate narratives (`source = 'cluster'`) or new variants of existing ones, write `EVOLVED_INTO` edges, set every `narrative.centroid` to the mean embedding of its fingerprints, and record the run in a `cluster_run` row. Owner: data.
- `score_notes` `{}`, every 5 minutes and on each counted rating, moderation decision and report: set each note's status as in "Scoring". Owner: core.
- `moderate_note` `{note_id}`: ask `MODERATION_MODEL` through a streamed tool call whether the note is abusive, spam, off topic, or makes factual claims without sources. A clean note becomes visible and queues `score_notes`; a flagged one stays in `pending_review` with the model's reason as `moderation_reason`. Without an API key the note stays in `pending_review`. Owner: core.

`fetch` and `match_variants` also keep `narrative.centroid` current for the narratives they change.

## Configuration

Environment variables, read in `backend/app/config.py`: `DATABASE_URL`, `FP_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `MODERATION_MODEL` (default `claude-haiku-5.5`), `MODERATOR_EMAILS` (comma-separated), `MODERATOR_REPUTATION` (default 0.8), `NOTE_MIN_RATINGS` (default 5), `NARRATIVE_MATCH_THRESHOLD` (cosine, default 0.825), `RELATED_MATCH_THRESHOLD` (cosine, default 0.70). Compose reads `COMPOSE_PROJECT_NAME`, `DB_PORT`, `API_PORT`, `FP_PORT` and `DASH_PORT` from the worktree's `.env`. The fingerprint service caches the model in `MODEL_CACHE_DIR`. The dashboard reads `DATABASE_URL` and `API_URL`. The offline benchmark reads `BENCHMARK_DATABASE_URL` and refuses to run against the product database. In local index mode the extension downloads the embedding model's weights from the Hugging Face hub on first use. Production (`deploy/compose.prod.yaml`) also needs `DOMAIN` and `POSTGRES_PASSWORD` in the VPS `.env`.

## File ownership

- integrator: `docs/`, `compose.yaml`, `backend/migrations/000_*`, `backend/app/{config,db,migrate,worker}.py`, `backend/app/jobs/__init__.py`, `backend/app/api/main.py`, `backend/tests/conftest.py`, `client/lib/types.ts`.
- ext: `client/entrypoints/`, `client/lib/{api,state,account,settings,messages,domain}.ts`, `client/wxt.config.ts`, `client/e2e/`, `client/package.json`, `client/README.md`, `client/.gitignore`.
- pipeline: `client/lib/{normalize,fingerprint,minhash,embed}.ts`, `fingerprint-service/`, `backend/app/fpclient.py`, `backend/app/jobs/{fetch,variants}.py`, migrations `2xx`.
- core: `backend/app/api/{core,account,narratives,notes}.py`, `backend/app/jobs/{score,moderate}.py`, `backend/app/{trust,mail,vectors}.py`, `backend/Dockerfile`, `deploy/`, migrations `1xx`.
- data: `backend/app/eval/`, `backend/app/jobs/cluster.py`, `dashboard/`, `simulation/`, migrations `3xx`.

Tests live next to their owner's code: `backend/tests/<workstream>/`, and `*.test.ts` beside TypeScript sources.
