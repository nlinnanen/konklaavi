-- Context notes on narratives and their ratings. score_notes sets a note's status, intercept and factor;
-- moderate_note and moderators set its moderation.
CREATE TABLE note (
  id                bigserial PRIMARY KEY,
  narrative_id      bigint NOT NULL REFERENCES narrative(id),
  author_install    uuid NOT NULL REFERENCES install(id),
  account_id        uuid NOT NULL REFERENCES user_account(id),
  classification    text NOT NULL CHECK (classification IN ('misleading', 'not_misleading')),
  body              text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 1000),
  sources           text[] NOT NULL DEFAULT '{}',
  status            text NOT NULL DEFAULT 'needs_more_ratings'
                    CHECK (status IN ('needs_more_ratings', 'helpful', 'not_helpful', 'controversial')),
  intercept         double precision,
  factor            double precision,
  -- When the status last became helpful or not_helpful; null while undecided. Rater reputation
  -- rewards ratings cast before it.
  decided_at        timestamptz,
  moderation        text NOT NULL DEFAULT 'pending_review'
                    CHECK (moderation IN ('visible', 'pending_review', 'hidden')),
  moderation_reason text,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON note (narrative_id);
CREATE INDEX ON note (account_id);
CREATE INDEX ON note (moderation) WHERE moderation = 'pending_review';

-- account_id is the install's account when it rated; scoring counts the install's current account.
CREATE TABLE note_rating (
  note_id     bigint NOT NULL REFERENCES note(id),
  install     uuid NOT NULL REFERENCES install(id),
  account_id  uuid REFERENCES user_account(id),
  helpfulness text NOT NULL CHECK (helpfulness IN ('helpful', 'somewhat', 'not_helpful')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (note_id, install)
);
CREATE INDEX ON note_rating (install);

-- A narrative ships its visible notes with their status and rating counts.
CREATE FUNCTION index_change_note() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO index_change (narrative_id) VALUES (NEW.narrative_id) ON CONFLICT DO NOTHING;
  RETURN NULL;
END $$;

CREATE TRIGGER index_change AFTER INSERT ON note
  FOR EACH ROW EXECUTE FUNCTION index_change_note();
CREATE TRIGGER index_change_update AFTER UPDATE ON note
  FOR EACH ROW WHEN (
    (OLD.status, OLD.moderation, OLD.body, OLD.sources, OLD.classification)
    IS DISTINCT FROM (NEW.status, NEW.moderation, NEW.body, NEW.sources, NEW.classification)
  ) EXECUTE FUNCTION index_change_note();

CREATE FUNCTION index_change_note_rating() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO index_change (narrative_id) SELECT narrative_id FROM note WHERE id = NEW.note_id
  ON CONFLICT DO NOTHING;
  RETURN NULL;
END $$;

CREATE TRIGGER index_change AFTER INSERT OR UPDATE OF helpfulness ON note_rating
  FOR EACH ROW EXECUTE FUNCTION index_change_note_rating();

-- Nothing writes votes.
DROP TRIGGER index_change ON vote;
DROP FUNCTION index_change_vote();
