-- EUvsDisinfo cases held out of the seed set, for the evaluation.
CREATE TABLE eval_case (
  case_id      text PRIMARY KEY,
  title        text NOT NULL,
  claim        text NOT NULL,
  explanation  text NOT NULL,
  source_url   text NOT NULL,
  article_urls text[] NOT NULL,
  languages    text[] NOT NULL DEFAULT '{}',
  published_on date
);
