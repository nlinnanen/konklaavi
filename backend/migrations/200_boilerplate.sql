-- Lowercase text without digits, English month and weekday names, or punctuation, so blocks that
-- differ only in dates and counts compare equal.
CREATE FUNCTION dateless(content text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN trim(regexp_replace(
  regexp_replace(
    lower(content),
    '\d+|\m(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|sept?(ember)?'
    '|oct(ober)?|nov(ember)?|dec(ember)?|(mon|tues|wednes|thurs|fri|satur|sun)day)\M',
    ' ', 'g'),
  '[[:punct:][:space:]]+', ' ', 'g'));

CREATE FUNCTION word_count(content text) RETURNS integer
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN coalesce(cardinality(regexp_split_to_array(nullif(trim(content), ''), '\s+')), 0);

ALTER TABLE fingerprint
  ADD COLUMN dateless_hash text GENERATED ALWAYS AS (md5(dateless(content))) STORED,
  -- Prose: at least 8 words besides digits and dates, and those are at least half of its words.
  ADD COLUMN prose boolean GENERATED ALWAYS AS (
    word_count(dateless(content)) >= 8 AND 2 * word_count(dateless(content)) >= word_count(content)
  ) STORED;
CREATE INDEX ON fingerprint (dateless_hash);
