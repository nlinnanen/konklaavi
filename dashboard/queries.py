"""SQL the dashboard runs. Kept free of Streamlit so the backend tests can run it against the schema."""

NARRATIVES = """
SELECT n.id,
       n.title,
       n.source,
       n.state,
       count(DISTINCT v.id) AS variants,
       count(DISTINCT o.page_id) AS pages,
       count(DISTINCT p.domain_id) AS domains,
       min(f.first_seen) AS first_seen
FROM narrative n
LEFT JOIN variant v ON v.narrative_id = n.id
LEFT JOIN fingerprint f ON f.variant_id = v.id
LEFT JOIN observation o ON o.fingerprint_id = f.id
LEFT JOIN page p ON p.id = o.page_id
GROUP BY n.id
ORDER BY pages DESC, first_seen DESC NULLS LAST, n.id DESC
"""

NARRATIVE = """
SELECT id, title, claim, explanation, verdict, techniques, source, source_url, state, created_at
FROM narrative WHERE id = %(id)s
"""

# Every page carrying one of the narrative's fingerprints.
NARRATIVE_PAGES = """
SELECT p.url,
       d.name AS domain,
       p.title,
       count(*) AS observations,
       min(o.seen_at) AS first_seen,
       max(o.seen_at) AS last_seen
FROM variant v
JOIN fingerprint f ON f.variant_id = v.id
JOIN observation o ON o.fingerprint_id = f.id
JOIN page p ON p.id = o.page_id
JOIN domain d ON d.id = p.domain_id
WHERE v.narrative_id = %(id)s
GROUP BY p.id, d.name
ORDER BY first_seen, p.url
"""

NARRATIVE_OBSERVATIONS_PER_DAY = """
SELECT time_bucket('1 day', o.seen_at) AS day, count(*) AS observations
FROM variant v
JOIN fingerprint f ON f.variant_id = v.id
JOIN observation o ON o.fingerprint_id = f.id
WHERE v.narrative_id = %(id)s
GROUP BY day
ORDER BY day
"""

# DERIVED_FROM edges touching the narrative's fingerprints, parent first, with a label for each end.
NARRATIVE_LINEAGE = """
WITH members AS (
  SELECT f.id FROM fingerprint f JOIN variant v ON v.id = f.variant_id WHERE v.narrative_id = %(id)s
),
edges AS (
  SELECT e.dst AS parent, e.src AS child
  FROM edge e
  WHERE e.type = 'DERIVED_FROM' AND e.src_kind = 'fingerprint' AND e.dst_kind = 'fingerprint'
    AND (e.src IN (SELECT id FROM members) OR e.dst IN (SELECT id FROM members))
  ORDER BY e.created_at
  LIMIT %(limit)s
),
labels AS (
  SELECT f.id, coalesce(d.name, '?') || ': ' || left(f.content, 60) AS label
  FROM fingerprint f
  LEFT JOIN page p ON p.id = f.first_page_id
  LEFT JOIN domain d ON d.id = p.domain_id
  WHERE f.id IN (SELECT parent FROM edges UNION SELECT child FROM edges)
)
SELECT e.parent, pl.label AS parent_label, e.child, cl.label AS child_label
FROM edges e JOIN labels pl ON pl.id = e.parent JOIN labels cl ON cl.id = e.child
ORDER BY e.parent, e.child
"""

# Spread reads core's continuous aggregates (migration 101), which file each observation under the
# narrative its fingerprint had when the bucket was materialised.
SPREAD_PER_DAY = """
WITH daily AS (
  SELECT day, sum(observations)::bigint AS observations, count(*) AS domains
  FROM narrative_domain_daily WHERE narrative_id = %(id)s GROUP BY day
)
SELECT d.day,
       d.observations,
       d.domains,
       sum(coalesce(n.new_domains, 0)) OVER (ORDER BY d.day)::bigint AS domains_so_far
FROM daily d
LEFT JOIN narrative_new_domains_daily n ON n.day = d.day AND n.narrative_id = %(id)s
ORDER BY d.day
"""

# Observations per day by how they were made: fetched pages, or detections reported by extensions.
SPREAD_HITS_PER_DAY = """
SELECT time_bucket('1 day', hour) AS day, source, sum(hits)::bigint AS hits
FROM narrative_hits_hourly WHERE narrative_id = %(id)s
GROUP BY 1, 2
ORDER BY 1, 2
"""

SPREAD_DOMAINS = """
SELECT dm.name AS domain,
       min(a.day) AS first_day,
       max(a.day) AS last_day,
       sum(a.observations)::bigint AS observations
FROM narrative_domain_daily a JOIN domain dm ON dm.id = a.domain_id
WHERE a.narrative_id = %(id)s
GROUP BY dm.name
ORDER BY first_day, domain
"""

# The most active reporters, one row per account, or per install without one, with one of their install
# ids for GET /me, which computes reputation.
REPORTERS = """
SELECT min(v.user_id::text) AS user_id, count(*) AS reports, max(v.received_at) AS last_report
FROM vote v
LEFT JOIN install i ON i.id = v.user_id
WHERE v.verdict <> 'trustworthy'
GROUP BY coalesce(i.account_id, v.user_id)
ORDER BY reports DESC, last_report DESC
LIMIT %(limit)s
"""

# Settled report narratives, latest first: the narrative that confirmed each, or none when it was rejected.
SETTLEMENTS = """
SELECT r.id AS report_id,
       r.title AS report,
       c.id IS NOT NULL AS confirmed,
       c.title AS settled_by,
       c.source,
       c.source_url,
       first.reported_at,
       r.settled_at,
       r.settled_at - first.reported_at AS time_to_settle
FROM narrative r
LEFT JOIN narrative c ON c.id = r.settled_by
LEFT JOIN LATERAL (SELECT min(received_at) AS reported_at FROM vote WHERE narrative_id = r.id) first ON true
WHERE r.source = 'report' AND r.settled_at IS NOT NULL
ORDER BY r.settled_at DESC
LIMIT %(limit)s
"""

# Each voter's latest feedback vote per narrative and match level. Feedback votes come from the extension's
# tooltip buttons, which record the match level they were cast on.
FEEDBACK_VOTES = """
SELECT DISTINCT ON (v.narrative_id, v.match_level, v.user_id)
       v.narrative_id, n.title, n.source, n.state, v.match_level, v.user_id::text AS user_id, v.verdict
FROM vote v
JOIN narrative n ON n.id = v.narrative_id
WHERE v.match_level IS NOT NULL
ORDER BY v.narrative_id, v.match_level, v.user_id, v.received_at DESC
"""

# Disputed narratives, most recently changed first, with the feedback votes cast on their matches.
DISPUTES = """
SELECT n.id,
       n.title,
       n.source,
       n.updated_at,
       count(v.id) FILTER (WHERE v.verdict = 'trustworthy') AS not_misinformation,
       count(v.id) FILTER (WHERE v.verdict <> 'trustworthy') AS misinformation
FROM narrative n
LEFT JOIN vote v ON v.narrative_id = n.id AND v.match_level IS NOT NULL
WHERE n.state = 'disputed'
GROUP BY n.id
ORDER BY n.updated_at DESC
LIMIT %(limit)s
"""
