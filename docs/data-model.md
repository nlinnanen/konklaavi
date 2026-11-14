# Data model

Content is identified at three levels:

- Exact: the same text, matched by SHA-256 of the normalised text.
- Variant: an edited copy, matched by MinHash over word shingles with LSH.
- Narrative: the same claim in different words, matched by cosine between a block's embedding and the narrative's vectors.

Fingerprints enter the database when the backend fetches a public page. Reports, detection hits from clients and EUvsDisinfo cases each trigger a fetch of the page they point to, so the graph grows wherever known narratives appear. Detection hits also record that a known variant was seen at a public URL, which gives the reach and spread of a narrative.

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

  Domain -->|HAS| Page
  Page -->|"CONTAINS {first_seen, source}"| FP
  Account -->|"POSTED {t, page}"| FP
  FP -->|VARIANT_OF| Var
  Var -->|PART_OF| Nar
  FP -->|"DERIVED_FROM {edit_dist, dt}"| FP
  Nar -->|"EVOLVED_INTO {split / merge / continue}"| Nar
  User -->|"VOTED {verdict, comment}"| FP
```

Two edge types describe how narratives evolve:

- `DERIVED_FROM` links a fingerprint to the most similar fingerprint seen earlier. The edges form a family tree of mutations within a narrative, showing which accounts and domains introduced each change.
- `EVOLVED_INTO` links a narrative cluster in one time window to its matching clusters in the next, recording splits, merges and drift.

A signature is the shipped form of a narrative in state `active`, `under_review` or `disputed`: its verdict, techniques, explanation and the match data clients need.

## Storage schema

All of it lives in Postgres with pgvector. Graph edges that have no table of their own sit in a generic `edge` table. Ids are `bigserial` except the voter and account ids, which are uuids. The migrations in `backend/migrations/` are the source for columns.

```mermaid
erDiagram
  DOMAIN ||--o{ PAGE : has
  PAGE ||--o{ OBSERVATION : on
  ACCOUNT ||--o{ OBSERVATION : posted
  FINGERPRINT ||--o{ OBSERVATION : seen_as
  FINGERPRINT ||--o{ SENTENCE : split_into
  FINGERPRINT ||--o{ LSH_BAND : indexed_by
  VARIANT ||--o{ FINGERPRINT : groups
  NARRATIVE ||--o{ VARIANT : groups
  NARRATIVE ||--o{ NARRATIVE_VECTOR : matched_by
  NARRATIVE ||--o{ NARRATIVE_PAGE : fetched_from
  PAGE ||--o{ NARRATIVE_PAGE : fetched_for
  NARRATIVE ||--o{ VOTE : attached_to
  NARRATIVE ||--o{ INDEX_CHANGE : changed
  USER_ACCOUNT ||--o{ INSTALL : links
  INSTALL ||--o{ VOTE : casts

  OBSERVATION {
    timestamptz seen_at
    bigint fingerprint_id
    bigint page_id
    bigint account_id
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
    text explanation
    text verdict
    text_array techniques
    text source
    text source_url
    text state
    vector384 centroid
    bigint settled_by FK
    timestamptz settled_at
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
  VOTE {
    bigserial id PK
    uuid user_id
    text content_hash
    text_array sentence_hashes
    text verdict
    text comment
    text url
    bigint narrative_id FK
  }
  USER_ACCOUNT {
    uuid id PK
    text email
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

`vote`, `sentence`, `narrative_vector`, `narrative_page`, `index_change` and `install` serve the API and the index. `eval_case` holds the evaluation's held-out EUvsDisinfo cases and `cluster_run` one row per clustering run. `login_token` and `job` support sign-in and the queue.

`observation.source` is `fetch` or `detection`. Detection observations reference existing fingerprints only, and their account is the poster on the page, never the extension user.

## Example lineage

The `DERIVED_FROM` tree of one narrative, read in the direction of spread:

```mermaid
flowchart LR
  A["@anon_acct on X<br/>day 0"] --> B["fringe blog<br/>day 1, edited"]
  B --> C["regional news comments<br/>day 3"]
  B --> D["Telegram mirror<br/>day 3"]
  C --> E["mainstream op-ed paraphrase<br/>day 6"]
```
