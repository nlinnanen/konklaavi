# Technical decisions

## Labels come from the crowd

HashTrust recognises narratives and records their spread, but it does not label them on its own authority. Every label a reader sees is a context note written by another reader and shown because the scoring job found it helpful. The product database holds no imported content and no labels from sources, fact-checkers or language models.

## Fingerprints

Each block gets fingerprints at the three levels of the [data model](data-model.md).

- A SHA-256 hash of every normalised sentence of 5+ words, computed with the browser's `crypto.subtle`. Normalisation is Unicode NFKC, lowercase, punctuation stripped and whitespace collapsed.
- MinHash over word 5-grams with 128 hash functions, using 32-bit MurmurHash3. LSH splits the signature into 32 bands of 4 rows, which puts the candidate threshold at a Jaccard similarity of roughly 0.4 to 0.5; candidates are confirmed at an estimated Jaccard of at least 0.5.
- A 384-dimensional embedding from `paraphrase-multilingual-MiniLM-L12-v2`, run with transformers.js. The model is multilingual so the Finnish, Russian and English versions of a claim land close together. Embedding units are every sentence and every run of three consecutive sentences, so short claims are compared as well as long ones.

Shingles decide the variant level, because shared phrasing is evidence of copying and timestamps give the direction. Embeddings decide the narrative level, because they group rewrites of the same claim.

The extension and the backend must produce identical fingerprints, so the TypeScript code in `client/lib/` is the only implementation. The backend runs it in the Node fingerprint service, and transformers.js runs the same embedding model in Node as in the browser.

## Page text stays in the browser

Page text leaves the browser only as a passage a reader submits with a note. In remote mode the extension sends sentence hashes and LSH bands to `/lookup`, which matches at the exact and variant levels. In local index mode it downloads the index, with each narrative's vectors as base64 int8, and matches on the device at every level. Detection hits carry a narrative id, a hash and a public URL, and the backend fetches that URL itself before anything about the page enters the graph.

## Note scoring

Notes are scored with the open-source Community Notes matrix factorisation, with one factor dimension, fitted by alternating least squares. A note is shown as helpful only when its intercept is high and its factor small, which means raters on both sides of the main divide found it helpful. Only ratings from verified accounts count, once per account and note. The thresholds and their reasons are in the docstring of `backend/app/jobs/score.py`.

## Accounts and reputation

The install id identifies a reader, and an emailed single-use link ties installs to an account. Writing and reporting notes need a verified account, and only verified ratings count. Reputation is `(good + 1) / (good + bad + 4)` per account, earned by notes that become helpful and by early ratings that agree with the outcome; its reasons are in the docstring of `backend/app/trust.py`. High reputation grants moderator rights.

## Moderation

Every note is reviewed by Claude, which answers through a tool call that reports abuse, spam, off-topic content and factual claims without sources. A clean note becomes visible and a flagged one waits in the review queue, as does every note when no API key is set. Readers' reports send a note back to the queue, and moderators approve or hide it from the dashboard. The model runs through Hostinger's AI Router with the Anthropic SDK.

## Index

The index is JSON: each shipped narrative with its notes, sentence hashes, LSH bands and vectors. Clients fetch deltas from the last version they hold, and triggers on notes, ratings and narrative vectors mark a narrative changed. A match ranks first by level, then by how urgent the narrative's display state is, then by closeness.

## Chunking

Blocks are the DOM's block-level elements, found generically for any site, and a `MutationObserver` rescans content as feeds load more. Fingerprinting runs on the main thread in 8 ms slices so the page keeps handling input; index parsing and matching run in the background worker, and the embedding model in an offscreen document.

## Storage

Postgres is the only datastore, run from the `timescale/timescaledb-ha` image with TimescaleDB and pgvector.

- `observation` is a TimescaleDB hypertable partitioned on `seen_at`, with continuous aggregates of hits per narrative per hour and new domains per narrative per day.
- pgvector holds fingerprint embeddings, narrative vectors and centroids.
- LSH bands sit in an indexed table.
- The job queue is a table, and workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`.

Migrations are plain SQL files applied in filename order by a Python runner at API and worker startup.

## Backend

The backend is Python with FastAPI, using uv, psycopg 3 and raw SQL, so the API and the worker share code. The fetch job uses httpx and trafilatura to extract page text and detaches boilerplate that repeats across a domain's pages. The `cluster` job groups unassigned fingerprints with HDBSCAN into candidate narratives.

## Extension

The extension is a Manifest V3 extension written in TypeScript with WXT. Highlights use the CSS Custom Highlight API so the page DOM is never modified, and the page UI lives in a Shadow DOM.

## Deployment

The stack runs with Docker Compose locally and on a Hostinger VPS. GitHub Actions builds the images and pushes them to GHCR, every push to `main` releases them on the VPS, and Ansible hardens and configures the VPS. The details are in [deployment.md](deployment.md).
