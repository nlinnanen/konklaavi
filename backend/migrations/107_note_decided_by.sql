-- The scoring rule that set a note's helpful, not_helpful or controversial status; null while it needs
-- more ratings. See app/jobs/score.py.
ALTER TABLE note ADD COLUMN decided_by text CHECK (decided_by IN ('bridging', 'agreement'));

-- decidedBy ships with the note, so a change to it marks the narrative changed too.
DROP TRIGGER index_change_update ON note;
CREATE TRIGGER index_change_update AFTER UPDATE ON note
  FOR EACH ROW WHEN (
    (OLD.status, OLD.decided_by, OLD.moderation, OLD.body, OLD.sources, OLD.classification)
    IS DISTINCT FROM (NEW.status, NEW.decided_by, NEW.moderation, NEW.body, NEW.sources, NEW.classification)
  ) EXECUTE FUNCTION index_change_note();
