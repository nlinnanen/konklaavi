# Components

This page breaks the system into components that can be built in parallel. The flows are in [architecture.md](architecture.md) and the tables in [data-model.md](data-model.md). Arrows show the data that moves between components.

## Context

```mermaid
flowchart LR
  Reader(["Reader<br/>extension user"])
  Owner(["Site owner / analyst"])
  Sys["HashTrust"]
  Web["Public web pages"]
  EU["EUvsDisinfo database"]
  Claude["Claude via Hostinger AI Router"]

  Reader -->|reports| Sys
  Sys -->|highlights with verdict and explanation| Reader
  Sys -->|narrative spread and lineage| Owner
  Web -->|page text| Sys
  EU -->|documented disinformation cases| Sys
  Sys -->|report text| Claude
  Claude -->|claim, techniques, explanation| Sys

  classDef ext fill:#999,stroke:#666,stroke-dasharray:4,color:#fff
  class Web,EU,Claude ext
```

## Containers

```mermaid
flowchart LR
  Reader(["Reader"])
  Owner(["Site owner / analyst"])

  subgraph Sys["HashTrust"]
    Ext["Extension<br/>TypeScript, WXT, transformers.js"]
    API["API<br/>Python, FastAPI"]
    Worker["Worker<br/>Python"]
    FPS["Fingerprint service<br/>Node, client/lib"]
    DB[("Postgres<br/>TimescaleDB, pgvector")]
    Dash["Dashboard<br/>Python, Streamlit"]
    Seed["Seed importer<br/>Python script"]
    Sim["Simulation<br/>Python script"]
  end

  Web["Public web pages"]
  EU["EUvsDisinfo database"]
  Claude["Claude via Hostinger AI Router"]

  Reader -->|reports| Ext
  Ext -->|highlights| Reader
  Ext -->|"vote: excerpt, url, verdict, comment"| API
  Ext -->|"detection hit: variant, url"| API
  API -->|signature index deltas| Ext
  API -->|reports, hits, jobs| DB
  DB -->|jobs| Worker
  Worker -->|"fingerprints, variants, narratives, signatures"| DB
  Web -->|page text| Worker
  Worker -->|page text| FPS
  FPS -->|"hashes, minhash, embeddings"| Worker
  Worker -->|report text| Claude
  Claude -->|claim, techniques, explanation| Worker
  EU -->|cases| Seed
  Seed -->|seed narratives, fetch jobs| DB
  DB -->|graph, spread metrics| API
  DB -->|graph, spread metrics| Dash
  Dash --> Owner
  Sim -->|synthetic votes| API

  classDef ext fill:#999,stroke:#666,stroke-dasharray:4,color:#fff
  class Web,EU,Claude ext
```

The job queue is a table in Postgres. Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, and the periodic jobs re-enqueue themselves.

## Extension components

```mermaid
flowchart LR
  DOM[/Page DOM/] --> Chunker
  Chunker --> Chunk[/Block-level chunk/]
  Chunk --> FP["Fingerprinter"]
  FP --> Fp[/"sentence hashes + LSH bands"/]
  Chunk --> Emb["Embedder<br/>offscreen document"]
  Emb --> Vec[/Embedding/]
  Vec --> Matcher
  Sync["Index sync"] --> Idx[/Signature index/]
  Fp --> Matcher["Local matcher"]
  Idx --> Matcher
  Matcher --> Match[/Match: signature, variant/]
  Match --> HL["Highlighter"]
  Match --> Tel["Telemetry sender"]
  RUI["Report UI"] --> Rep[/Report/]
```

## Worker components

```mermaid
flowchart LR
  Rep[/Report/] --> Fetch["Fetch job<br/>httpx, trafilatura"]
  Hit[/Detection hit/] --> Fetch
  SeedJ[/Seed fetch job/] --> Fetch
  Fetch --> Fp[/"Fingerprints + observations"/]
  Fp --> VM["Variant matcher<br/>LSH, MinHash Jaccard"]
  VM --> Var[/"Variants + DERIVED_FROM"/]
  Var --> NC["Narrative clusterer<br/>HDBSCAN"]
  NC --> Nar[/"Narratives + EVOLVED_INTO"/]
  Rep --> AJ["Analysis job<br/>Claude"]
  AJ --> Verd[/"Claim, techniques, explanation"/]
  Nar --> SB["Signature builder"]
  Verd --> SB
  SB --> Sig[/"Signatures + index version"/]
```

The fetch job fetches a URL anonymously, which also confirms it is public, then extracts the text, chunks it and fingerprints each chunk. Reports, detection hits and the seed importer all enqueue fetch jobs, so the graph grows wherever known narratives appear.

## Component list

- Fingerprint library: normalisation, sentence splitting, SHA-256, MinHash and LSH bands, and the embedding model, written once in TypeScript in `client/lib/`. The extension bundles it, and the fingerprint service runs it in Node for the backend.
- Extension: chunker, fingerprinter, index sync, local matcher, highlighter, report UI and telemetry sender.
- API: index deltas, reports and detection hits.
- Worker: fetch job, variant matcher, narrative clusterer, analysis job and signature builder.
- Seed importer: reads EUvsDisinfo cases into narratives with verdicts and explanations, and enqueues fetch jobs for the outlet URLs each case links to.
- Case list importer: collects the titles of recent EUvsDisinfo cases from the euvsdisinfo.eu case list and imports each as an active narrative whose claim is its title, with no articles to fetch.
- Dashboard: a Streamlit app in `dashboard/app.py` that reads Postgres directly and shows the narrative graph, lineage tree and spread over time.
- Simulation: synthetic honest, lazy, partisan and coordinated users sending votes through the API and reading flags and reputations back.
- Database: schema migrations as plain SQL files.

## Contracts

Parallel work depends on four contracts, written together at the start:

- The SQL schema, including the jobs table.
- The fingerprint service's request and response shape.
- The API request and response shapes, including the index format.
- The job types and their payloads.

## Workstreams

- Extension: everything in the extension, with the TypeScript fingerprinter.
- Content pipeline: the Python fingerprinter, fetch job, variant matcher and lineage.
- Backend core: schema, API, analysis job, signature builder and index build.
- Data and evaluation: seed importer, narrative clusterer, dashboard and simulation.

The EUvsDisinfo cases are split into a seed set, which populates the index, and an evaluation set, which measures how well the system finds narratives it was not seeded with. Pages from reliable outlets serve as negatives for the false-positive rate.
