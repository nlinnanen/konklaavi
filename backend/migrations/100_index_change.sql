-- Narratives whose shipped signature, sentences or bands changed, per writing transaction.
-- GET /index sends the narratives changed by transactions at or after the client's version, which is
-- the oldest transaction still running when the client last read the index; see app/api/core.py.
CREATE TABLE index_change (
  xid          xid8 NOT NULL DEFAULT pg_current_xact_id(),
  narrative_id bigint NOT NULL,
  PRIMARY KEY (xid, narrative_id)
);

CREATE FUNCTION index_change_narrative() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO index_change (narrative_id) VALUES (NEW.id) ON CONFLICT DO NOTHING;
  RETURN NULL;
END $$;

CREATE TRIGGER index_change AFTER INSERT ON narrative
  FOR EACH ROW EXECUTE FUNCTION index_change_narrative();
-- Only the columns a signature ships; the cluster job rewrites every centroid each run.
CREATE TRIGGER index_change_update AFTER UPDATE ON narrative
  FOR EACH ROW WHEN (
    (OLD.title, OLD.verdict, OLD.techniques, OLD.explanation, OLD.source, OLD.source_url, OLD.state)
    IS DISTINCT FROM
    (NEW.title, NEW.verdict, NEW.techniques, NEW.explanation, NEW.source, NEW.source_url, NEW.state)
  ) EXECUTE FUNCTION index_change_narrative();

CREATE FUNCTION index_change_variant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO index_change (narrative_id)
  SELECT id FROM unnest(ARRAY[OLD.narrative_id, NEW.narrative_id]) id WHERE id IS NOT NULL
  ON CONFLICT DO NOTHING;
  RETURN NULL;
END $$;

CREATE TRIGGER index_change AFTER UPDATE OF narrative_id ON variant
  FOR EACH ROW WHEN (OLD.narrative_id IS DISTINCT FROM NEW.narrative_id)
  EXECUTE FUNCTION index_change_variant();

CREATE FUNCTION index_change_fingerprint() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO index_change (narrative_id)
  SELECT DISTINCT va.narrative_id FROM variant va
  WHERE va.narrative_id IS NOT NULL
    AND va.id IN (SELECT variant_id FROM new_rows UNION SELECT variant_id FROM old_rows)
  ON CONFLICT DO NOTHING;
  RETURN NULL;
END $$;

CREATE FUNCTION index_change_fingerprint_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO index_change (narrative_id)
  SELECT DISTINCT va.narrative_id FROM variant va
  WHERE va.narrative_id IS NOT NULL AND va.id IN (SELECT variant_id FROM new_rows)
  ON CONFLICT DO NOTHING;
  RETURN NULL;
END $$;

CREATE TRIGGER index_change_insert AFTER INSERT ON fingerprint
  REFERENCING NEW TABLE AS new_rows
  FOR EACH STATEMENT EXECUTE FUNCTION index_change_fingerprint_insert();
CREATE TRIGGER index_change_update AFTER UPDATE ON fingerprint
  REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
  FOR EACH STATEMENT EXECUTE FUNCTION index_change_fingerprint();

-- Sentences and bands added to a fingerprint that already belongs to a narrative.
CREATE FUNCTION index_change_fingerprint_rows() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO index_change (narrative_id)
  SELECT DISTINCT va.narrative_id FROM fingerprint f JOIN variant va ON va.id = f.variant_id
  WHERE va.narrative_id IS NOT NULL AND f.id IN (SELECT fingerprint_id FROM new_rows)
  ON CONFLICT DO NOTHING;
  RETURN NULL;
END $$;

CREATE TRIGGER index_change AFTER INSERT ON sentence
  REFERENCING NEW TABLE AS new_rows
  FOR EACH STATEMENT EXECUTE FUNCTION index_change_fingerprint_rows();
CREATE TRIGGER index_change AFTER INSERT ON lsh_band
  REFERENCING NEW TABLE AS new_rows
  FOR EACH STATEMENT EXECUTE FUNCTION index_change_fingerprint_rows();

-- POST /hits looks up the hit fetches enqueued for a URL in the last day.
CREATE INDEX job_fetch_url ON job ((payload->>'url'), created_at) WHERE kind = 'fetch';
