import uuid

from app.jobs.score import score


def add_note(conn, narrative_id, author):
    return conn.execute(
        "INSERT INTO note (narrative_id, author_install, account_id, classification, body, moderation)"
        " SELECT %s, id, account_id, 'misleading', 'Context.', 'visible' FROM install WHERE id = %s RETURNING id",
        (narrative_id, author),
    ).fetchone()[0]


def rate(conn, note_id, installs, helpfulness):
    for install in installs:
        conn.execute("INSERT INTO install (id) VALUES (%s) ON CONFLICT DO NOTHING", (install,))
        conn.execute(
            "INSERT INTO note_rating (note_id, install, helpfulness) VALUES (%s, %s, %s)",
            (note_id, install, helpfulness),
        )


def statuses(conn):
    return dict(conn.execute("SELECT id, status FROM note").fetchall())


def test_a_note_both_camps_find_helpful_is_helpful_and_a_one_sided_one_is_not(conn, narrative, signed_in):
    narrative_id = narrative([])
    author = signed_in()
    left = [signed_in() for _ in range(5)]
    right = [signed_in() for _ in range(5)]
    # Earlier notes the camps split on, which reveal the viewpoint dimension.
    for liked, disliked in ((left, right), (left, right), (right, left), (right, left)):
        note = add_note(conn, narrative_id, author)
        rate(conn, note, liked, "helpful")
        rate(conn, note, disliked, "not_helpful")
    bridging = add_note(conn, narrative_id, author)
    rate(conn, bridging, left + right, "helpful")
    one_sided = add_note(conn, narrative_id, author)
    rate(conn, one_sided, left, "helpful")
    rate(conn, one_sided, right, "not_helpful")

    score(conn)
    result = statuses(conn)
    assert result[bridging] == "helpful"
    assert result[one_sided] == "controversial"
    assert conn.execute("SELECT decided_at IS NOT NULL FROM note WHERE id = %s", (bridging,)).fetchone()[0]


def test_ratings_of_installs_without_an_account_do_not_count(conn, narrative, signed_in):
    narrative_id = narrative([])
    note = add_note(conn, narrative_id, signed_in())
    rate(conn, note, [uuid.uuid4() for _ in range(10)], "helpful")
    score(conn)
    assert statuses(conn)[note] == "needs_more_ratings"
