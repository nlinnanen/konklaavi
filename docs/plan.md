# Build plan

HashTrust has two halves. Fingerprint tracking recognises that a passage belongs to a narrative and where the narrative has spread, without judging it. Context notes let signed-in readers attach context to a narrative, and the Community Notes bridging algorithm decides which notes are shown. Every label comes from the crowd. The decisions behind this are in [ledger.md](ledger.md); the contracts are in [api.md](api.md).

## Narratives

A narrative is born when a signed-in user selects a passage and adds a note. The passage is sent with the note, which is an explicit act. The backend fingerprints the passage at once, fetches the public page, and tracks the narrative from then on: extension users' on-device matches report detection hits, and the backend fetches those pages, so the narrative's spread grows by itself. There is no seed content in the product database.

## Notes and consensus

- A note on a narrative says whether the narrative is misleading or not misleading, explains the context and links its sources.
- Signed-in users rate notes as helpful, somewhat helpful or not helpful. Ratings from unverified installs are kept but don't count.
- A periodic job fits the Community Notes matrix factorisation, rating ≈ μ + user bias + note bias + user factor × note factor, and sets each note's status: needs more ratings, helpful, not helpful, or controversial when raters split along the factor without agreeing.
- A narrative's display state follows its notes: misleading when a misleading note is helpful, context when a not-misleading note is helpful, controversial, needs ratings, or tracked when it has no visible note.
- Reputation follows track record: writers gain it when their notes become helpful and lose it when they become not helpful; raters gain it when their early ratings agree with the outcome.

## Moderation

Each submitted note goes through an LLM review for abuse, spam and missing sources, which can hold it for review. Held and user-reported notes wait in a review queue that volunteer moderators work through in the dashboard. Writing notes requires a verified account.

## Benchmark

The EUvsDisinfo dataset stays as an offline benchmark of narrative recognition: whether fingerprints and embeddings recognise articles carrying the same claim across sites. It runs against its own database, never the product's, and only when someone starts it by hand.

## Slice: context notes

- core: notes, ratings and the scoring job; reputation; the moderation review and queue; `/index`, `/lookup` and `/check` ship narratives with their note state; removal of votes, settlement, source verdicts, LLM explanations and paraphrases.
- pipeline: narratives created from note passages; tracking through fetches and hits; removal of the settlement job.
- ext: "Add context" on a selection, the tooltip with the narrative's spread, its notes and rating buttons, highlight states, sign-in for writing and rating, the slow mode switch, and a redesigned, nicer interface.
- data: the dashboard's notes overview, narrative spread and moderation queue; the seed importers become the offline benchmark; removal of source-based pages.
- integrator: merge, deploy locally, clear the seed content from the local database, and rewrite the docs for the new product.

## Testing conventions

- Tests only for logic that would fail silently or that the demo depends on, such as the scoring job and note state.
- Database code is tested against the real Timescale container, never mocks.
- While iterating, run only the touched test file; run the full suite once before committing.
- No full-scale imports, evaluations or simulations in any build or merge step.
