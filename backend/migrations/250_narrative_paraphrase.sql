-- LLM paraphrases of a narrative's claim ('paraphrase'), shipped in GET /index like its other vectors.
ALTER TABLE narrative_vector ADD COLUMN text text;

ALTER TABLE narrative_vector DROP CONSTRAINT narrative_vector_kind_check;
ALTER TABLE narrative_vector ADD CONSTRAINT narrative_vector_kind_check
  CHECK (kind IN ('block', 'claim', 'paraphrase'));

ALTER TABLE narrative_vector DROP CONSTRAINT narrative_vector_check;
ALTER TABLE narrative_vector ADD CONSTRAINT narrative_vector_check
  CHECK ((kind = 'block') = (fingerprint_id IS NOT NULL) AND (kind = 'paraphrase') = (text IS NOT NULL));

ALTER TABLE narrative_vector DROP CONSTRAINT narrative_vector_narrative_id_fingerprint_id_key;
CREATE UNIQUE INDEX narrative_vector_block_key ON narrative_vector (narrative_id, fingerprint_id) WHERE kind = 'block';
CREATE UNIQUE INDEX narrative_vector_claim_key ON narrative_vector (narrative_id) WHERE kind = 'claim';
CREATE UNIQUE INDEX narrative_vector_paraphrase_key ON narrative_vector (narrative_id, text) WHERE kind = 'paraphrase';
