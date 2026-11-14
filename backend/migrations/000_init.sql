-- Base schema. Workstreams add their own files: 1xx core, 2xx pipeline, 3xx data.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE domain (
  id   bigserial PRIMARY KEY,
  name text NOT NULL UNIQUE
);

CREATE TABLE page (
  id         bigserial PRIMARY KEY,
  url        text NOT NULL UNIQUE,
  domain_id  bigint NOT NULL REFERENCES domain(id),
  title      text,
  published_at timestamptz,
  fetched_at timestamptz,
  status     text NOT NULL DEFAULT 'pending' -- pending | ok | failed | private
);

CREATE TABLE account (
  id       bigserial PRIMARY KEY,
  platform text NOT NULL,
  handle   text NOT NULL,
  UNIQUE (platform, handle)
);

-- A narrative and its shipped signature: one row.
CREATE TABLE narrative (
  id           bigserial PRIMARY KEY,
  title        text NOT NULL,
  claim        text,
  explanation  text,
  verdict      text NOT NULL DEFAULT 'misinformation', -- a negative Verdict from client/lib/types.ts
  techniques   text[] NOT NULL DEFAULT '{}',
  source       text NOT NULL,                          -- euvsdisinfo | report | cluster
  source_url   text,
  state        text NOT NULL DEFAULT 'candidate',      -- candidate | under_review | active | disputed | retired | rejected
  centroid     vector(384),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE variant (
  id           bigserial PRIMARY KEY,
  narrative_id bigint REFERENCES narrative(id)
);

-- One content block (paragraph, post, comment).
CREATE TABLE fingerprint (
  id            bigserial PRIMARY KEY,
  content_hash  text NOT NULL UNIQUE, -- fingerprintSentences().contentHash
  content       text NOT NULL,
  minhash       integer[],
  embedding     vector(384),
  variant_id    bigint REFERENCES variant(id),
  first_seen    timestamptz NOT NULL DEFAULT now(),
  first_page_id bigint REFERENCES page(id)
);
CREATE INDEX ON fingerprint (variant_id);

-- Exact level: the sentence hashes the extension sends to /lookup.
CREATE TABLE sentence (
  hash           text NOT NULL,
  fingerprint_id bigint NOT NULL REFERENCES fingerprint(id),
  PRIMARY KEY (hash, fingerprint_id)
);

-- Variant level: MinHash LSH bands.
CREATE TABLE lsh_band (
  band           smallint NOT NULL,
  hash           text NOT NULL,
  fingerprint_id bigint NOT NULL REFERENCES fingerprint(id),
  PRIMARY KEY (band, hash, fingerprint_id)
);

CREATE TABLE observation (
  seen_at        timestamptz NOT NULL,
  fingerprint_id bigint NOT NULL,
  page_id        bigint NOT NULL,
  account_id     bigint,
  source         text NOT NULL -- fetch | detection
);
SELECT create_hypertable('observation', 'seen_at');
CREATE INDEX ON observation (fingerprint_id, seen_at DESC);

-- Body of POST /votes, as sent by the extension.
CREATE TABLE vote (
  id              bigserial PRIMARY KEY,
  user_id         uuid NOT NULL,
  content_hash    text NOT NULL,
  sentence_hashes text[] NOT NULL,
  verdict         text NOT NULL,
  comment         text,
  url             text NOT NULL,
  domain          text NOT NULL,
  excerpt         text,
  created_at      timestamptz NOT NULL,
  received_at     timestamptz NOT NULL DEFAULT now(),
  narrative_id    bigint REFERENCES narrative(id),
  UNIQUE (user_id, content_hash)
);
CREATE INDEX ON vote USING gin (sentence_hashes);
CREATE INDEX ON vote (domain);

CREATE TABLE edge (
  src_kind   text NOT NULL, -- fingerprint | narrative
  src        bigint NOT NULL,
  dst_kind   text NOT NULL,
  dst        bigint NOT NULL,
  type       text NOT NULL, -- DERIVED_FROM | EVOLVED_INTO
  props      jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (src_kind, src, dst_kind, dst, type)
);

CREATE TABLE job (
  id         bigserial PRIMARY KEY,
  kind       text NOT NULL,
  payload    jsonb NOT NULL DEFAULT '{}',
  state      text NOT NULL DEFAULT 'queued', -- queued | running | done | failed
  attempts   int NOT NULL DEFAULT 0,
  run_after  timestamptz NOT NULL DEFAULT now(),
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON job (state, run_after);
