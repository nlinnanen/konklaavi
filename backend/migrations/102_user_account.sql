-- Optional accounts. An install (the extension's userId) votes on its own until a magic link links it
-- to an account; reputation then counts the votes of all the account's installs.
CREATE TABLE user_account (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email      text NOT NULL UNIQUE, -- lowercased
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE install (
  id         uuid PRIMARY KEY, -- the extension's userId
  account_id uuid REFERENCES user_account(id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON install (account_id);

-- Single-use magic-link tokens, stored as the SHA-256 of the token sent by email.
CREATE TABLE login_token (
  token_hash bytea PRIMARY KEY,
  install_id uuid NOT NULL REFERENCES install(id),
  email      text NOT NULL,
  expires_at timestamptz NOT NULL,
  used_at    timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON login_token (email, created_at);
CREATE INDEX ON login_token (install_id, created_at);
