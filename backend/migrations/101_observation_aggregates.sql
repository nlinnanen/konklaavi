-- Spread over time. Both aggregates follow observation -> fingerprint -> variant -> narrative as it
-- stood when the bucket was last refreshed; a later change of a fingerprint's variant reaches only
-- buckets the refresh policy still covers.

CREATE MATERIALIZED VIEW narrative_hits_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 hour', o.seen_at) AS hour,
       va.narrative_id,
       o.source,
       count(*) AS hits
FROM observation o
JOIN fingerprint f ON f.id = o.fingerprint_id
JOIN variant va ON va.id = f.variant_id
WHERE va.narrative_id IS NOT NULL
GROUP BY 1, 2, 3
WITH NO DATA;

SELECT add_continuous_aggregate_policy('narrative_hits_hourly',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '30 minutes');

CREATE MATERIALIZED VIEW narrative_domain_daily
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 day', o.seen_at) AS day,
       va.narrative_id,
       p.domain_id,
       count(*) AS observations
FROM observation o
JOIN fingerprint f ON f.id = o.fingerprint_id
JOIN variant va ON va.id = f.variant_id
JOIN page p ON p.id = o.page_id
WHERE va.narrative_id IS NOT NULL
GROUP BY 1, 2, 3
WITH NO DATA;

SELECT add_continuous_aggregate_policy('narrative_domain_daily',
  start_offset => INTERVAL '30 days', end_offset => INTERVAL '1 day', schedule_interval => INTERVAL '1 hour');

-- A domain is new to a narrative on the first day the narrative is observed on it.
CREATE VIEW narrative_new_domains_daily AS
SELECT first_day AS day, narrative_id, count(*) AS new_domains
FROM (
  SELECT narrative_id, domain_id, min(day) AS first_day FROM narrative_domain_daily GROUP BY 1, 2
) firsts
GROUP BY 1, 2;

ALTER TABLE observation SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'fingerprint_id',
  timescaledb.compress_orderby = 'seen_at DESC'
);
SELECT add_compression_policy('observation', INTERVAL '30 days');
