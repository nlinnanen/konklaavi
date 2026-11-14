-- A narrative's vectors are its claim and its central blocks.
DELETE FROM narrative_vector WHERE kind = 'paraphrase';
DROP INDEX narrative_vector_paraphrase_key;
ALTER TABLE narrative_vector DROP CONSTRAINT narrative_vector_check;
ALTER TABLE narrative_vector DROP CONSTRAINT narrative_vector_kind_check;
ALTER TABLE narrative_vector DROP COLUMN text;
ALTER TABLE narrative_vector ADD CONSTRAINT narrative_vector_kind_check CHECK (kind IN ('block', 'claim'));
ALTER TABLE narrative_vector ADD CONSTRAINT narrative_vector_check CHECK ((kind = 'block') = (fingerprint_id IS NOT NULL));
