-- One row per run of the cluster job.
CREATE TABLE cluster_run (
  id         bigserial PRIMARY KEY,
  ran_at     timestamptz NOT NULL DEFAULT now(),
  unassigned int NOT NULL, -- fingerprints with an embedding and no variant
  embeddings int NOT NULL, -- of those, the newest ones clustered, at most the job's cap
  clusters   int NOT NULL,
  extended   int NOT NULL, -- clusters that joined an existing narrative
  created    int NOT NULL, -- clusters that became a new narrative
  evolved    int NOT NULL  -- new narratives with an EVOLVED_INTO edge from an earlier one
);
