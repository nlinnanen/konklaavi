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
