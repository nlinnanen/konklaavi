"""SQL the dashboard runs. Kept free of Streamlit so the backend tests can run it against the schema."""

# A narrative's display state, from its visible notes, strongest first: a helpful misleading note, a
# helpful not-misleading note, a controversial note, a note still collecting ratings; tracked otherwise.
NARRATIVES = """
WITH notes AS (
  SELECT narrative_id,
         CASE
           WHEN bool_or(visible AND status = 'helpful' AND classification = 'misleading') THEN 'misleading'
           WHEN bool_or(visible AND status = 'helpful') THEN 'context'
           WHEN bool_or(visible AND status = 'controversial') THEN 'controversial'
           WHEN bool_or(visible AND status = 'needs_more_ratings') THEN 'needs_ratings'
         END AS state,
         count(*) FILTER (WHERE visible) AS visible_notes,
         count(*) FILTER (WHERE moderation = 'pending_review') AS pending_notes
  FROM (SELECT *, moderation = 'visible' AS visible FROM note) note
  GROUP BY narrative_id
),
spread AS (
  SELECT v.narrative_id,
         count(DISTINCT v.id) AS variants,
         count(DISTINCT o.page_id) AS pages,
         count(DISTINCT p.domain_id) AS sites,
         min(f.first_seen) AS first_seen
  FROM variant v
  JOIN fingerprint f ON f.variant_id = v.id
  LEFT JOIN observation o ON o.fingerprint_id = f.id
  LEFT JOIN page p ON p.id = o.page_id
  GROUP BY v.narrative_id
)
SELECT n.id,
       n.title,
       n.source,
       coalesce(nt.state, 'tracked') AS state,
       coalesce(nt.visible_notes, 0) AS visible_notes,
       coalesce(nt.pending_notes, 0) AS pending_notes,
       coalesce(s.variants, 0) AS variants,
       coalesce(s.pages, 0) AS pages,
       coalesce(s.sites, 0) AS sites,
       s.first_seen,
       n.claim,
       n.source_url,
       n.created_at
FROM narrative n
LEFT JOIN notes nt ON nt.narrative_id = n.id
LEFT JOIN spread s ON s.narrative_id = n.id
WHERE n.source = ANY(%(sources)s)
ORDER BY pages DESC, n.created_at DESC, n.id DESC
"""

# Rating counts of a note: every rating, the ones scoring counts (raters with a verified account), and the
# counted ones per helpfulness.
_NOTE_COLUMNS = """
       nt.id,
       nt.narrative_id,
       n.title AS narrative,
       nt.classification,
       nt.status,
       nt.moderation,
       nt.moderation_reason,
       nt.body,
       nt.sources,
       nt.intercept,
       nt.factor,
       count(r.note_id) AS ratings,
       count(DISTINCT COALESCE(i.account_id, r.install)) AS counted,
       count(r.note_id) FILTER (WHERE r.helpfulness = 'helpful') AS helpful,
       count(r.note_id) FILTER (WHERE r.helpfulness = 'somewhat') AS somewhat,
       count(r.note_id) FILTER (WHERE r.helpfulness = 'not_helpful') AS not_helpful,
       nt.created_at
FROM note nt
JOIN narrative n ON n.id = nt.narrative_id
LEFT JOIN note_rating r ON r.note_id = nt.id
LEFT JOIN install i ON i.id = r.install
"""

RECENT_NOTES = f"""
SELECT {_NOTE_COLUMNS}
GROUP BY nt.id, n.title
ORDER BY nt.created_at DESC, nt.id DESC
LIMIT %(limit)s
"""

NARRATIVE_NOTES = f"""
SELECT {_NOTE_COLUMNS}
WHERE nt.narrative_id = %(id)s
GROUP BY nt.id, n.title
ORDER BY nt.moderation = 'visible' DESC, nt.status = 'helpful' DESC, nt.created_at DESC, nt.id DESC
"""

MODERATION_QUEUE = f"""
SELECT {_NOTE_COLUMNS}
WHERE nt.moderation = 'pending_review'
GROUP BY nt.id, n.title
ORDER BY nt.created_at, nt.id
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

# Accounts with notes or ratings, most active first, with one of their install ids for GET /me, which
# computes reputation.
CONTRIBUTORS = """
WITH activity AS (
  SELECT a.id,
         min(i.id::text) AS user_id,
         (SELECT count(*) FROM note WHERE account_id = a.id) AS notes,
         (SELECT count(*) FROM note_rating r JOIN install ri ON ri.id = r.install WHERE ri.account_id = a.id)
           AS ratings
  FROM user_account a
  JOIN install i ON i.account_id = a.id
  GROUP BY a.id
)
SELECT id::text AS account_id, user_id, notes, ratings
FROM activity
WHERE notes + ratings > 0
ORDER BY notes + ratings DESC, id
LIMIT %(limit)s
"""
