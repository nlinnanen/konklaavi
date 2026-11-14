# Narrative recognition benchmark

An offline benchmark of whether fingerprints and embeddings recognise articles carrying the same claim across sites. It uses EUvsDisinfo cases: each seed case becomes a narrative, as if a note had been written on its claim, and fetch jobs for some of its article URLs; the article URLs it holds out, the URLs of whole held-out test cases, and the reliable-outlet pages in `../negatives.txt` are then fetched and looked up. `run.py` explains the sets and the threshold sweep.

The benchmark runs only against its own database, named by `BENCHMARK_DATABASE_URL`. `python -m app.eval.benchmark` points the app at it before anything connects, and refuses a URL naming the same database as `DATABASE_URL` (or `postgres` when that is unset). It fetches hundreds of pages, so it runs only when someone starts it by hand.

## Running it

From the worktree root, with the stack up. Create the database once:

```sh
docker compose exec db psql -U postgres -c 'CREATE DATABASE benchmark'
```

Import the cases, which also migrates the database (`--limit N` imports the first N):

```sh
docker compose run --rm -e BENCHMARK_DATABASE_URL=postgresql://postgres:postgres@db:5432/benchmark api python -m app.eval.benchmark import
```

Start a worker on the benchmark database to run the fetch jobs, and give it time to fetch the seed pages:

```sh
docker compose run -d --name benchmark-worker -e DATABASE_URL=postgresql://postgres:postgres@db:5432/benchmark worker
```

Run the benchmark, which fetches the evaluated pages through that worker and writes `backend/app/eval/results/latest.json`:

```sh
docker compose run --rm -v ./backend/app/eval/results:/app/app/eval/results -e BENCHMARK_DATABASE_URL=postgresql://postgres:postgres@db:5432/benchmark api python -m app.eval.benchmark run
```

Stop the worker afterwards:

```sh
docker rm -f benchmark-worker
```

`python -m app.eval.benchmark download --cases 400` rebuilds `data/euvsdisinfo.jsonl` from the Hugging Face dataset NLP-UniBW/eu_vs_disinfo.
