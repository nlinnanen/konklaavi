"""URL normalization of the demo's input. From the repository root:

    uv run --with pytest --with-requirements demo/proxy-view/viewer.py pytest demo/proxy-view
"""

import pytest
import viewer
from viewer import normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("yle.fi/uutiset/3-123", "https://yle.fi/uutiset/3-123"),
        ("  www.hs.fi/politiikka/  ", "https://www.hs.fi/politiikka/"),
        ("HTTPS://WWW.BBC.com/News", "https://www.bbc.com/News"),
        ("http://yle.fi", "http://yle.fi"),
        ("//yle.fi/a", "https://yle.fi/a"),
        ("yle.fi./a", "https://yle.fi/a"),
        ("yle.fi:443/a", "https://yle.fi/a"),
        ("http://yle.fi:80/a", "http://yle.fi/a"),
        ("yle.fi/a?next=https://example.com#x", "https://yle.fi/a?next=https://example.com#x"),
    ],
)
def test_normalizes(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "ftp://yle.fi/a",
        "https://evil.example\\@yle.fi/",  # Chromium reads the backslash as a slash: evil.example
        "https://evil.example\\.yle.fi/",
        "https://yle.fi@evil.example/",
        "https://user:pass@yle.fi/",
        "yle.fi:22",
        "localhost:8000",
        "https://yle.fi:abc/",
        "https://yle.fi /a",
        "https://yle.fi\t.evil.example/",
        "https://yle.fi\n/a",
        "https://[::1%25lo]/",
        "https://.../",
        "http:yle.fi",
    ],
)
def test_refuses(raw):
    assert normalize_url(raw) is None


def test_normalized_hosts_still_go_through_the_allowlist(monkeypatch):
    monkeypatch.setattr(viewer, "ALLOWED", ["yle.fi"])
    monkeypatch.setenv("SKIP_ADDRESS_CHECK", "1")
    assert viewer.private_reason(normalize_url("uutiset.yle.fi/a")) is None
    assert viewer.private_reason(normalize_url("example.com/a")) is not None
    assert viewer.private_reason(normalize_url("evilyle.fi/a")) is not None
    assert viewer.private_reason(normalize_url("yle.fi.evil.example/a")) is not None
