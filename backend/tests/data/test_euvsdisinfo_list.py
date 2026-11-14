from pathlib import Path

from app.seed.euvsdisinfo_list import Case, parse_list

FIXTURE = Path(__file__).parent / "fixtures" / "euvsdisinfo-list.html"


def test_list_page_gives_total_and_cases_without_prefix():
    total, cases = parse_list(FIXTURE.read_text())
    assert total == 506
    assert cases == [
        Case(
            "The European Commission finally broke Orbán by investing heavily in Tisza’s political project",
            "https://euvsdisinfo.eu/report/the-european-commission-finally-broke-orban-by-investing-heavily"
            "-in-tiszas-political-project/",
            "2026-04-13",
        ),
        Case(
            "EU Mission was likely hit by a Ukrainian antimissile",
            "https://euvsdisinfo.eu/report/eu-mission-was-likely-hit-by-a-ukrainian-antimissile/",
            "2025-09-02",
        ),
    ]
