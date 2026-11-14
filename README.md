# HashTrust

HashTrust is a content layer for the browser. Its extension recognises passages that belong to a tracked narrative, by sentence hashes, MinHash bands and on-device embeddings, and highlights them with the state that crowdsourced context notes give the narrative. Signed-in readers add notes to a selected passage and rate other readers' notes, and the Community Notes bridging algorithm decides which note is shown. The backend tracks where each narrative spreads by fetching the public pages it is seen on.

Built for Junction 2026, on the "Trust the source" challenge direction.

## Try it

The demo deployment is at **https://srv2160986.hstgr.cloud/**: the dashboard is at the root, the API under `/api` and the moderators' admin panel at `/api/admin`.

### Browser extension

The extension isn't in a web store, so you load it unpacked. That needs no review and takes a minute.

1. Download the zip from the [`latest-demo` release](https://github.com/nlinnanen/junction26/releases/tag/latest-demo). Every deploy rebuilds it against the demo API.
2. Unzip it into a folder you keep.
3. Open `chrome://extensions` (or Edge or Brave), turn on **Developer mode** and click **Load unpacked**. Choose the unzipped folder.
4. Pin HashTrust from the puzzle-piece menu and open an article.

Using it:

- **Highlights** mark passages that belong to a tracked narrative. Hover one to see the narrative, the note shown for it and where it has spread. The colour is the narrative's state, and a lighter highlight means the passage only matched by meaning, not by wording:

  | State | Meaning |
  |---|---|
  | Misleading | a note rated helpful says the narrative is misleading |
  | Context added | a note rated helpful adds context without calling it misleading |
  | Raters disagree | raters from different viewpoints don't agree on its notes |
  | Needs ratings | its notes are waiting for enough ratings |
  | Tracked | the narrative is tracked but has no notes yet |

- **Sign in** from the popup to build reputation; notes and ratings work without it. The demo server skips email verification.
- **Select a passage** to add a note, saying whether it is misleading and citing sources. Rate other readers' notes as helpful, somewhat helpful or not helpful. The bridging algorithm shows a note only once raters who usually disagree both find it helpful.
- **The popup** lists the matches on the current page, the legend and the settings.

The extension matches on your device: it downloads the narrative index, and page text leaves the browser only when you submit a note. The first match by meaning downloads the embedding model (about 120 MB) into the browser cache.

### Web demo (no extension needed)

**https://srv2160986.hstgr.cloud/demo/** opens an article and shows what the extension would show on it: the highlights, the cards with readers' notes, rating and reporting notes, and adding a note to a selected passage. It runs the extension's own UI code. A side panel lists the matched narratives, a banner marks the page as a demo copy, and a short guide explains it on the first visit.

It is a demo, not the product. The server renders the article again in headless Chromium, so comments and other content its JavaScript loads are included, removes its scripts and sends its visible text to the API's `/check`; that takes 10 to 25 seconds, and some sites don't work. It opens Wikipedia, the major Finnish news sites (Yle, HS, Ilta-Sanomat, Iltalehti, MTV Uutiset and others), major international news sites (BBC, the Guardian, the New York Times, DW, Al Jazeera and others) and Reddit; the start page lists them. Some of them (Reddit, Reuters, AP, Euronews) show automated browsers a bot check, so they don't load. A rendered page is kept for 15 minutes, so opening it again is instant.

To run it locally, with any site allowed (needs Node and `npm install` in `client/`, since the overlay is built from the extension's modules):

```sh
ALLOWED_DOMAINS='*' uv run demo/proxy-view/viewer.py    # then open http://localhost:8787
```

- It uses the demo API. Set `HASHTRUST_API=http://localhost:8000` for a local backend. The first run downloads Chromium for Playwright.
- Add `&test=1` to a `/view?url=...` link to inject three labelled fake matches. This checks that a site renders and highlights, whatever narratives it carries.
- Sites behind bot protection refuse the fetch; the panel says so.

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
- `backend/`: the FastAPI API, the moderators' admin panel in `backend/app/admin/`, the job worker, SQL migrations and the offline benchmark in `backend/app/eval/benchmark/`.
- `dashboard/`: a Streamlit app for narratives, notes, the moderation queue and reputation. Moderators use the admin panel.
- `simulation/`: synthetic crowd runs against the API. They post to `/votes`, which the notes API does not serve.
- `demo/proxy-view/`: the web demo, which shows an article with the extension's highlights and cards without installing the extension.
- `deploy/` and `infra/ansible/`: the production compose file and the VPS configuration.

## Running locally

The stack runs with Docker Compose: Postgres with TimescaleDB and pgvector, the fingerprint service, the API, the worker and the dashboard.

```sh
cp .env.example .env
docker compose up -d --build
```

The API is on `http://localhost:8000`, the admin panel on `http://localhost:8000/admin` and the dashboard on `http://localhost:8501`. The admin panel signs in with an account's email and password; moderators are the accounts in `MODERATOR_EMAILS` or with enough reputation. The first start downloads the embedding model into the `models` volume. LLM moderation of notes needs `ANTHROPIC_API_KEY` in `.env`; without it new notes wait in the moderation queue.

To load the extension against the local backend, build it from `client/` and load `client/dist/chrome-mv3` as an unpacked extension in `chrome://extensions`:

```sh
cd client
npm install
WXT_API_URL=http://localhost:8000 npm run build
```

Moderator rights go to the emails in `MODERATOR_EMAILS`. Backend tests run with `uv run pytest` in `backend/`, which recreates a `trust_test` database on the compose Postgres, and extension tests with `npm test` in `client/`.
