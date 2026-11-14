# Data model

Content is recognised at three levels:

- Exact: a shared sentence hash, SHA-256 over the normalised sentence.
- Variant: an edited copy, found by MinHash over word 5-grams with LSH bands and confirmed at an estimated Jaccard of 0.5.
- Narrative: the same claim in other words, found by cosine between an embedding unit and the narrative's vectors, at the `narrative` level from `NARRATIVE_MATCH_THRESHOLD` and the weaker `related` level from `RELATED_MATCH_THRESHOLD`.

Fingerprints enter the database when a note's passage is stored and when the backend fetches a public page. Notes and detection hits each enqueue a fetch of the page they point to, and the fetch attaches the blocks that carry the narrative, so the graph grows wherever the narrative is seen. Observations record each sighting with its time and page and feed the narrative's spread.

## Graph

```mermaid
flowchart LR
  Domain((Domain))
  Page((Page))
  FP((Fingerprint))
  Var((Variant))
  Nar((Narrative))
  Note((Note))
  Acct((Account))

  Domain -->|HAS| Page
  Page -->|"OBSERVED {seen_at, fetch / detection}"| FP
  FP -->|VARIANT_OF| Var
  Var -->|PART_OF| Nar
  FP -->|"DERIVED_FROM {jaccard, dt_seconds}"| FP
  Nar -->|"EVOLVED_INTO"| Nar
  Note -->|ON| Nar
  Acct -->|WROTE| Note
  Acct -->|"RATED {helpfulness}"| Note
```

`DERIVED_FROM` links a fingerprint to the closest earlier-seen near-duplicate, so the edges form a family tree of edits within a narrative. `EVOLVED_INTO` links a cluster candidate to the earlier narrative it grew out of.

A narrative has a `source`. A `note` narrative is created by `POST /notes`, its claim the passage, and ships to clients while it has a note that is not hidden. A `cluster` narrative is a candidate the `cluster` job found among unassigned fingerprints; it never ships.

## Storage schema

Everything lives in Postgres with TimescaleDB and pgvector. Graph edges sit in a generic `edge` table. Ids are `bigserial` except accounts and installs, which are uuids. The migrations in `backend/migrations/` are the source for columns, and the diagram shows the ones the code reads.

```mermaid
erDiagram
  DOMAIN ||--o{ PAGE : has
  PAGE ||--o{ OBSERVATION : on
  FINGERPRINT ||--o{ OBSERVATION : seen_as
  FINGERPRINT ||--o{ SENTENCE : split_into
  FINGERPRINT ||--o{ LSH_BAND : indexed_by
  VARIANT ||--o{ FINGERPRINT : groups
  NARRATIVE ||--o{ VARIANT : groups
  NARRATIVE ||--o{ NARRATIVE_VECTOR : matched_by
  NARRATIVE ||--o{ NARRATIVE_PAGE : fetched_from
  PAGE ||--o{ NARRATIVE_PAGE : fetched_for
  NARRATIVE ||--o{ NOTE : has
  NARRATIVE ||--o{ INDEX_CHANGE : changed
  NOTE ||--o{ NOTE_RATING : rated_by
  USER_ACCOUNT ||--o{ INSTALL : links
  USER_ACCOUNT ||--o{ NOTE : writes
  INSTALL ||--o{ NOTE_RATING : casts

  OBSERVATION {
    timestamptz seen_at
    bigint fingerprint_id
    bigint page_id
    text source
  }
  FINGERPRINT {
    bigserial id PK
    text content_hash
    text content
    int_array minhash
    vector384 embedding
    bigint variant_id FK
    timestamptz first_seen
    boolean prose
  }
  SENTENCE {
    text hash
    bigint fingerprint_id FK
  }
  LSH_BAND {
    smallint band
    text hash
    bigint fingerprint_id FK
  }
  VARIANT {
    bigserial id PK
    bigint narrative_id FK
  }
  NARRATIVE {
    bigserial id PK
    text title
    text claim
    text source
    text source_url
    vector384 centroid
    timestamptz created_at
  }
  NARRATIVE_VECTOR {
    bigint narrative_id FK
    text kind
    bigint fingerprint_id FK
    vector384 embedding
  }
  NARRATIVE_PAGE {
    bigint narrative_id FK
    bigint page_id FK
  }
  NOTE {
    bigserial id PK
    bigint narrative_id FK
    uuid author_install FK
    uuid account_id FK
    text classification
    text body
    text_array sources
    text status
    float intercept
    float factor
    timestamptz decided_at
    text moderation
    text moderation_reason
  }
  NOTE_RATING {
    bigint note_id FK
    uuid install FK
    uuid account_id FK
    text helpfulness
  }
  USER_ACCOUNT {
    uuid id PK
    text email
    text password_hash
  }
  INSTALL {
    uuid id PK
    uuid account_id FK
  }
  INDEX_CHANGE {
    xid8 xid
    bigint narrative_id
  }
  EDGE {
    text src_kind
    bigint src
    text dst_kind
    bigint dst
    text type
    jsonb props
  }
```

`observation` is a TimescaleDB hypertable on `seen_at`, and the continuous aggregates `narrative_hits_hourly` and `narrative_domain_daily` feed the dashboard's spread charts. Triggers on `note`, `note_rating` and `narrative_vector` write `index_change` rows, which `/index` turns into deltas. `job` is the work queue, `cluster_run` holds one row per clustering run, and `eval_case` holds the offline benchmark's cases in the benchmark's own database. `observation.source` is `fetch` or `detection`; a detection observation references an existing fingerprint and never identifies the extension user.
