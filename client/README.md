# Trust Verifier: browser extension

Users select text on a page and rate it (misinformation / manipulative / misleading / trustworthy). The extension fingerprints the text at sentence level, sends the fingerprint and the verdict to the backend, and highlights content on any page that the crowd has flagged.

The design, the API contract and the milestones are in [PLAN.md](PLAN.md).

## Develop with Docker (nothing to install but Docker)

```bash
docker compose up                        # dev server with hot reload
docker compose run --rm dev npm test     # unit tests
docker compose run --rm dev npm run build  # production build -> dist/chrome-mv3
docker compose down -v                   # stop and delete the node_modules volume
```

With `docker compose up` running:

1. Go to `chrome://extensions` and turn on Developer mode.
2. Click "Load unpacked" and choose `client/dist/chrome-mv3-dev`.
3. Edit code. The container rebuilds and reloads the extension over port 3000.

Dependencies install into a Docker volume when the container starts, so nothing goes onto your machine. After changing `package.json`, just restart the container.

## Develop locally (Node ≥ 22.12)

```bash
npm install
npm run dev            # Chrome with hot reload (opens a browser with the extension loaded)
npm run dev:firefox
npm run build          # production build -> dist/chrome-mv3
npm test               # unit tests (fingerprinting, aggregation, mock backend)
npm run compile        # typecheck
```

To load the build manually, go to `chrome://extensions`, turn on Developer mode, click "Load unpacked" and choose `dist/chrome-mv3`.

## End-to-end test

`npm run test:e2e` builds the extension in development mode (`dist/chrome-mv3-dev`, whose UI shadow root is open) and runs `e2e/smoke.spec.ts` with Playwright. The test loads the extension into Chromium, serves `e2e/fixture.html` and a stub `/lookup` from a local server, and checks the exact and edited-copy highlights and the pinned tooltip.

Chromium needs system libraries that a plain machine or the VM may lack. `e2e/Dockerfile` provides them:

```bash
docker build -t trust-verifier-e2e -f e2e/Dockerfile e2e
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/work" -w /work trust-verifier-e2e npm run test:e2e
```

Run both from `client/` after `npm install`; the container uses the mounted `node_modules`.

## How it works

- **Content script** (`entrypoints/content/`):
  - `textModel.ts` groups text nodes into blocks and maps text offsets to DOM Ranges.
  - `scanner.ts` hashes every sentence on the page and computes each block's content hash and MinHash LSH bands, looks them up, and rescans changed DOM through a MutationObserver.
  - `highlighter.ts` paints flagged sentences, and blocks matched as edited copies, with the CSS Custom Highlight API, so the page DOM is never modified.
  - `ui.ts` holds the rate button, verdict picker, tooltip and toast, all in a Shadow DOM.
- **Background** (`entrypoints/background.ts`): every network call goes through here. It picks the mock or the remote backend from the settings and adds the anonymous `userId`.
- **Popup** (`entrypoints/popup/`): flagged items on the current page, stats for the domain, and settings.
- **Shared** (`lib/`):
  - `normalize.ts` and `fingerprint.ts` do normalization, sentence splitting and SHA-256 hashing; `minhash.ts` computes MinHash signatures and LSH bands.
  - `api.ts` has `RemoteApi` and `MockApi`.
  - `aggregate.ts` turns vote counts into a flagged / not-flagged result.
  - `types.ts` defines the shared types.

## Backend

The extension defaults to a **mock backend** that stores votes in `chrome.storage.local`, and a single vote is enough to flag content. To use the real backend, open the popup, go to Settings, choose **Remote** and enter the base URL. The endpoints it needs are in `docs/api.md`. The mock matches sentences only.

## Notes

- Sentences match exactly once normalized: case, punctuation and whitespace don't matter, but any change in wording does. Sentences under 5 words are ignored.
- A block (a paragraph, roughly) also matches as an edited copy when its LSH bands overlap a known narrative's. It gets a dashed underline instead of a sentence highlight, unless one of its sentences matches exactly.
- A partial selection snaps to the full sentences it touches.
- Lookups send only hashes and LSH bands, never page text. Votes include the selected text unless "Send selected text" is turned off.
- Dev builds open the UI shadow root (`mode: 'open'`) so tests and devtools can inspect it. Production builds keep it closed.
