-- Representative unit vectors of a narrative for narrative-level matching, shipped in GET /index:
-- its most central attached blocks ('block') and the embedding of its title and claim ('claim').
CREATE TABLE narrative_vector (
  narrative_id   bigint NOT NULL REFERENCES narrative(id),
  kind           text NOT NULL CHECK (kind IN ('block', 'claim')),
  fingerprint_id bigint REFERENCES fingerprint(id),
  embedding      vector(384) NOT NULL,
  CHECK ((kind = 'claim') = (fingerprint_id IS NULL)),
  UNIQUE NULLS NOT DISTINCT (narrative_id, fingerprint_id)
);

CREATE FUNCTION index_change_narrative_vector() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO index_change (narrative_id)
  VALUES (CASE WHEN TG_OP = 'DELETE' THEN OLD.narrative_id ELSE NEW.narrative_id END)
  ON CONFLICT DO NOTHING;
  RETURN NULL;
END $$;

CREATE TRIGGER index_change AFTER INSERT OR UPDATE OR DELETE ON narrative_vector
  FOR EACH ROW EXECUTE FUNCTION index_change_narrative_vector();

-- Vectors for the narratives that exist already.
INSERT INTO job (kind) VALUES ('narrative_vectors');
