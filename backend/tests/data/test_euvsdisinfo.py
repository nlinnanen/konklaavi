from pathlib import Path

from app.seed.euvsdisinfo import import_cases, is_test_case, load_cases

FIXTURE = Path(__file__).parent / "fixtures" / "euvsdisinfo.jsonl"


def counts(conn):
    return tuple(
        conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("narrative", "job", "eval_case")
    )


def test_seed_cases_become_narratives_with_fetch_jobs(conn):
    cases = load_cases(FIXTURE)
    seed = [c for c in cases if not is_test_case(c["id"])]
    test = [c for c in cases if is_test_case(c["id"])]
    assert seed and test

    import_cases(conn, cases)

    narratives = {
        row[4]: row
        for row in conn.execute(
            "SELECT id, title, claim, explanation, source_url, source, state, verdict FROM narrative"
        )
    }
    assert set(narratives) == {c["url"] for c in seed}
    case = seed[0]
    assert narratives[case["url"]][1:] == (
        case["title"],
        case["summary"],
        case["disproof"],
        case["url"],
        "euvsdisinfo",
        "active",
        "misinformation",
    )
    jobs = conn.execute("SELECT kind, payload FROM job").fetchall()
    assert all(kind == "fetch" for kind, _ in jobs)
    assert sorted((p["url"], p["narrative_id"]) for _, p in jobs) == sorted(
        (url, narratives[c["url"]][0]) for c in seed for url in c["article_urls"]
    )
    assert {p["reason"] for _, p in jobs} == {"seed"}
    eval_rows = conn.execute("SELECT case_id, article_urls FROM eval_case").fetchall()
    assert sorted(eval_rows) == sorted((c["id"], c["article_urls"]) for c in test)


def test_split_does_not_depend_on_order_or_subset(conn):
    cases = load_cases(FIXTURE)
    import_cases(conn, cases[::-1])
    reversed_split = conn.execute("SELECT source_url FROM narrative ORDER BY source_url").fetchall()
    conn.execute("TRUNCATE narrative, job, eval_case CASCADE")
    for case in cases:
        import_cases(conn, [case])
    assert conn.execute("SELECT source_url FROM narrative ORDER BY source_url").fetchall() == reversed_split


def test_reimport_changes_nothing(conn):
    cases = load_cases(FIXTURE)
    first = import_cases(conn, cases)
    before = counts(conn)
    second = import_cases(conn, cases)
    assert counts(conn) == before
    assert (second.narratives, second.fetch_jobs, second.eval_cases) == (0, 0, 0)
    assert (first.narratives, first.fetch_jobs, first.eval_cases) == before
