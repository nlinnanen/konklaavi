# Technical decisions

## Fingerprints

Each chunk gets three fingerprints, one per level of the [data model](data-model.md).

- SHA-256 of the normalised text, computed with the browser's built-in `crypto.subtle`. Normalisation is Unicode NFKC, lowercase, punctuation stripped and whitespace collapsed.
- MinHash over word 5-grams with 128 hash functions, using 32-bit MurmurHash3. LSH splits the signature into 32 bands of 4 rows, which puts the match threshold at a Jaccard similarity of roughly 0.4 to 0.5. Candidates are then confirmed by estimated containment, so a short excerpt still matches the longer text it was taken from.
- A 384-dimensional sentence embedding from a multilingual model, run in the browser with transformers.js. The model has to be multilingual so Finnish, Russian and English versions of a narrative land in the same cluster.

Shingles decide the variant level, because shared phrasing is evidence of copying and timestamps give the direction. Embeddings decide the narrative level, because they group rewrites of the same claim.

The extension and the backend must produce identical fingerprints, so the TypeScript fingerprint code in `client/lib/` is the only implementation. The backend runs it in a Node fingerprint service that the Python worker calls over HTTP, and transformers.js runs the same embedding model in Node as in the browser.

## Matching runs on the client

The client never uploads fingerprints of rendered content, because embeddings and shingle hashes can be inverted back into text. It downloads the signature index and matches locally. Telemetry contains only a known variant id, a public URL and a timestamp, and the server confirms the URL is public by fetching it anonymously.

## Signature index

The index is a Bloom filter of LSH band hashes, plus narrative centroids and signature metadata. It is versioned and signed, and clients fetch deltas from the last version they hold.

## Chunking

Chunks are the DOM's block-level elements, with selectors written specifically for the main social platforms so that one post or comment becomes one chunk together with its author handle. A `MutationObserver` fingerprints content as feeds load more.

## Storage

Postgres is the only datastore, run from the `timescale/timescaledb-ha` image, which includes TimescaleDB, pgvector and pgvectorscale. Development runs it in a local Docker container. Production points the same schema at either the container on the VPS or a Tiger Cloud service, which only changes the connection string.

- `observation` is a TimescaleDB hypertable partitioned on `seen_at`, with compression on older chunks.
- Continuous aggregates precompute hits per narrative per hour, new domains per narrative per day and posts per account per hour. They feed the dashboard's spread charts and the detection of posting bursts.
- pgvector holds fingerprint embeddings and narrative centroids.
- LSH bands sit in an indexed table.
- The job queue is a table, and workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`.
- Graph queries use recursive CTEs, and the worker loads subgraphs into networkx for clustering and lineage.

Migrations are plain SQL files applied in filename order by a Python runner at API and worker startup.

## Backend

The backend is Python with FastAPI, using uv, psycopg 3 and raw SQL, so the API and the worker share code. The fetch job uses httpx and trafilatura to extract page text. Narratives are clustered with HDBSCAN on embeddings, and clusters in consecutive time windows are matched by centroid similarity.

## Report analysis

The analysis job calls Claude through Hostinger's AI Router, using the Anthropic SDK with the router's base URL and key. It extracts the core claim, classifies the manipulation techniques and writes the plain-language explanation that ships with the signature.

## Deployment

The whole stack runs with Docker Compose on the team's Hostinger VPS (KVM 2, 2 vCPU, 8 GB RAM), behind its free domain: Postgres, the fingerprint service with the embedding model on CPU, the API, the worker, and the Streamlit dashboard reading Postgres directly. The same compose file runs locally and on the VPS.

## Extension

The extension is a Manifest V3 extension written in TypeScript with WXT. Fingerprinting and the heuristic classifier run in a Web Worker so the page stays responsive.
