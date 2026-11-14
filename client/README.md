# HashTrust: browser extension

The extension recognises passages that belong to a tracked narrative, by sentence fingerprints and on-device embeddings, and highlights them with the state their crowdsourced context notes give them. Signed-in readers select a passage to add a note (misleading or not misleading, with context and sources) and rate other readers' notes; the backend's Community Notes scoring decides which note is shown.

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
npm test               # unit tests
npm run compile        # typecheck
```

To load the build manually, go to `chrome://extensions`, turn on Developer mode, click "Load unpacked" and choose `dist/chrome-mv3`.

## End-to-end test

`npm run test:e2e` builds the extension in development mode (`dist/chrome-mv3-dev`, whose UI shadow root is open) and runs `e2e/smoke.spec.ts` with Playwright. The test loads the extension into Chromium, serves `e2e/fixture.html` and a stub backend (`/lookup`, `/index`, `/narratives`, `/notes`, `/me`, `/hits`) from a local server, and checks in remote mode the exact and edited-copy highlights, the pinned card with its note, source and rating, and a note added to a selected passage; in local index mode, the highlights and the posted detection hit.

Chromium needs system libraries that a plain machine or the VM may lack. `e2e/Dockerfile` provides them:

```bash
docker build -t hashtrust-e2e -f e2e/Dockerfile e2e
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/work" -w /work hashtrust-e2e npm run test:e2e
```

Run both from `client/` after `npm install`; the container uses the mounted `node_modules`.

## How it works

- **Content script** (`entrypoints/content/`):
  - `textModel.ts` groups text nodes into blocks and maps text offsets to DOM Ranges.
  - `scanner.ts` hashes every sentence on the page and computes each block's content hash and MinHash LSH bands, looks them up, and rescans changed DOM through a MutationObserver. It works in slices of a few milliseconds so the page keeps handling input, keeps the fingerprints when only the lookups must be redone (a backend switch, a new note), and embeds units for narrative matching only when the page is idle.
  - `highlighter.ts` paints matches with the CSS Custom Highlight API, so the page DOM is never modified, and finds the highlight under the pointer from the caret position, measuring only that block's ranges.
  - `ui.ts` holds the "Add context" button, the note form, the sign-in prompt, the highlight card and the toast, all in a Shadow DOM, with their own light and dark palettes.
- **Background** (`entrypoints/background.ts`): every network call goes through here. It picks the mock backend, the remote backend or the local index from the settings, adds the anonymous `userId`, remembers the install's own ratings, keeps the downloaded index in IndexedDB and in memory across mode switches, loads it as soon as local mode is chosen, and sends detection hits.
- **Offscreen document** (`entrypoints/offscreen/`): runs the embedding model with transformers.js for local index mode. Chrome's service worker can't load the ONNX runtime, so the background opens this document on the first embedding; Firefox runs the same code in its background page. The build copies the ONNX runtime's wasm files into `ort/`.
- **Popup** (`entrypoints/popup/`): the narratives on the current page, the account (sign-in, reputation, your notes and their status, your ratings), the highlight legend and settings.
- **Shared** (`lib/`):
  - `normalize.ts` and `fingerprint.ts` do normalization, sentence splitting and SHA-256 hashing; `minhash.ts` computes MinHash signatures and LSH bands.
  - `api.ts` has `RemoteApi`, `LocalIndexApi` with its `NarrativeIndex`, `MockApi` and the `HitReporter`.
  - `state.ts` has a narrative's display state from its notes, the match ranking and the highlight styles.
  - `settings.ts` holds the settings, with the backend mode and the detection reports switch.
  - `types.ts` defines the shared types.

## Backend

The popup's Settings choose one of three backends:

- **Mock** stores notes and ratings in `chrome.storage.local`, with the install always signed in. A note's status is the mean of its ratings (helpful from 0.7, not helpful up to 0.3, raters disagree between), so one rating is enough to try each state. It matches sentences, and blocks by content hash.
- **Remote** sends every lookup to the backend at the base URL.
- **Local index** downloads the backend's narrative index (`GET /index`), refreshes it with deltas once it is five minutes old, and matches on the device, so no page hashes leave the browser. Embedding units (every sentence, and every run of 3 consecutive sentences of a block) that match nothing exactly or as an edited copy are embedded on the device, at most 400 per scan pass, and match a narrative at the narrative level when their cosine to one of its vectors reaches `narrativeThreshold`, or as related from `relatedThreshold`. The first embedding downloads the model (`Xenova/paraphrase-multilingual-MiniLM-L12-v2`, q8, about 120 MB) from the Hugging Face hub into the browser's cache; until then, and if it fails, only exact and edited-copy matches show. When a lookup matches a narrative above the related level on an http(s) page, the extension reports a detection hit (`POST /hits`) with the hash, narrative and page URL, once per hash and page, unless "Report where narratives appear" is off.

A build defaults to Mock, or to Local index from the backend in `WXT_API_URL` when it is set (`WXT_API_URL=https://example.org/api npm run build`). The endpoints are in `docs/api.md`.

## Notes

- Sentences match exactly once normalized: case, punctuation and whitespace don't matter, but any change in wording does. Sentences under 5 words are ignored.
- A block (a paragraph, roughly) also matches as an edited copy when its LSH bands overlap a known narrative's.
- A highlight shows its narrative's state: misleading (a strong vermilion fill and underline, with the helpful note), context (a calm blue fill, with the note), raters disagree (grey), needs ratings (a faint wash) and tracked (a subtle dotted underline, "Part of a narrative seen on N sites since a date"). Narrative-level and related matches, which need Local index mode, are drawn lighter than exact copies and edited copies. Each sentence takes its strongest match (match level first, then the more urgent state), so highlights never overlap. The popup has the legend.
- Hovering a highlight shows its card; clicking pins it, loads the narrative's current notes and offers Helpful, Somewhat and Not helpful on each note, "Report" and "Add a note". Ratings are stored for any install but count only from signed-in ones.
- "Add context" on a selection snaps it to the full sentences it touches and needs a signed-in account; the card offers to email a sign-in link otherwise. The passage text is sent with the note, which is the only time page text leaves the browser.
- Lookups send only hashes and LSH bands, never page text; in local index mode they send nothing, and detection hits send the matched hash and the page URL.
- Dev builds open the UI shadow root (`mode: 'open'`) so tests and devtools can inspect it. Production builds keep it closed.
