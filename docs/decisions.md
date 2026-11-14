# Technical decisions

## Fingerprints

Each chunk gets three fingerprints, one per level of the [data model](data-model.md).

- SHA-256 of the normalised text, computed with the browser's built-in `crypto.subtle`. Normalisation is Unicode NFKC, lowercase, punctuation stripped and whitespace collapsed.
- MinHash over word 5-grams with 128 hash functions, using 32-bit MurmurHash3. LSH splits the signature into 32 bands of 4 rows, which puts the match threshold at a Jaccard similarity of roughly 0.4 to 0.5. Candidates are then confirmed by estimated containment, so a short excerpt still matches the longer text it was taken from.
- A 384-dimensional sentence embedding from a multilingual model, run in the browser with transformers.js. The model has to be multilingual so Finnish, Russian and English versions of a narrative land in the same cluster.

Shingles decide the variant level, because shared phrasing is evidence of copying and timestamps give the direction. Embeddings decide the narrative level, because they group rewrites of the same claim.

## Matching runs on the client

The client never uploads fingerprints of rendered content, because embeddings and shingle hashes can be inverted back into text. It downloads the signature index and matches locally. Telemetry contains only a known variant id, a public URL and a timestamp, and the server confirms the URL is public by fetching it anonymously.

## Signature index

The index is a Bloom filter of LSH band hashes, plus narrative centroids and signature metadata. It is versioned and signed, and clients fetch deltas from the last version they hold.

## Chunking

Chunks are the DOM's block-level elements, with selectors written specifically for the main social platforms so that one post or comment becomes one chunk together with its author handle. A `MutationObserver` fingerprints content as feeds load more.

## Storage

Postgres with pgvector is the only datastore. It holds fingerprints and embeddings, LSH bands in an indexed table, observations, reports, signatures and graph edges. Graph queries use recursive CTEs, and the graph worker loads subgraphs into networkx for clustering and lineage.

## Backend

The backend is Python with FastAPI, so the API and the workers share code. Narratives are clustered with HDBSCAN on embeddings, and clusters in consecutive time windows are matched by centroid similarity.

## Report analysis

The analysis worker sends each report to the Claude API. It extracts the core claim, classifies the manipulation techniques and writes the plain-language explanation that ships with the signature.

## Extension

The extension is a Manifest V3 extension written in TypeScript. Fingerprinting and the heuristic classifier run in a Web Worker so the page stays responsive.
