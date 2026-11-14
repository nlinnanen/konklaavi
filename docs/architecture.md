# Architecture

The goals are in [north-star.md](north-star.md), the data model in [data-model.md](data-model.md) and the technology choices in [decisions.md](decisions.md).

The system has two halves. The extension fingerprints and matches content locally against a downloaded signature index. The backend builds that index from user votes and reports, EUvsDisinfo cases, fact-checks, the Pravda network and the public pages it fetches, and keeps the narrative graph. The C4 diagrams and the component list are in [components.md](components.md).

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
  Ext->>Ext: sentence hashes + LSH bands per chunk
  Ext->>Ext: look up sentence hashes and bands in the index
  Ext->>Ext: embed unmatched chunks, compare with narrative vectors
  opt match
    Ext->>Page: highlight with verdict and explanation
    Ext->>Tel: narrative id, public url, timestamp
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

  User->>Ext: select text, choose verdict, add a comment
  Ext->>API: text, url, verdict, comment
  API->>DB: store report, enqueue fetch job
  A->>DB: claim report and fetch job
  A->>A: fetch url anonymously, confirm it is public
  A->>A: LLM: core claim, techniques, explanation
  A->>DB: search fetched pages for variants
  A->>DB: create or extend candidate narrative
```

## Signature lifecycle

```mermaid
stateDiagram-v2
  [*] --> Candidate: cluster job
  [*] --> UnderReview: analysed report
  [*] --> Active: EUvsDisinfo, fact-check or network import
  UnderReview --> Active: votes confirm, or settlement matches a case
  UnderReview --> Retired: settlement finds a reliable outlet
  UnderReview --> Disputed: trustworthy votes
  Active --> Disputed: trustworthy votes
  Disputed --> Active: negative votes confirm
  Disputed --> UnderReview: votes no longer dispute
  Disputed --> Retired: trustworthy majority
  Active --> Retired: trustworthy majority
  Retired --> [*]
```

Clients receive Active signatures as verdicts and UnderReview and Disputed signatures as soft warnings. A Candidate narrative is not shipped. Votes never move EUvsDisinfo or fact-checker narratives, and a narrative with `settled_at` set keeps its state.
