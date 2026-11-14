-- Accounts sign in with email and password, hashed with argon2. Accounts made before passwords have none
-- and sign in again with "Create account".
ALTER TABLE user_account ADD COLUMN password_hash text;

DROP TABLE login_token;

-- Sign-in and registration attempts, for rate limiting per email and per install.
CREATE TABLE auth_attempt (
  email      text NOT NULL, -- lowercased
  install_id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON auth_attempt (email, created_at);
CREATE INDEX ON auth_attempt (install_id, created_at);
