-- The match level of the highlight a feedback vote was cast on: exact | variant | narrative | related.
ALTER TABLE vote ADD COLUMN match_level text;

-- A report narrative's signature ships its reporters, so a vote attached to one changes the index.
CREATE FUNCTION index_change_vote() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO index_change (narrative_id)
  SELECT id FROM narrative WHERE id = NEW.narrative_id AND source = 'report'
  ON CONFLICT DO NOTHING;
  RETURN NULL;
END $$;

CREATE TRIGGER index_change AFTER INSERT OR UPDATE OF narrative_id, verdict ON vote
  FOR EACH ROW WHEN (NEW.narrative_id IS NOT NULL)
  EXECUTE FUNCTION index_change_vote();
