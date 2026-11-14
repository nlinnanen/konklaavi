# Architecture

The goals are in [north-star.md](north-star.md), the data model in [data-model.md](data-model.md) and the technology choices in [decisions.md](decisions.md).

The system has two halves. The extension fingerprints and matches content locally against a downloaded signature index. The backend builds that index from crawled public pages and user reports, and keeps the narrative graph.

## Components

```mermaid
flowchart LR
  subgraph Extension
    Chunk["Chunker<br/>posts, comments, paragraphs"]
    FP["Fingerprinter<br/>sha + minhash + embedding"]
    Match["Local matcher"]
    Heur["Heuristic classifier"]
    Idx[("Signature index<br/>local copy")]
    UI["Highlight + report UI"]
    Chunk --> FP --> Match
    Idx --> Match
    Chunk --> Heur
    Match --> UI
    Heur --> UI
  end

  subgraph Backend
    IndexAPI["Index API<br/>signed deltas"]
    ReportAPI["Report API"]
    TelAPI["Telemetry API"]
    Crawler["Crawler"]
    Analysis["Analysis worker<br/>LLM triage"]
    Graph["Graph worker<br/>clustering, lineage"]
    DB[("Postgres + pgvector")]
    Dash["Dashboard + graph UI"]
  end

  Sim["Simulation"]

  IndexAPI --> Idx
  UI -->|report: text + url| ReportAPI
  Match -->|hit: variant + public url| TelAPI
  Sim --> ReportAPI
  Sim --> TelAPI
  ReportAPI --> DB
  TelAPI --> DB
  Crawler --> DB
  Analysis <--> DB
  Graph <--> DB
  DB --> IndexAPI
  DB --> Dash
```

## Scanning a page

Everything up to the telemetry call runs in the browser.

```mermaid
sequenceDiagram
  participant Page
  participant Ext as Extension
  participant Tel as Telemetry API

  Ext->>Ext: fetch index delta on a timer
  Page->>Ext: DOM rendered or mutated
  Ext->>Ext: chunk new content, normalise text
  Ext->>Ext: sha + minhash + embedding per chunk
  Ext->>Ext: look up LSH bands in Bloom filter
  Ext->>Ext: nearest narrative centroid
  alt match
    Ext->>Page: highlight with verdict and explanation
    Ext->>Tel: variant_id, public url, timestamp
  else no match
    Ext->>Ext: heuristic classifier
    opt manipulative style
      Ext->>Page: soft highlight, no verdict
    end
  end
```

## Reporting

A report is the only path through which text a user reads reaches the backend.

```mermaid
sequenceDiagram
  actor User
  participant Ext as Extension
  participant API as Report API
  participant A as Analysis worker
  participant DB as Postgres

  User->>Ext: select text, choose verdict
  Ext->>API: text, url, verdict, technique
  API->>API: fetch url anonymously, confirm it is public
  API->>DB: store report
  A->>DB: load new reports
  A->>A: LLM: core claim, techniques, explanation
  A->>DB: search crawled corpus for variants
  A->>DB: create or extend candidate narrative
```

## Signature lifecycle

```mermaid
stateDiagram-v2
  [*] --> Candidate: first report
  Candidate --> UnderReview: LLM verdict or more reports
  UnderReview --> Active: enough independent evidence
  UnderReview --> Rejected: evidence disagrees
  Active --> Disputed: users dispute highlights
  Disputed --> Active: dispute rejected
  Disputed --> Retired: dispute upheld
  Retired --> [*]
  Rejected --> [*]
```

Clients receive Active signatures as verdicts and UnderReview signatures as soft warnings. Allowlist entries attach to a signature and suppress it on specific domains, such as fact-checkers quoting the claim.
