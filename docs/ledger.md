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
- integrator: backend tests read the worktree's `.env` for variables not already exported, so a bare `uv run pytest` reaches the worktree's stack.
- integrator: the `live` pytest marker is registered in `backend/pyproject.toml`.
- integrator: ext owns `client/package.json`, `client/README.md` and `client/.gitignore`.
- integrator: the `dashboard` compose service builds `./dashboard` and serves on `DASH_PORT`.
- pipeline: the fetch job does not retry a page that fails to load or returns non-200; it records the page as `private` or `failed`. A fingerprint service error raises, so the job retries.
- pipeline: a page is stored under the payload URL, not the redirect target, and its domain comes from the payload URL.
- pipeline: one block is one non-empty line of trafilatura's main text; blocks without a contentHash are skipped.
- pipeline: extraction leaves out comments (`include_comments=False`).
- pipeline: every fetch writes one `fetch` observation per fingerprint, so a refetch adds observations but no fingerprints.
- pipeline: narrative attachment creates one variant per narrative per page holding the page's fingerprints that have no variant yet.
- pipeline: `published_at` holds the date trafilatura extracts, without a time.
- core: votes alone flag a hash with at least 2 votes and a negative share of at least 0.5 (the extension's `FLAG_THRESHOLD`).
- core: a hash in an active narrative is flagged even when every vote is trustworthy; `topVerdict` follows the votes when they flag, else the narrative's verdict, else the top verdict overall.
- core: `/lookup` returns a result only for hashes with votes or an active or under_review signature; a signature-only hash has zero counts.
- core: when a hash belongs to several narratives, the signature comes from the active one first, then the most recently updated.
- core: domain `flaggedCount` uses the 2-vote minimum per contentHash; domain `topVerdict` needs 1 vote and is null without votes.
- core: every vote enqueues `analyze`, which does nothing for trustworthy votes and for votes already attached to a narrative; a vote without an excerpt only attaches to an existing narrative.
- core: attaching a vote to an existing narrative never calls the LLM; a missing key fails only narrative creation.
- core: concurrent narrative creation is serialised by a transaction-level advisory lock taken after the LLM call, with a recheck under the lock.
- core: a new report narrative reuses an existing fingerprint with the vote's contentHash and its variant.
- core: analysis uses `output_config` json_schema structured output with default model `claude-opus-5-5`, without the server-side fallbacks beta.
- core: `/check` splits text input on blank lines and URL input into one block per trafilatura line; a failed fetch returns 502 and an empty request 422.
- data: EUvsDisinfo cases come from the Hugging Face dataset `NLP-UniBW/eu_vs_disinfo` through the datasets-server rows API, since euvsdisinfo.eu blocks bots and the Zenodo CSV lacks case text.
- data: the seed file `backend/app/seed/data/euvsdisinfo.jsonl` holds the 400 newest cases with a title, summary, disproof and an http(s) article URL.
- data: a case is a test case when the first 8 bytes of sha256(case id) mod 100 is below 20; only seed cases become narratives, test cases go to `eval_case`.
- data: the importer skips a narrative with the same source and source_url, enqueues fetch jobs only for new narratives, and runs in one transaction under an advisory lock.
- data: `--limit N` takes the first N cases of the file before the split.
- data: the dashboard is its own uv project with its SQL in `dashboard/queries.py`, which a backend test runs against the real schema.
- data: dashboard counts follow variant -> fingerprint -> observation -> page; first seen is min(fingerprint.first_seen).
- ext: the mock backend flags a fixed narrative sentence (`MOCK_NARRATIVES`) with its signature and no votes.
- ext: flagged content shows the signature's verdict when present, otherwise `topVerdict`, in highlight colour, tooltip heading and popup chip.
- ext: a narrative link is followed only for http/https URLs (`sourceHref`).
- ext: clicking a highlight pins its tooltip; Escape, a click elsewhere or scrolling unpins it, and a click ending a selection does not pin.
- ext: the e2e test sets remote mode by writing settings from the service worker and asserts on `CSS.highlights`.
- ext: Playwright runs from `client/e2e/playwright.config.ts`; `npm run test:e2e` builds first.
- ext: `client/e2e/Dockerfile` runs the e2e test where Chromium's system libraries are missing, including the VM.
- integrator: A fetched page joins a narrative only through blocks that are relevant to the claim by embedding similarity and that are not boilerplate (seen on 2+ pages of one domain).
- integrator: Production runs behind Caddy on the VPS's free domain, with `/` routed to the dashboard and `/api/*` to the API, because the extension needs an HTTPS API URL.
- integrator: The core workstream owns the job lease change in `backend/app/jobs/__init__.py`.

## Deferred

- A job left `running` by a crashed worker is never reclaimed; it needs a lease timeout.
- The fingerprint service builds with TypeScript 7 while the client stays on 5.9.
- pipeline: M2 decides which blocks are relevant before attaching them to a narrative; every main-text block is attached, so page boilerplate carries the narrative's signature.
- pipeline: minhash and lsh_band rows and the `match_variants` enqueue wait for M2 bands.
- pipeline: a fingerprint already in another narrative's variant is not attached to the payload's narrative.
- pipeline: pages that fail to load are marked failed and never refetched.
- pipeline: `vote_id` and `reason` in the fetch payload are unused.
- core: `/check` has its own httpx and trafilatura extraction instead of the fetch job's.
- core: whether the Hostinger AI Router passes `output_config` through is unverified; the fallback is strict tool use.
- core: a report narrative gets no variant when the existing fingerprint's variant belongs to another narrative.
- core: a vote edited to trustworthy keeps its `narrative_id`.
- core: `/check` does not dedupe repeated sentences.
- core: `backend/tests/core/conftest.py` still registers the `live` marker that `pyproject.toml` registers.
- data: the Leite et al. trustworthy URLs, negatives for the M3 false-positive rate, are not imported.
- data: disproof text keeps EUvsDisinfo's boilerplate closing paragraphs.
- data: `narrative(source, source_url)` has no index; a partial unique index would let the importer use ON CONFLICT.
- ext: the e2e test covers only highlighting; tooltip pinning and the popup were checked by hand.
- ext: the production build closes the shadow root, so e2e cannot inspect the tooltip without a development build.
- ext: `client/README.md` does not mention `npm run test:e2e` or the e2e Dockerfile.
- ext: `npm install` drops optional peer entries (detect-libc, lightningcss) from `client/package-lock.json`.
- ext: `sourceHref`, `SOURCE_LABELS` and `displayVerdict` sit in `client/lib/messages.ts`; a `client/lib/signature.ts` would fit them better.
- pipeline: `splitSentences` treats a newline in DOM text as a sentence break, so a sentence wrapped in the HTML source never matches; replacing line breaks with spaces before segmenting keeps offsets but may change stored hashes.
- integrator: `splitSentences` treats a newline as a sentence break; changing it changes stored hashes, so it waits for a parity check across the extension and the service.
- integrator: The unique index on `narrative(source, source_url)` and moving signature helpers to `client/lib/signature.ts` wait until they are needed.
