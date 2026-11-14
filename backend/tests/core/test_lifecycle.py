import uuid

from app.jobs import analyze


def age_accounts(conn, days_apart=1):
    """Spread the voters' first votes a day apart, so they form no burst."""
    users = [row[0] for row in conn.execute("SELECT DISTINCT user_id FROM vote ORDER BY user_id")]
    for i, user in enumerate(users):
        conn.execute(
            "UPDATE vote SET received_at = now() - make_interval(days => %s) WHERE user_id = %s",
            (days_apart * (i + 1), user),
        )
    conn.commit()


def run_analyze(conn):
    for (vote_id,) in conn.execute("SELECT id FROM vote ORDER BY id").fetchall():
        analyze.handle(conn, {"vote_id": vote_id})
        conn.commit()


def state(conn, narrative_id):
    return conn.execute("SELECT state FROM narrative WHERE id = %s", (narrative_id,)).fetchone()[0]


def report(conn, narrative, state="under_review"):
    """A report narrative with verdict misleading, holding sentence s1."""
    narrative_id = narrative(["s1"], state=state, verdict="misleading")
    conn.execute("UPDATE narrative SET source = 'report' WHERE id = %s", (narrative_id,))
    conn.commit()
    return narrative_id


def lookup(client, hash):
    return {r["hash"]: r for r in client.post("/lookup", json={"hashes": [hash]}).json()["results"]}[hash]


def test_a_report_becomes_active_once_three_users_call_its_text_negative(conn, narrative, vote):
    narrative_id = report(conn, narrative)
    vote("misinformation", ["s1"])
    vote("manipulative", ["s1"])
    age_accounts(conn)
    run_analyze(conn)
    assert state(conn, narrative_id) == "under_review"

    vote("misinformation", ["s1"])
    age_accounts(conn)
    run_analyze(conn)
    assert state(conn, narrative_id) == "active"


def test_a_disputed_narrative_ships_as_under_review_and_is_not_flagged(conn, client, narrative, vote):
    narrative_id = report(conn, narrative, state="active")
    vote("trustworthy", ["s1"])
    vote("trustworthy", ["s1"])
    age_accounts(conn)
    run_analyze(conn)

    assert state(conn, narrative_id) == "disputed"
    result = lookup(client, "s1")
    assert (result["flagged"], result["signature"]["state"]) == (False, "under_review")


def test_votes_from_a_burst_of_new_accounts_do_not_flag_content(conn, client, vote):
    for _ in range(3):
        vote("misinformation", ["h1"])
    assert lookup(client, "h1")["flagged"] is False

    age_accounts(conn)
    assert lookup(client, "h1")["flagged"] is True


def test_a_voter_who_agreed_with_settled_narratives_outweighs_new_accounts(conn, client, narrative, vote):
    for i in range(3):
        narrative([f"case-{i}"], content_hash=f"case-{i}")
    veteran = uuid.uuid4()
    for i in range(3):
        vote("misinformation", [f"case-{i}"], user=veteran)
    vote("trustworthy", ["h1"], user=veteran)
    vote("misleading", ["h1"])
    vote("misleading", ["h1"])
    age_accounts(conn)

    result = lookup(client, "h1")
    assert (result["flagged"], result["counts"]["misleading"], result["counts"]["trustworthy"]) == (
        False,
        2,
        1,
    )
