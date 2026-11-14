# Data model

Content is identified at three levels:

- Exact: the same text, matched by SHA-256 of the normalised text.
- Variant: an edited copy, matched by MinHash over word shingles with LSH.
- Narrative: the same claim in different words, matched by clustering embeddings.

Fingerprints enter the database from two sources: the crawler, which fetches public pages, and user reports. Detection hits from clients add observations of known variants at public URLs, which give the reach and spread of a narrative.

## Graph

```mermaid
flowchart LR
  User((User))
  Account((Account))
  Domain((Domain))
  Page((Page))
  FP((Fingerprint))
  Var((Variant))
  Nar((Narrative))
  Sig((Signature))

  Domain -->|HAS| Page
  Page -->|"CONTAINS {first_seen, source}"| FP
  Account -->|"POSTED {t, page}"| FP
  FP -->|VARIANT_OF| Var
  Var -->|PART_OF| Nar
  FP -->|"DERIVED_FROM {edit_dist, dt}"| FP
  Nar -->|"EVOLVED_INTO {split / merge / continue}"| Nar
  User -->|"REPORTED {verdict, technique}"| FP
  Sig -->|DESCRIBES| Nar
```

Two edge types describe how narratives evolve:

- `DERIVED_FROM` links a fingerprint to the most similar fingerprint seen earlier. The edges form a family tree of mutations within a narrative, showing which accounts and domains introduced each change.
- `EVOLVED_INTO` links a narrative cluster in one time window to its matching clusters in the next, recording splits, merges and drift.

A signature is the shipped form of a narrative: its verdict, techniques, explanation and the match data clients need.

## Storage schema

All of it lives in Postgres with pgvector. Graph edges that have no table of their own sit in a generic `edge` table.

```mermaid
erDiagram
  DOMAIN ||--o{ PAGE : has
  PAGE ||--o{ OBSERVATION : on
  ACCOUNT ||--o{ OBSERVATION : posted
  FINGERPRINT ||--o{ OBSERVATION : seen_as
  FINGERPRINT ||--o{ LSH_BAND : indexed_by
  VARIANT ||--o{ FINGERPRINT : groups
  NARRATIVE ||--o{ VARIANT : groups
  NARRATIVE ||--o| SIGNATURE : shipped_as
  SIGNATURE ||--o{ ALLOWLIST : suppressed_by
  USER ||--o{ REPORT : files
  FINGERPRINT ||--o{ REPORT : about

  OBSERVATION {
    uuid id PK
    uuid fingerprint_id FK
    uuid page_id FK
    uuid account_id FK
    text source
    timestamptz seen_at
  }
  FINGERPRINT {
    uuid id PK
    bytea sha256
    int_array minhash
    vector384 embedding
    text content
    uuid variant_id FK
    timestamptz first_seen
  }
  LSH_BAND {
    int band
    bigint hash
    uuid fingerprint_id FK
  }
  VARIANT {
    uuid id PK
    uuid narrative_id FK
  }
  NARRATIVE {
    uuid id PK
    int window
    text claim
    vector384 centroid
  }
  SIGNATURE {
    uuid id PK
    uuid narrative_id FK
    text state
    text verdict
    text_array techniques
    text explanation
    int index_version
  }
  ALLOWLIST {
    uuid signature_id FK
    text domain
  }
  REPORT {
    uuid id PK
    uuid user_id FK
    uuid fingerprint_id FK
    text verdict
    text technique
    timestamptz created_at
  }
  EDGE {
    uuid src
    uuid dst
    text type
    jsonb props
  }
```

`OBSERVATION.source` is `crawl`, `report` or `detection`. Detection observations reference existing fingerprints only, and their account is the poster on the page, never the extension user.

## Example lineage

The `DERIVED_FROM` tree of one narrative, read in the direction of spread:

```mermaid
flowchart LR
  A["@anon_acct on X<br/>day 0"] --> B["fringe blog<br/>day 1, edited"]
  B --> C["regional news comments<br/>day 3"]
  B --> D["Telegram mirror<br/>day 3"]
  C --> E["mainstream op-ed paraphrase<br/>day 6"]
```
