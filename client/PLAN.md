# Browser Extension — Implementation Plan

Scope: `client/` only. The backend doesn't exist yet. This plan defines the API contract the extension expects and ships a mock backend inside the extension, so the extension can be built and demoed on its own.

## Stack

- **[WXT](https://wxt.dev)**: a framework for Manifest V3 extensions, built on Vite. It gives hot reload in development and builds for both Chrome and Firefox from one codebase.
- **TypeScript** throughout.
- **Vanilla TS + Shadow DOM** for the UI injected into pages, so page CSS can't break it and it can't break the page.
- **Vanilla TS** for the popup too (no framework).
- **Vitest** for unit tests of the hashing and matching logic.

## Architecture

```
client/
  entrypoints/
    background.ts      # service worker: API client, cache, anonymous user id, mock backend
    content/           # content script: selection → vote UI, page scan → highlights
    popup/             # popup (vanilla TS): page summary, domain stats, settings
  lib/
    normalize.ts       # text normalization
    fingerprint.ts     # sentence splitting + SHA-256 hashing
    api.ts             # typed API client (real + mock implementations)
    types.ts           # Verdict, Vote, LookupResult, ...
    messages.ts        # typed messages between content script and background
```

The content script never talks to the network. Every API call goes through the background worker. This keeps CORS and API config in one place, and the background can cache results and switch between the real and mock backends.

## Fingerprinting (v1)

The core problem: a user votes on an arbitrary text selection, but a later visitor's page has to be matched without knowing what anyone selected.

Solution: match at the **sentence level**.

1. **Normalize**: Unicode NFKC, lowercase, collapse whitespace, strip punctuation and quote variants.
2. **Split into sentences**: drop fragments shorter than about 5 words, since they are too generic to match on.
3. **Hash**: `SHA-256(normalized sentence)` via WebCrypto gives the sentence hashes.
4. **Vote**: send the hash of the full selection plus its list of sentence hashes.
5. **Lookup**: the content script splits the page's visible text into sentences, hashes them, and sends the hashes in batches. The backend returns verdict aggregates for any hashes it knows.
6. **Highlight**: map the matched hashes back to DOM text ranges and highlight them.

Privacy property: lookups send only hashes, never page text.

**v2 (later):** add embeddings to catch paraphrased content. This means either a local model (transformers.js) or sending text to the backend. It's out of scope for v1, but the API has an optional `embedding` field reserved for it.

## Verdicts

`misinformation`, `manipulative`, `misleading`, `trustworthy`. Each vote can also carry an optional short comment.

## API contract (proposed for the backend)

```
POST /votes
  { userId, contentHash, sentenceHashes[], verdict, comment?, url, domain, excerpt?, createdAt }
  -> { ok: true }

POST /lookup
  { hashes[] }                         # batched, max ~500
  -> { results: [{ hash, counts: { misinformation, manipulative, misleading, trustworthy }, total, flagged: bool, topVerdict }] }
     # only hashes that have votes; topVerdict = top negative verdict when flagged, else top overall

GET /domains/:domain
  -> { domain, votesCount, flaggedCount, counts: {...}, topVerdict | null }
```

The backend must allow CORS from extension origins, e.g. `Access-Control-Allow-Origin: *`, with `Content-Type` allowed as a request header. The authoritative types are in `lib/types.ts`.

- **`userId`**: a random UUID generated on install and stored in `chrome.storage.local`. It is anonymous and lets the backend dedupe and rate-limit votes.
- **`excerpt`**: the selected text, sent only on a vote (not on a lookup), so the backend has something human-readable. There will be a setting to turn it off.

## Mock backend

`MockApi` in the background worker implements the same interface. It stores votes in `chrome.storage.local` and does the aggregation locally. A setting in the popup switches between the mock and a real backend URL. The mock is the default until the backend exists.

## Milestones

1. **Scaffold**: WXT + TypeScript. Builds, loads unpacked in Chrome.
2. **Fingerprint lib**: normalization, sentence splitting, hashing, with Vitest tests.
3. **Voting flow**: select text, a floating "Rate" button appears, a verdict picker (Shadow DOM) opens, the vote goes through the background to the mock API, and a confirmation shows.
4. **Page scan + flagging**: scan visible text on load, plus scan new content as it appears (MutationObserver, debounced), batch the lookups, highlight flagged sentences, and show a tooltip with the verdict breakdown.
5. **Popup**: flagged items on the current page, stats for the current domain, settings (backend mode/URL, send excerpt, highlight on/off).
6. **Real API client**: configurable base URL and error handling, ready to plug into the backend.

## Out of scope for v1

Embeddings or fuzzy matching, images and video, user accounts and reputation, resistance to mass fake voting (that belongs in the backend), publishing to extension stores.
