# Decision ledger

Decisions made during the build, newest last, and nits deferred for later. Design decisions that shape the whole system are in [decisions.md](decisions.md).

## Decisions

- Work lands on the `integration` branch; `main` changes only through a pull request.
- Four workstreams (ext, pipeline, core, data) each work in their own worktree under `~/code/junction26/ws-<name>` on branch `ws/<name>`. The integrator merges them into `integration` at each milestone.
- The fingerprint code in `client/lib/` is the only implementation. The backend reaches it through the Node fingerprint service.
- The embedding model is `Xenova/paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensions, with no input prefix.
- The dashboard is a Streamlit app. Tiger Data's dashboards may replace its time-series panels later.
- The signature index ships as plain JSON lists of hashes and bands. A Bloom filter is an optimisation for later.
- Migrations run from a Python runner at API and worker startup, so the same files apply to a local container, the VPS and Tiger Cloud.
- Tests that touch the database run against the real Timescale container; each workstream's stack runs on its own ports.
- A job handler's writes and the job's `done` mark commit in one transaction; the claim commits before the handler runs.
- A failed job retries after 30 s, doubling per attempt; a job whose kind has no handler fails at once without retrying.
- The worker enqueues a periodic job only when its kind has a registered handler, and only when none of that kind is queued, running or created within its interval.
- Without `DATABASE_URL` or `FP_URL`, the backend uses localhost on `DB_PORT` and `FP_PORT`, so host-side runs follow the worktree's ports.
- Backend tests create a database `trust_test` in the compose Postgres; `TEST_DATABASE_URL` overrides it.
- The backend image installs uv with pip, because copying it from the uv image fails on this VM's Docker with "operation not supported".
- The fingerprint service runs TypeScript directly with tsx and its tests use `node:test`.

## Deferred

- A job left `running` by a crashed worker is never reclaimed; it needs a lease timeout.
- The fingerprint service builds with TypeScript 7 while the client stays on 5.9.
