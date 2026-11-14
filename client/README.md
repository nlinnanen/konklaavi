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

## How it works

- **Content script** (`entrypoints/content/`):
  - `textModel.ts` groups text nodes into blocks and maps text offsets to DOM Ranges.
  - `scanner.ts` hashes every sentence on the page, looks the hashes up, and rescans changed DOM through a MutationObserver.
  - `highlighter.ts` paints flagged sentences with the CSS Custom Highlight API, so the page DOM is never modified.
  - `ui.ts` holds the rate button, verdict picker, tooltip and toast, all in a Shadow DOM.
- **Background** (`entrypoints/background.ts`): every network call goes through here. It picks the mock or the remote backend from the settings and adds the anonymous `userId`.
- **Popup** (`entrypoints/popup/`): flagged items on the current page, stats for the domain, and settings.
- **Shared** (`lib/`):
  - `normalize.ts` and `fingerprint.ts` do normalization, sentence splitting and SHA-256 hashing.
  - `api.ts` has `RemoteApi` and `MockApi`.
  - `aggregate.ts` turns vote counts into a flagged / not-flagged result.
  - `types.ts` defines the shared types.

## Backend

The extension defaults to a **mock backend** that stores votes in `chrome.storage.local`, and a single vote is enough to flag content. To use the real backend, open the popup, go to Settings, choose **Remote** and enter the base URL. The endpoints it needs are in PLAN.md.

## Notes

- Matching is exact per normalized sentence: case, punctuation and whitespace don't matter, but any change in wording does. Sentences under 5 words are ignored.
- A partial selection snaps to the full sentences it touches.
- Lookups send only hashes, never page text. Votes include the selected text unless "Send selected text" is turned off.
- Dev builds open the UI shadow root (`mode: 'open'`) so tests and devtools can inspect it. Production builds keep it closed.
