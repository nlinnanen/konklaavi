-- The pages fetched for a narrative, where its blocks are picked by relevance.
CREATE TABLE narrative_page (
  narrative_id bigint NOT NULL REFERENCES narrative(id),
  page_id      bigint NOT NULL REFERENCES page(id),
  PRIMARY KEY (narrative_id, page_id)
);
INSERT INTO narrative_page (narrative_id, page_id)
SELECT DISTINCT n.id, p.id
FROM job j
JOIN page p ON p.url = j.payload->>'url'
JOIN narrative n ON n.id = (j.payload->>'narrative_id')::bigint
WHERE j.kind = 'fetch' AND j.payload->>'narrative_id' IS NOT NULL;
