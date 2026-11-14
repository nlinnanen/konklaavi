# HashTrust

HashTrust is a content layer for the browser. Its extension recognises passages that belong to a tracked narrative, by sentence hashes, MinHash bands and on-device embeddings, and highlights them with the state that crowdsourced context notes give the narrative. Signed-in readers add notes to a selected passage and rate other readers' notes, and the Community Notes bridging algorithm decides which note is shown. The backend tracks where each narrative spreads by fetching the public pages it is seen on.

Built for Junction 2026, on the "Trust the source" challenge direction.

## Documentation

- [North star](docs/north-star.md): what HashTrust is for and where its limits lie.
- [Architecture](docs/architecture.md): scanning a page, adding and rating notes, scoring and moderation.
- [Components](docs/components.md): container and component diagrams and what each part does.
- [Data model](docs/data-model.md): fingerprint levels, the narrative graph and the storage schema.
- [API contracts](docs/api.md): HTTP API, fingerprint service, jobs and configuration.
- [Technical decisions](docs/decisions.md): the choices behind fingerprinting, matching, scoring and storage.
- [Evaluation](docs/evaluation.md): the offline narrative recognition benchmark.
- [Deployment](docs/deployment.md): images, the VPS and releases.

## Layout

- `client/`: the browser extension (WXT, TypeScript) and the fingerprint library in `client/lib/` that the extension and the fingerprint service share. Its [README](client/README.md) covers development.
- `fingerprint-service/`: a Node service that runs the fingerprint library and the embedding model for the backend.
- `backend/`: the FastAPI API, the job worker, SQL migrations and the offline benchmark in `backend/app/eval/benchmark/`.
- `dashboard/`: a Streamlit app for narratives, notes, the moderation queue and reputation.
- `simulation/`: synthetic crowd runs against the API. They post to `/votes`, which the notes API does not serve.
- `deploy/` and `infra/ansible/`: the production compose file and the VPS configuration.

## Running locally

The stack runs with Docker Compose: Postgres with TimescaleDB and pgvector, the fingerprint service, the API, the worker and the dashboard.

```sh
cp .env.example .env
docker compose up -d --build
```

The API is on `http://localhost:8000` and the dashboard on `http://localhost:8501`. The first start downloads the embedding model into the `models` volume. LLM moderation of notes needs `ANTHROPIC_API_KEY` in `.env`; without it new notes wait in the moderation queue.

To load the extension against the local backend, build it from `client/` and load `client/dist/chrome-mv3` as an unpacked extension in `chrome://extensions`:

```sh
cd client
npm install
WXT_API_URL=http://localhost:8000 npm run build
```

Moderator rights go to the emails in `MODERATOR_EMAILS`. Backend tests run with `uv run pytest` in `backend/`, which recreates a `trust_test` database on the compose Postgres, and extension tests with `npm test` in `client/`.
