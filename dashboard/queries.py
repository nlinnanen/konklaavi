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

# The continuous aggregate of observations the spread page reads when it exists:
# observation_daily(day, fingerprint_id, page_id, observations).
OBSERVATION_DAILY = "observation_daily"

HAS_OBSERVATION_DAILY = """
SELECT EXISTS (
  SELECT 1 FROM timescaledb_information.continuous_aggregates WHERE view_name = 'observation_daily'
)
"""

_FROM_AGGREGATE = """
SELECT d.day, d.fingerprint_id, d.page_id, d.observations
FROM observation_daily d JOIN members m ON m.id = d.fingerprint_id
"""

_FROM_OBSERVATIONS = """
SELECT time_bucket('1 day', o.seen_at) AS day, o.fingerprint_id, o.page_id, count(*) AS observations
FROM observation o JOIN members m ON m.id = o.fingerprint_id
GROUP BY 1, 2, 3
"""

_SPREAD = """
WITH members AS (
  SELECT f.id FROM fingerprint f JOIN variant v ON v.id = f.variant_id WHERE v.narrative_id = %(id)s
),
daily AS ({daily}),
by_domain AS (
  SELECT d.day, p.domain_id, d.page_id, d.observations FROM daily d JOIN page p ON p.id = d.page_id
)
"""

_SPREAD_PER_DAY = """
, first_seen AS (SELECT domain_id, min(day) AS day FROM by_domain GROUP BY domain_id)
SELECT b.day,
       sum(b.observations)::bigint AS observations,
       count(DISTINCT b.page_id) AS pages,
       count(DISTINCT b.domain_id) AS domains,
       (SELECT count(*) FROM first_seen f WHERE f.day <= b.day) AS domains_so_far
FROM by_domain b
GROUP BY b.day
ORDER BY b.day
"""

_SPREAD_DOMAINS = """
SELECT dm.name AS domain,
       min(b.day) AS first_day,
       max(b.day) AS last_day,
       count(DISTINCT b.page_id) AS pages,
       sum(b.observations)::bigint AS observations
FROM by_domain b JOIN domain dm ON dm.id = b.domain_id
GROUP BY dm.name
ORDER BY first_day, domain
"""


def _spread(query: str, aggregate: bool) -> str:
    return _SPREAD.format(daily=_FROM_AGGREGATE if aggregate else _FROM_OBSERVATIONS) + query


def spread_per_day(aggregate: bool) -> str:
    """Per day, a narrative's observations, pages and domains, and the domains it has reached so far.

    Reads the continuous aggregate when `aggregate` is true, else the observation table.
    """
    return _spread(_SPREAD_PER_DAY, aggregate)


def spread_domains(aggregate: bool) -> str:
    """Per domain, the first and last day a narrative was seen there, its pages and observations."""
    return _spread(_SPREAD_DOMAINS, aggregate)
