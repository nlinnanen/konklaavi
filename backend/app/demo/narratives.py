from psycopg import Connection


def delete_narratives(conn: Connection, narratives: list[int]) -> None:
    """Delete narratives that hold no notes, with their vectors and the fingerprints only notes made.

    A fingerprint fetched from a page stays as page data, detached from its variant like any unassigned
    block; one with no page, a note's passage, goes with its sentences and bands.
    """
    fingerprints = [
        row[0]
        for row in conn.execute(
            "SELECT f.id FROM fingerprint f JOIN variant v ON v.id = f.variant_id"
            " WHERE v.narrative_id = ANY(%s) AND f.first_page_id IS NULL",
            (narratives,),
        )
    ]
    # Deleting a narrative fires no trigger; the index delta lists it as removed from this change.
    conn.execute(
        "INSERT INTO index_change (narrative_id) SELECT unnest(%s::bigint[]) ON CONFLICT DO NOTHING",
        (narratives,),
    )
    conn.execute("DELETE FROM narrative_vector WHERE narrative_id = ANY(%s)", (narratives,))
    conn.execute("DELETE FROM narrative_page WHERE narrative_id = ANY(%s)", (narratives,))
    conn.execute("DELETE FROM sentence WHERE fingerprint_id = ANY(%s)", (fingerprints,))
    conn.execute("DELETE FROM lsh_band WHERE fingerprint_id = ANY(%s)", (fingerprints,))
    conn.execute("DELETE FROM fingerprint WHERE id = ANY(%s)", (fingerprints,))
    conn.execute(
        "UPDATE fingerprint f SET variant_id = NULL FROM variant v WHERE v.id = f.variant_id AND v.narrative_id = ANY(%s)",
        (narratives,),
    )
    conn.execute("DELETE FROM variant WHERE narrative_id = ANY(%s)", (narratives,))
    conn.execute("UPDATE vote SET narrative_id = NULL WHERE narrative_id = ANY(%s)", (narratives,))
    conn.execute("UPDATE narrative SET settled_by = NULL WHERE settled_by = ANY(%s)", (narratives,))
    conn.execute("DELETE FROM narrative WHERE id = ANY(%s)", (narratives,))
