# Components

This page breaks the system into components that can be built in parallel. The flows are in [architecture.md](architecture.md) and the tables in [data-model.md](data-model.md). Arrows show the data that moves between components.

## Context

```mermaid
flowchart LR
  Reader(["Reader<br/>extension user"])
  Owner(["Site owner / analyst"])
  Sys["Content Trust Layer"]
  Web["Public web pages"]
  EU["EUvsDisinfo database"]
  Claude["Claude API"]

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

  subgraph Sys["Content Trust Layer"]
    Ext["Extension<br/>TypeScript, WXT, transformers.js"]
    API["API<br/>Python, FastAPI"]
    Worker["Worker<br/>Python"]
    DB[("Postgres + pgvector")]
    Dash["Dashboard<br/>React, react-sigma"]
    Seed["Seed importer<br/>Python script"]
    Sim["Simulation<br/>Python script"]
  end

  Web["Public web pages"]
  EU["EUvsDisinfo database"]
  Claude["Claude API"]

  Reader -->|reports| Ext
  Ext -->|highlights| Reader
  Ext -->|"report: text, url, verdict"| API
  Ext -->|"detection hit: variant, url"| API
  API -->|signature index deltas| Ext
  API -->|reports, hits, jobs| DB
  DB -->|jobs| Worker
  Worker -->|"fingerprints, variants, narratives, signatures"| DB
  Web -->|page text| Worker
  Worker -->|report text| Claude
  Claude -->|claim, techniques, explanation| Worker
  EU -->|cases| Seed
  Seed -->|seed narratives, fetch jobs| DB
  DB -->|graph, spread metrics| API
  API -->|graph, spread metrics| Dash
  Dash --> Owner
  Sim -->|synthetic reports and hits| API

  classDef ext fill:#999,stroke:#666,stroke-dasharray:4,color:#fff
  class Web,EU,Claude ext
```

The job queue is a table in Postgres. Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, and the periodic jobs re-enqueue themselves.

## Extension components

```mermaid
flowchart LR
  DOM[/Page DOM/] --> Chunker
  Chunker --> Chunk[/Chunk + author handle/]
  Chunk --> FP["Fingerprinter"]
  FP --> Fp[/"sha + minhash + embedding"/]
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
  Fp --> VM["Variant matcher<br/>LSH, containment"]
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

- Fingerprint library: chunk normalisation, SHA-256, MinHash and LSH bands, and the embedding model, implemented in both TypeScript and Python. A shared JSON file of test vectors keeps the two implementations producing identical output.
- Extension: chunker, fingerprinter, index sync, local matcher, highlighter, report UI and telemetry sender.
- API: index deltas, reports, detection hits, and graph and metrics queries for the dashboard.
- Worker: fetch job, variant matcher, narrative clusterer, analysis job and signature builder.
- Seed importer: reads EUvsDisinfo cases into narratives with verdicts and explanations, and enqueues fetch jobs for the outlet URLs each case links to.
- Dashboard: narrative graph, lineage tree and spread over time.
- Simulation: synthetic honest, lazy, partisan and coordinated users sending reports and hits through the API, plus the evaluation of precision and false-positive rate.
- Database: schema migrations as plain SQL files.

## Contracts

Parallel work depends on four contracts, written together at the start:

- The SQL schema, including the jobs table.
- The fingerprint specification and its test vectors.
- The API request and response shapes, including the index format.
- The job types and their payloads.

## Workstreams

- Extension: everything in the extension, with the TypeScript fingerprinter.
- Content pipeline: the Python fingerprinter, fetch job, variant matcher and lineage.
- Backend core: schema, API, analysis job, signature builder and index build.
- Data and evaluation: seed importer, narrative clusterer, dashboard and simulation.

The EUvsDisinfo cases are split into a seed set, which populates the index, and an evaluation set, which measures how well the system finds narratives it was not seeded with. Pages from reliable outlets serve as negatives for the false-positive rate.
