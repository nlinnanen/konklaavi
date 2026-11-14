# junction26 — Crowdsourced Content Trust Verifier

> Context document for humans and LLMs working on this repo. Keep it up to date as decisions are made.

## What we're building

A crowdsourced system for flagging misinformation and manipulative content on the web.

1. **Users vote on content** — A browser extension lets users select content on any website (text, claims, images, etc.) and give a verdict such as *misinformation*, *manipulative*, *misleading*, or *trustworthy*.
2. **Content is fingerprinted** — The extension hashes and/or embeds the selected content so the same (or near-identical) content can be recognized again, even when it appears on other pages or domains.
3. **Verdicts are aggregated** — The fingerprint, verdict and metadata (URL, domain, timestamp) are sent to the backend, which stores the votes and combines them into a trust signal per piece of content.
4. **Content is flagged in place** — When a user visits a page, the extension checks its content against the backend and highlights anything the crowd has flagged.
5. **Sources are traced** — Aggregated data shows which domains and sources originate or spread flagged content.

## Repository layout

| Folder        | Purpose |
|---------------|---------|
| `client/`     | Browser extension: content selection, voting UI, hashing/embedding, on-page flagging. |
| `backend/`    | API and storage: receives votes, stores fingerprints and verdicts, aggregates trust scores, serves lookups, does domain-level analysis. |
| `simulation/` | Simulation and testing of the system, e.g. synthetic users, votes and content to test aggregation and resistance to manipulation. *(TBD)* |

## Key concepts

- **Fingerprint**: How a piece of content is identified across pages.
  - *Exact hashes* (e.g. SHA-256 of normalized text) catch identical copies.
  - *Embeddings / fuzzy hashes* catch paraphrased or slightly edited copies.
  - Which approach (or mix) to use is still open.
- **Verdict**: A user's label for a piece of content (misinformation, manipulative, etc.).
- **Trust score**: The aggregated result of many verdicts on one fingerprint.
- **Source attribution**: Linking flagged content to the domains where it appears, and especially where it first appeared.

## Open questions / design considerations

- Hashing vs. embedding (or both) for content matching. How to normalize content before hashing.
- Vote aggregation that resists brigading and Sybil attacks (reputation, rate limits, weighting).
- Privacy: what user and browsing data is sent to the backend, and how to minimize it.
- Lookup performance: how the extension checks a page's content without sending the whole page.
- Tech stack for the extension, backend and storage (e.g. a vector DB for embeddings): not yet decided.

## Status

Early stage. The project is being built for Junction 2026. The folders exist, but the implementation hasn't started yet.
