-- Admin panel sign-ins: the cookie holds a random token, the table its SHA-256.
CREATE TABLE admin_session (
  token_hash bytea PRIMARY KEY,
  account_id uuid NOT NULL REFERENCES user_account(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);
CREATE INDEX ON admin_session (account_id);

-- Admin sign-ins come from a browser, not an install, and are rate limited per email only.
ALTER TABLE auth_attempt ALTER COLUMN install_id DROP NOT NULL;
