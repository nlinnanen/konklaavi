from app.eval.benchmark.download import html_to_text, to_case

ROW = {
    "title": "Claim",
    "summary": "Summary.",
    "response": "",
    "response_html": '<div>\n <p>\n  First, see\n  <a href="#">\n   here\n  </a>\n  .\n </p>\n <p>Second.</p>\n</div>',
    "link": "https://euvsdisinfo.eu/report/some-case/",
    "link_original": ["https://a.example/1", "https://a.example/1", "archive.ph/xyz"],
    "article_language": ["Russian", ""],
    "publication_date": "2025-07-15 00:00:00",
}


def test_disproof_keeps_paragraphs_and_joins_links_to_their_text():
    assert html_to_text(ROW["response_html"]) == "First, see here.\n\nSecond."


def test_case_keeps_distinct_web_article_urls():
    case = to_case(ROW)
    assert case["id"] == "some-case"
    assert case["article_urls"] == ["https://a.example/1"]
    assert case["languages"] == ["Russian"]


def test_row_without_article_url_is_skipped():
    assert to_case(ROW | {"link_original": []}) is None
