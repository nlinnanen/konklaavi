-- An install without an account writes notes; signing in gives them the account.
ALTER TABLE note ALTER COLUMN account_id DROP NOT NULL;
CREATE INDEX ON note (author_install);
