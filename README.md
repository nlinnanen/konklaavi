# junction26 — Content Trust Layer

> Context document for humans and LLMs working on this repo. Keep it up to date as decisions are made.

Network-level security such as WAFs stops bad requests, but it cannot see bad content that arrives through valid, encrypted requests. This project adds the layer inside the browser: an extension that recognises known propaganda narratives as the user reads, and a backend that tracks how those narratives spread and mutate across the web. It works like antimalware for narratives. The backend keeps an index of known narrative signatures, the extension fingerprints page content and matches it locally against that index, and user reports feed new narratives into the index.

Built for Junction 2026, on the "Trust the source" challenge direction.

## Documentation

- [North star](docs/north-star.md): what we are building and why.
- [Architecture](docs/architecture.md): page scanning, reporting and the signature lifecycle.
- [Components](docs/components.md): C4 diagrams, component list, contracts and workstreams.
- [Data model](docs/data-model.md): fingerprint levels, the narrative graph and the storage schema.
- [Technical decisions](docs/decisions.md): fingerprinting, client-side matching, storage and the stack.

## Repository layout

- `client/`: browser extension that chunks, fingerprints and matches page content, highlights matches and sends reports.
- `backend/`: API, worker, seed importer and the Postgres database.
- `simulation/`: synthetic users, reports and content for measuring precision, false-positive rate and resistance to brigading.

## Key concepts

- Fingerprint: how a chunk of content is identified. SHA-256 catches exact copies, MinHash over shingles catches edited copies and an embedding catches paraphrases.
- Variant: a group of fingerprints that are edited copies of each other.
- Narrative: a group of variants that make the same claim.
- Signature: the shipped form of a narrative, with its verdict, techniques, explanation and match data.
- Report: a user's explicit submission of content they find manipulative. It is the only path through which text a user reads reaches the backend.

## Open questions

- Which threshold of reports and LLM verdicts promotes a candidate narrative to an active signature.
- Which multilingual embedding model fits the extension's size and speed budget.
- Which heuristic classifier, if any, runs in the browser for content that matches no signature.

## Status

The design is documented. Implementation has not started.
