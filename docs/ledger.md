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

## Deferred

Nothing yet.
