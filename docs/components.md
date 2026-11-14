# Components

The flows between these components are in [architecture.md](architecture.md), their contracts in [api.md](api.md) and the tables in [data-model.md](data-model.md). Arrows show the data that moves.

## Context

```mermaid
flowchart LR
  Reader(["Reader<br/>extension user"])
  Mod(["Moderator / analyst"])
  Sys["HashTrust"]
  Web["Public web pages"]
  LLM["Claude via Hostinger AI Router"]

  Reader -->|"notes, ratings, reports"| Sys
  Sys -->|"highlights with narrative state and notes"| Reader
  Sys -->|"spread, notes, moderation queue"| Mod
  Mod -->|"approve or hide"| Sys
  Web -->|page text| Sys
  Sys -->|note text| LLM
  LLM -->|"review: clean or flagged, reason"| Sys

  classDef ext fill:#999,stroke:#666,stroke-dasharray:4,color:#fff
  class Web,LLM ext
```

## Containers

```mermaid
flowchart LR
  Reader(["Reader"])
  Mod(["Moderator / analyst"])

  subgraph Sys["HashTrust"]
    Ext["Extension<br/>TypeScript, WXT, transformers.js"]
    API["API<br/>Python, FastAPI"]
    Worker["Worker<br/>Python"]
    FPS["Fingerprint service<br/>Node, client/lib"]
    DB[("Postgres<br/>TimescaleDB, pgvector")]
    Dash["Dashboard<br/>Python, Streamlit"]
  end

  Web["Public web pages"]
  LLM["Claude via Hostinger AI Router"]

  Reader <--> Ext
  Ext -->|"lookups, notes, ratings, reports, hits, sign-in"| API
  API -->|"lookup results, index deltas, notes, /me"| Ext
  API -->|"notes, ratings, observations, jobs"| DB
  API -->|"/check text"| FPS
  DB -->|jobs| Worker
  Worker -->|"pages, fingerprints, variants, vectors, note status"| DB
  Web -->|page text| Worker
  Worker -->|blocks| FPS
  FPS -->|"hashes, bands, embeddings"| Worker
  Worker -->|note text| LLM
  DB -->|"narratives, spread, queue, reputation"| Dash
  Dash -->|"moderation decisions, /check"| API
  Dash <--> Mod

  classDef ext fill:#999,stroke:#666,stroke-dasharray:4,color:#fff
  class Web,LLM ext
```

The job queue is a table in Postgres. Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, and the worker enqueues `cluster`, `narrative_vectors` and `score_notes` every five minutes.

## Extension

```mermaid
flowchart LR
  DOM[/Page DOM/] --> TM["Text model<br/>textModel.ts"]
  TM --> Scan["Scanner<br/>scanner.ts"]
  Scan -->|"sentence hashes, bands"| BG["Background<br/>background.ts"]
  BG -->|units to embed| Off["Offscreen embedder"]
  Off -->|vectors| BG
  BG <-->|"/lookup, /index, /hits"| API[(API)]
  BG -->|LookupResults| Scan
  Scan --> HL["Highlighter<br/>CSS Custom Highlight API"]
  HL --> UI["Page UI<br/>card, note form"]
  UI -->|"notes, ratings, reports"| BG
  Pop["Popup<br/>page narratives, account, settings"] <--> BG
```

The content script never modifies the page DOM: highlights are CSS Custom Highlight ranges and the card and the note form live in a Shadow DOM. Every network call goes through the background, which picks the mock backend, the remote API or the local index from the settings.

## Worker

```mermaid
flowchart LR
  Note[/POST /notes/] --> Fetch
  Hit[/Detection hit/] --> Fetch["fetch<br/>httpx, trafilatura"]
  Fetch --> Fp[/"pages, fingerprints, observations"/]
  Fp --> VM["match_variants<br/>LSH, MinHash Jaccard"]
  VM --> Var[/"variants, DERIVED_FROM"/]
  Fp --> NV["narrative_vectors"]
  Note --> NV
  NV --> Vec[/"claim and block vectors"/]
  Fp --> CL["cluster<br/>HDBSCAN"]
  CL --> Cand[/"candidate narratives, EVOLVED_INTO"/]
  Note --> MOD["moderate_note<br/>Claude"]
  MOD --> Mo[/"visible or held"/]
  Rat[/Ratings/] --> SC["score_notes<br/>matrix factorisation"]
  SC --> St[/"note status, reputation inputs"/]
```

## Component list

- Fingerprint library: normalisation, sentence splitting, sentence and content hashes, MinHash and LSH bands, embedding units and the int8 vector format, written once in TypeScript in `client/lib/`. The extension bundles it, and the fingerprint service runs it in Node.
- Extension: the scanner, highlighter and page UI in the content script, the background with the local index, the offscreen embedder and the popup.
- API: lookup, `/check` and the index; notes, ratings, reports and the moderation queue; sign-in by email and password, and `/me`; detection hits.
- Worker: `fetch`, `match_variants`, `narrative_vectors`, `cluster`, `score_notes` and `moderate_note`.
- Dashboard: Streamlit pages for narratives and their spread, a narrative's detail, notes, the moderation queue, reputation and checking a text. It reads Postgres directly and acts through the API.
- Offline benchmark: imports EUvsDisinfo cases into its own database and measures narrative recognition, described in [evaluation.md](evaluation.md).
- Database: plain SQL migrations in `backend/migrations/`, applied in filename order at API and worker startup.
