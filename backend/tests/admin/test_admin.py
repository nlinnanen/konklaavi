import dataclasses
import re

import pytest
from fastapi.testclient import TestClient

from app import trust
from app.api.account import hasher
from app.api.main import app

PASSWORD = "correct horse battery"


@pytest.fixture
def client(conn):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def account(conn):
    """Add an account with PASSWORD for the email; returns its id."""

    def add(email):
        account_id = conn.execute(
            "INSERT INTO user_account (email, password_hash) VALUES (%s, %s) RETURNING id",
            (email, hasher.hash(PASSWORD)),
        ).fetchone()[0]
        conn.commit()
        return account_id

    return add


def sign_in(client, email):
    return client.post("/admin/login", data={"email": email, "password": PASSWORD}, follow_redirects=False)


def test_a_sign_in_keeps_the_browser_signed_in_across_requests(client, account):
    account("mod@example.org")
    assert client.get("/admin/", follow_redirects=False).headers["location"] == "./login"

    response = sign_in(client, "mod@example.org")
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=604800" in cookie

    for path in ("/admin/", "/admin/notes", "/admin/users"):
        page = client.get(path, follow_redirects=False)
        assert page.status_code == 200, path
        assert "mod@example.org" in page.text

    client.post("/admin/logout", follow_redirects=False)
    assert client.get("/admin/", follow_redirects=False).status_code == 303


def test_a_wrong_password_opens_no_session(client, account):
    account("mod@example.org")
    response = client.post("/admin/login", data={"email": "mod@example.org", "password": "wrong password"})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def held_note(conn, account_id):
    install = conn.execute(
        "INSERT INTO install (id, account_id) VALUES (gen_random_uuid(), %s) RETURNING id", (account_id,)
    ).fetchone()[0]
    narrative_id = conn.execute(
        "INSERT INTO narrative (title, claim, source) VALUES ('Claim', 'Claim.', 'note') RETURNING id"
    ).fetchone()[0]
    note_id = conn.execute(
        "INSERT INTO note (narrative_id, author_install, account_id, classification, body)"
        " VALUES (%s, %s, %s, 'misleading', 'Context.') RETURNING id",
        (narrative_id, install, account_id),
    ).fetchone()[0]
    conn.commit()
    return note_id


def test_only_moderators_approve_or_hide_notes(client, conn, account, monkeypatch):
    monkeypatch.setattr(
        trust,
        "settings",
        dataclasses.replace(trust.settings, moderator_emails=frozenset({"mod@example.org"})),
    )
    note_id = held_note(conn, account("author@example.org"))
    account("reader@example.org")
    account("mod@example.org")

    sign_in(client, "reader@example.org")
    refused = client.post(
        f"/admin/moderation/notes/{note_id}", data={"action": "approve"}, headers={"HX-Request": "true"}
    )
    assert refused.status_code == 403
    assert (
        conn.execute("SELECT moderation FROM note WHERE id = %s", (note_id,)).fetchone()[0]
        == "pending_review"
    )

    sign_in(client, "mod@example.org")
    approved = client.post(
        f"/admin/moderation/notes/{note_id}", data={"action": "approve"}, headers={"HX-Request": "true"}
    )
    assert approved.status_code == 200
    assert f'id="note-{note_id}"' in approved.text
    conn.rollback()
    assert conn.execute("SELECT moderation FROM note WHERE id = %s", (note_id,)).fetchone()[0] == "visible"


