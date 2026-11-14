-- How a report narrative was settled: the EUvsDisinfo or fact-check narrative that confirmed it, and when
-- it was confirmed or rejected.
ALTER TABLE narrative
  ADD COLUMN settled_by bigint REFERENCES narrative(id),
  ADD COLUMN settled_at timestamptz;
