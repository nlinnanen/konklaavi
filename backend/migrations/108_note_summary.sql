-- One sentence of at most 140 characters saying what the note tells readers, written by moderate_note
-- or summarize_notes; null until one of them runs.
ALTER TABLE note ADD COLUMN summary text CHECK (char_length(summary) <= 140);
CREATE INDEX ON note (id) WHERE summary IS NULL AND moderation = 'visible';

-- The summary ships with the note, so a change to it marks the narrative changed too.
DROP TRIGGER index_change_update ON note;
CREATE TRIGGER index_change_update AFTER UPDATE ON note
  FOR EACH ROW WHEN (
    (OLD.status, OLD.decided_by, OLD.moderation, OLD.body, OLD.sources, OLD.classification, OLD.summary)
    IS DISTINCT FROM
    (NEW.status, NEW.decided_by, NEW.moderation, NEW.body, NEW.sources, NEW.classification, NEW.summary)
  ) EXECUTE FUNCTION index_change_note();