def test_an_anonymous_note_is_listed_with_its_install_and_counts_one_rating_per_rater(client, conn, account):
    reader = account("mod@example.org")
    author, rater, phone, laptop = (
        conn.execute(
            "INSERT INTO install (id, account_id) VALUES (gen_random_uuid(), %s) RETURNING id", (a,)
        ).fetchone()[0]
        for a in (None, None, reader, reader)
    )
    narrative_id = conn.execute(
        "INSERT INTO narrative (title, claim, source) VALUES ('Claim', 'Claim.', 'note') RETURNING id"
    ).fetchone()[0]
    note_id = conn.execute(
        "INSERT INTO note (narrative_id, author_install, classification, body, status, decided_by, moderation)"
        " VALUES (%s, %s, 'misleading', 'Context.', 'helpful', 'agreement', 'visible') RETURNING id",
        (narrative_id, author),
    ).fetchone()[0]
    for install in (rater, phone, laptop):
        conn.execute(
            "INSERT INTO note_rating (note_id, install, helpfulness) VALUES (%s, %s, 'helpful')",
            (note_id, install),
        )
    conn.commit()
    sign_in(client, "mod@example.org")

    anonymous = f"Anonymous install {str(author)[:8]}"
    for page in (client.get("/admin/notes?rule=agreement"), client.get(f"/admin/narratives/{narrative_id}")):
        assert anonymous in page.text
        assert "by agreement" in page.text
        # The reader's two installs count once.
        assert "2 helpful" in page.text and "+1 uncounted" in page.text
    assert "Context." not in client.get("/admin/notes?rule=bridging").text
    assert anonymous in client.get("/admin/users").text


def unit(i):
    """A 384-dimension unit vector pointing mostly along axis 0, tilted towards axis i."""
    v = [0.0] * 384
    v[0], v[i] = 0.9, (1 - 0.81) ** 0.5
    return str(v)


def test_the_narrative_page_groups_blocks_by_how_they_joined(client, conn, account):
    account("mod@example.org")
    claim = "Sanctions are destroying Europe from within."
    narrative_id = conn.execute(
        "INSERT INTO narrative (title, claim, source) VALUES (%s, %s, 'note') RETURNING id", (claim, claim)
    ).fetchone()[0]
    variant_id = conn.execute(
        "INSERT INTO variant (narrative_id) VALUES (%s) RETURNING id", (narrative_id,)
    ).fetchone()[0]
    domain_id = conn.execute("INSERT INTO domain (name) VALUES ('news.example') RETURNING id").fetchone()[0]
    page_id = conn.execute(
        "INSERT INTO page (url, domain_id) VALUES ('https://news.example/a', %s) RETURNING id", (domain_id,)
    ).fetchone()[0]
    signature = list(range(128))
    blocks = [
        # (text, page, sentence hashes, minhash, embedding)
        (claim, None, ["s1"], signature, None),
        (
            "Sanctions are destroying Europe from within. Prices keep rising.",
            page_id,
            ["s1", "s9"],
            None,
            None,
        ),
        (
            "Sanctions are wrecking Europe from the inside, they say.",
            page_id,
            ["s7"],
            signature[:100] + [-1] * 28,
            None,
        ),
        ("Europe pays the price of its own punishments.", page_id, ["s8"], None, unit(1)),
    ]
    for i, (text, page, hashes, minhash, embedding) in enumerate(blocks):
        fid = conn.execute(
            "INSERT INTO fingerprint (content_hash, content, variant_id, first_page_id, minhash, embedding)"
            " VALUES (%s, %s, %s, %s, %s, %s::vector) RETURNING id",
            (f"c{i}", text, variant_id, page, minhash, embedding),
        ).fetchone()[0]
        for h in hashes:
            conn.execute("INSERT INTO sentence (hash, fingerprint_id) VALUES (%s, %s)", (h, fid))
    conn.execute(
        "INSERT INTO narrative_vector (narrative_id, kind, embedding) VALUES (%s, 'claim', %s::vector)",
        (narrative_id, unit(2)),
    )
    conn.commit()
    sign_in(client, "mod@example.org")

    page = client.get(f"/admin/narratives/{narrative_id}")
    assert page.status_code == 200
    groups = dict(re.findall(r'data-level="(\w+)">(.*?)</ul>', page.text, re.DOTALL))
    assert claim in groups["passage"]
    assert "Prices keep rising." in groups["exact"] and "1 shared sentence" in groups["exact"]
    assert "wrecking Europe" in groups["edited"] and "Jaccard 0.78" in groups["edited"]
    # cos = 0.81 between the block's and the claim's vectors.
    assert "own punishments" in groups["embedding"] and "cosine 0.810" in groups["embedding"]
