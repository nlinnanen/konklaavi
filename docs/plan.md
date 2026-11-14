# Build plan

One integrator (Claude on `main`) writes the contracts and a runnable skeleton, then runs four workstreams in parallel, each in its own git worktree with its own agent. Every milestone ends with a merged `main` that runs end to end. The design is in [north-star.md](north-star.md), [components.md](components.md) and [decisions.md](decisions.md).

## Step 0: contracts and skeleton

The integrator does this alone on `main`, before any workstream starts:

- `backend/migrations/000_init.sql`: the base schema. Workstreams add migrations in their own number range: 1xx core, 2xx pipeline, 3xx data.
- `backend/app/`: config, database pool, migration runner, the job runner with a handler registry and periodic jobs, and a FastAPI app with one router file per workstream.
- `fingerprint-service/`: a Node HTTP service that runs `client/lib` fingerprinting, with the exact level working.
- `client/lib/types.ts`: an optional `signature` on `LookupResult`.
- `docs/api.md`: the HTTP contract, the fingerprint service contract, and the job kinds and payloads.
- `compose.yaml` at the root: Timescale, the API, the worker and the fingerprint service. Ports come from a per-worktree `.env`, so all worktrees can run their stacks at once.

Step 0 is done when `docker compose up` applies migrations, `/health` answers, `/lookup` returns an empty result, and the fingerprint service returns the same hashes as the extension for the same text.

## Workstreams

Each workstream owns its files. Nobody edits another workstream's files; a needed change to someone else's file or to a contract goes to the integrator.

- ext: `client/entrypoints/`, `client/lib/api.ts`, the popup.
- pipeline: `client/lib/` fingerprinting (normalise, sentences, MinHash, embeddings), `fingerprint-service/`, and the fetch, variant and lineage jobs.
- core: the API routes for votes, lookup, domains, index and hits, the analysis job, signature lifecycle, continuous aggregates, Dockerfiles and deployment.
- data: the EUvsDisinfo importer, narrative clustering, the dashboard, the simulation and the evaluation.

## Milestones and deliverables

### M1: exact matching on real data

The demo: a Pravda-network article is highlighted, and the tooltip shows the EUvsDisinfo case.

- ext: Remote mode against the backend, the `signature` in the tooltip with a link to the source case.
- pipeline: the fingerprint service at the exact level, and the fetch job that fetches a page anonymously, extracts text with trafilatura, and stores the page, fingerprints, sentences and observations, attached to a narrative when the job carries one.
- core: `/votes`, `/lookup` and `/domains/:domain` on Postgres, matching the extension's existing contract. A vote enqueues a fetch and an analysis job.
- data: the EUvsDisinfo importer creating narratives and enqueuing fetch jobs for the linked articles, with cases split into a seed set and a test set.

### M2: edited copies, narratives and explanations

The demo: an edited copy on another site still matches, and a fresh report comes back with named techniques and a plain-language explanation.

- ext: MinHash bands per block in `/lookup`, technique labels in the tooltip.
- pipeline: MinHash and LSH bands in `client/lib` and the service, embeddings with transformers.js in the service, the variant matcher and `DERIVED_FROM` lineage.
- core: the analysis job calling Claude through the Hostinger AI Router, and band matching in `/lookup`.
- data: HDBSCAN narrative clustering on embeddings and `EVOLVED_INTO` edges.

### M3: graph, privacy and numbers

The demo: the dashboard shows a narrative's lineage and spread over time; the extension matches locally; the evaluation shows precision and false-positive rate.

- ext: `LocalIndexApi`, which downloads the index and matches on the client behind the `TrustApi` interface, and detection hits sent to `/hits`.
- pipeline: fetch jobs triggered by detection hits.
- core: `/index` and `/hits`, the signature lifecycle, continuous aggregates on observations, and the production compose file with a deployment runbook.
- data: the Streamlit dashboard (`dashboard/app.py`) reading Postgres directly, the simulation of honest, partisan and coordinated users, and the evaluation report.

## Integration

At the end of each milestone the integrator merges the four branches into `main`, runs every test suite and an end-to-end check against the compose stack, and pushes. Workstreams rebase on `main` before starting the next milestone. Contract changes happen only on `main`.

## Testing conventions

- Pure logic gets unit tests: Vitest for TypeScript, pytest for Python. One test per behaviour the code promises, not one per branch, as the `unit-tests` skill describes. No tests that restate configuration values.
- Code that touches the database is tested with pytest against a real Timescale container from the worktree's compose stack. No database mocks.
- Fingerprint parity has one cross-runtime test: the same texts through the extension's `client/lib` and through the fingerprint service must give identical hashes, bands and embeddings.
- The fetch job is tested against local HTML fixtures served in the test, never the internet.
- The analysis job is tested with a fake LLM client. One live test, marked `live`, runs only when an API key is set.
- The extension gets a Playwright smoke test that loads the built extension on a fixture page against a local backend and checks that a seeded sentence is highlighted.
- Every commit passes `npm test` and `npm run compile` in TypeScript packages and `uv run pytest` and `uv run ruff check` in `backend/`.
- Evaluation numbers are reported from the evaluation script's output, never retyped.

## Needs from the team

- A Hostinger AI Router key, set as `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL`. The analysis job fails when they are missing.
- Deployment to the VPS, from the runbook core writes.
