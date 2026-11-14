from pathlib import Path

import pytest

from app.eval.benchmark.cases import import_cases, is_test_case, load_cases, split_urls
from app.eval.benchmark.database import use_benchmark_database

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
    assert seed and test and any(split_urls(c)[1] for c in seed)

    import_cases(conn, cases)

    narratives = {
        row[3]: row for row in conn.execute("SELECT id, title, claim, source_url, source FROM narrative")
    }
    assert set(narratives) == {c["url"] for c in seed}
    case = seed[0]
    assert narratives[case["url"]][1:] == (
        case["title"],
        case["summary"],
        case["url"],
        "note",
    )
    jobs = conn.execute("SELECT kind, payload FROM job").fetchall()
    assert all(kind == "fetch" for kind, _ in jobs)
    assert sorted((p["url"], p["narrative_id"]) for _, p in jobs) == sorted(
        (url, narratives[c["url"]][0]) for c in seed for url in split_urls(c)[0]
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


def test_a_case_with_one_url_holds_nothing_out():
    assert split_urls({"article_urls": ["https://a/1"]}) == (["https://a/1"], [])


def test_a_third_of_the_urls_rounded_up_is_held_out_whatever_their_order():
    urls = [f"https://a/{i}" for i in range(4)]

    seed, held_out = split_urls({"article_urls": urls})
    _, held_out_reversed = split_urls({"article_urls": urls[::-1]})

    assert len(held_out) == 2
    assert sorted(seed + held_out) == urls
    assert sorted(held_out_reversed) == sorted(held_out)


PRODUCT = "postgresql://postgres:postgres@db:5432/postgres"
BENCHMARK = "postgresql://postgres:postgres@db:5432/benchmark"


def test_the_benchmark_points_the_app_at_its_own_database():
    environ = {"DATABASE_URL": PRODUCT, "BENCHMARK_DATABASE_URL": BENCHMARK}

    use_benchmark_database(environ)

    assert environ["DATABASE_URL"] == BENCHMARK


@pytest.mark.parametrize(
    "environ",
    [
        {"DATABASE_URL": PRODUCT},
        {"DATABASE_URL": PRODUCT, "BENCHMARK_DATABASE_URL": PRODUCT.replace("@db:", "@localhost:")},
        {"BENCHMARK_DATABASE_URL": "postgresql://postgres:postgres@localhost:5436/postgres"},
    ],
    ids=["unset", "same database", "product default"],
)
def test_the_benchmark_refuses_the_product_database(environ):
    product = environ.get("DATABASE_URL")

    with pytest.raises(SystemExit):
        use_benchmark_database(environ)

    assert environ.get("DATABASE_URL") == product
