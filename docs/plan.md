# Build plan

The plan gets to a deployed end-to-end demo as early as possible and then deepens it. Every milestone ends with something that runs on the Hostinger URL and can be shown to the judges. Four workstreams run in parallel against contracts fixed in the first two hours. The design is in [north-star.md](north-star.md), [components.md](components.md) and [decisions.md](decisions.md).

## Starting point

The extension in `client/` already does most of the browser work, described in [client/PLAN.md](../client/PLAN.md):

- A DOM text model that groups text into blocks and maps offsets back to ranges.
- Sentence splitting with `Intl.Segmenter`, normalisation and SHA-256 per sentence.
- Highlighting through the CSS Custom Highlight API, with a tooltip, rate button, verdict picker and popup.
- A `TrustApi` interface with a mock and a remote implementation, and the `POST /votes`, `POST /lookup` and `GET /domains/:domain` contract.

The plan builds on this. The backend implements that contract first, so the extension works against a real server on day one. New capabilities arrive as optional fields on the existing types and as new `TrustApi` implementations, not as renames.

## One fingerprint implementation

The fingerprint code in `client/lib/` is the only implementation. The backend runs it in a small Node service that the Python worker calls over HTTP, so sentence splitting, normalisation, MinHash and embeddings produce identical output on both sides. transformers.js runs the same embedding model in Node and in the browser.

## Contracts (hours 0 to 2)

Everyone works on these together before splitting up:

- API: the existing contract from `client/lib/types.ts`, plus an optional `signature` field on `LookupResult` carrying the narrative id, techniques, explanation and source (an EUvsDisinfo case or a user report).
- Schema: the SQL in [data-model.md](data-model.md) as migration files, with the jobs table.
- Fingerprint service: `POST /fingerprint` taking text and returning sentences with hashes, and later block MinHash signatures and embeddings.
- Compose: the root `compose.yaml` with Timescale, the API, the worker and the fingerprint service, deployed to the VPS once with an empty database.

## Milestones

### M1, exact matching on real data (by hour 7)

The demo: open an article from the Pravda network and see sentences highlighted, with the EUvsDisinfo case summary in the tooltip.

- Extension: point Remote mode at the deployed API, and show `signature` in the tooltip when it is present.
- Content pipeline: the fingerprint service wrapping `client/lib/fingerprint.ts`, and the fetch job that extracts text with trafilatura and stores sentence hashes.
- Backend core: the API implementing `/votes`, `/lookup` and `/domains/:domain` on Postgres.
- Data and evaluation: the EUvsDisinfo seed importer, which creates narratives with verdicts and enqueues fetch jobs for the articles each case links to.

### M2, edited copies, narratives and explanations (by hour 13)

The demo: an edited copy of a seeded article on another site still matches, and a fresh user report comes back within a minute with named techniques and a plain-language explanation.

- Extension: MinHash per block alongside the sentence hashes, and the techniques and explanation in the tooltip.
- Content pipeline: MinHash and LSH bands in the fingerprint service, the variant matcher, and `DERIVED_FROM` lineage.
- Backend core: the analysis job calling Claude through the AI Router, and LSH band lookup in `/lookup`.
- Data and evaluation: embeddings, HDBSCAN narrative clustering, and the evaluation split of EUvsDisinfo cases into seed and test sets.

### M3, the graph and the numbers (by hour 19)

The demo: the dashboard shows a narrative's lineage from its first account to mainstream sites, its spread over time, and the precision and false-positive rate on held-out cases.

- Extension: a `LocalIndexApi` that downloads the signature index and matches on the client, behind the same `TrustApi` interface, plus detection hits sent as telemetry.
- Content pipeline: fetch jobs triggered by detection hits, so the graph grows where narratives appear.
- Backend core: the index endpoint, the signature lifecycle, and the observation hypertable with continuous aggregates.
- Data and evaluation: the dashboard with the lineage tree and spread charts, and the simulation of honest, partisan and coordinated users.

### M4, freeze and pitch (hours 19 to 24)

Feature freeze at hour 21. The rest goes to bug fixes on the demo path, reseeding the production database, the evaluation slide and rehearsing the demo.

## Demo script

1. Open a Pravda-network article. Sentences are highlighted, and the tooltip names the narrative, its techniques and the EUvsDisinfo case.
2. Open a site that republished it with edits. It still matches as a variant.
3. Select a sentence on a fresh page and report it. Within a minute the highlight appears with an LLM explanation.
4. Open the dashboard. Show the narrative's lineage, its spread over time and the accounts that posted it first.
5. Show the evaluation: precision and false-positive rate on held-out cases, and how far a simulated brigade moves a verdict.
