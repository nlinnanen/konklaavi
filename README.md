# Konklaavi

Konklaavi is a content trust layer for the browser. The browser extension recognises passages that belong to a tracked narrative using sentence hashes (exact copies), MinHash bands (edited copies) and on-device embeddings (the same claim in other words), and displays useful context that other users have added. Readers add notes to a selected passage and rate other readers' notes, and the Community Notes bridging algorithm decides which note is shown. The backend tracks where each narrative spreads: extensions report the pages it is seen on, and the backend fetches those public pages to record where it appeared first and how far it has spread.

Built for Junction 2026 for Hostinger's "Decode the Noise" challenge.

## Try it

The demo deployment is at **https://konklaavi.tech/**: the web demo is at the root, the API under `/api` and the moderators' admin panel at `/admin`.

### Browser extension

The extension isn't in a web store, so you load it unpacked. That needs no review and takes a minute.

1. Download a zip from the [`latest-demo` release](https://github.com/nlinnanen/konklaavi/releases/tag/latest-demo): `konklaavi-chrome.zip` for Chrome, Edge or Brave, `konklaavi-firefox.zip` for Firefox 140 or later. Every deploy of `main` rebuilds them against the demo API.
2. Chrome, Edge or Brave: unzip it into a folder you keep, open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked** and choose the folder.
3. Firefox: open `about:debugging`, then **This Firefox** > **Load Temporary Add-on**, and pick the zip itself. Firefox removes a temporary add-on when it closes.
4. Pin Konklaavi from the toolbar's extensions menu and open an article.

Using it:

- **Highlights** mark passages that belong to a tracked narrative. Hover one to see the narrative, the note shown for it and where it has spread. The colour is the narrative's state, and a lighter highlight means the passage only matched by meaning, not by wording:

  | State | Meaning |
  |---|---|
  | Misleading | a note rated helpful says the narrative is misleading |
  | Context added | a note rated helpful adds context without calling it misleading |
  | Raters disagree | raters from different viewpoints don't agree on its notes |
  | Needs ratings | its notes are waiting for enough ratings |
  | Tracked | the narrative is tracked but no note is shown for it yet |

- **Sign in** from the popup to build reputation and to report notes; adding notes and rating them work without an account, which always counts at a new account's reputation. Sign-in doesn't verify the email address.
- **Select a passage** to add a note, saying whether it is misleading and citing sources. Rate other readers' notes as helpful, somewhat helpful or not helpful, or report one, which sends it back to moderation. A note needs at least five ratings. The bridging algorithm then rates it helpful only when raters who usually disagree both find it helpful; while most of its raters are new, plain agreement decides instead.
- **The popup** lists the matches on the current page, the account, the legend and the settings.

The extension matches on your device: it downloads the narrative index and compares the page against it locally, so page text leaves the browser only when you submit a note. When a passage matches, it reports the page's address and the matched narrative to the backend, which is how spread is tracked; **Report where narratives appear** in the popup's settings turns that off. The first match by meaning downloads the embedding model (`paraphrase-multilingual-MiniLM-L12-v2`, about 120 MB quantised; larger weights on Chrome with WebGPU) into the browser cache.

### Web demo (no extension needed)

**https://konklaavi.tech/** opens an article and shows what the extension would show on it: the highlights, the cards with readers' notes, rating and reporting notes, and adding a note to a selected passage (reporting needs sign-in, as in the extension). It runs the extension's own UI code, so the page looks as it would with the extension. A top bar marks the page as a demo copy and holds the address bar, a **Next narrative** button (or the N key) that jumps between highlights, and a link to the extension. A guided tour walks through the features on the first visit, and **How it works** in the bar replays it.

It is a demo, not the product. The server renders the article again in headless Chromium, so comments and other content its JavaScript loads are included, removes its scripts and sends its visible text to the API's `/check`; that takes about 5 to 10 seconds the first time, and some pages may not work. It opens the sites whose articles are scraped for narratives and notes: RT, Sputnik, Yle and BBC. A rendered page is kept for 15 minutes, so opening it again is instant.

To run it locally, with any site allowed (needs Node and `npm install` in `client/`, since the overlay is built from the extension's modules):

```sh
ALLOWED_DOMAINS='*' uv run demo/proxy-view/viewer.py    # then open http://localhost:8787
```

- It uses the demo API. Set `KONKLAAVI_API=http://localhost:8000` for a local backend. The first run downloads Chromium for Playwright.
- Add `&test=1` to a `/view?url=...` link to inject up to three labelled fake matches once the page's check succeeds. This checks that a site renders and highlights, whatever narratives it carries.
- Sites behind bot protection refuse the fetch; the demo shows an error page that says so.

## Documentation

- [North star](docs/north-star.md): what Konklaavi is for and where its limits lie.
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
- `demo/proxy-view/`: the web demo, which shows an article with the extension's highlights and cards without installing the extension.
- `tools/crowd/`: crowd bots, LLM personas (careful and casual readers, two political camps, trolls and a brigade) that read real news sites and write and rate notes through the public API, so scoring, moderation and reputation run on realistic data. Its [README](tools/crowd/README.md) covers running it.
- `deploy/` and `infra/ansible/`: the production compose file and Caddy configuration, and the Ansible playbooks that set up the VPS.
- `hackathons/`: the Junction 2026 challenge briefs.
- `system_overview.html`: a one-page visual overview of the system.

## Running locally

The stack runs with Docker Compose: Postgres with TimescaleDB and pgvector, the fingerprint service, the API, the worker and the dashboard.

```sh
cp .env.example .env
docker compose up -d --build
```

The API is on `http://localhost:8000`, the admin panel on `http://localhost:8000/admin` and the dashboard on `http://localhost:8501`. The admin panel signs in with an account's email and password; moderators are the accounts in `MODERATOR_EMAILS` or with enough reputation. The first start downloads the embedding model into the `models` volume. LLM moderation of notes runs on the Hostinger AI Router, which serves the Anthropic Messages API: set `HOSTINGER_ROUTER_KEY` in `.env` (or `ANTHROPIC_API_KEY` with `ANTHROPIC_BASE_URL` for another endpoint; the model is `MODERATION_MODEL`, by default `claude-haiku-5.5`). Without a key, new notes wait in the moderation queue for a moderator.

To load the extension against the local backend, build it from `client/` and load `client/dist/chrome-mv3` as an unpacked extension in `chrome://extensions`:

```sh
cd client
npm install
WXT_API_URL=http://localhost:8000 npm run build
```

Backend tests run with `uv run pytest` in `backend/`, which recreates a `trust_test` database on the compose Postgres, and extension tests with `npm test` in `client/`.
