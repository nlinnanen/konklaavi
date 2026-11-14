import dataclasses
import logging
import re
import uuid

from app.api import account


def test_a_magic_link_signs_the_install_in_once(client, caplog):
    user = uuid.uuid4()
    assert client.get("/me", params={"userId": str(user)}).json()["email"] is None

    with caplog.at_level(logging.WARNING, logger="app.api.account"):
        response = client.post(
            "/auth/magic-link", json={"userId": str(user), "email": " Reader@Example.org "}
        )
    assert response.json() == {"ok": True}
    path = re.search(r"/auth/verify\?token=\S+", caplog.text).group(0)

    page = client.get(path)
    assert page.status_code == 200
    assert "Signed in as reader@example.org" in page.text
    assert client.get("/me", params={"userId": str(user)}).json()["email"] == "reader@example.org"
    assert client.get(path).status_code == 400


def test_skipping_email_verification_signs_the_install_in_at_once(client, monkeypatch, caplog):
    monkeypatch.setattr(account, "settings", dataclasses.replace(account.settings, auth_skip_email_verification=True))
    user = uuid.uuid4()

    with caplog.at_level(logging.WARNING, logger="app.api.account"):
        response = client.post("/auth/magic-link", json={"userId": str(user), "email": "Reader@Example.org"})
    assert response.json() == {"ok": True, "signedIn": True}
    assert "/auth/verify" not in caplog.text
    assert client.get("/me", params={"userId": str(user)}).json()["email"] == "reader@example.org"


def me(client, user):
    return client.get("/me", params={"userId": str(user)}).json()


def test_reputation_follows_helpful_notes_and_early_agreeing_ratings(client, conn, narrative, signed_in):
    assert (me(client, uuid.uuid4())["verified"], me(client, uuid.uuid4())["reputation"]) == (False, None)
    narrative(["h1"])
    writer = conn.execute("SELECT author_install FROM note").fetchone()[0]
    rater = signed_in()
    client.post("/notes/1/ratings", json={"userId": str(rater), "helpfulness": "somewhat"})
    assert me(client, writer)["reputation"] == me(client, rater)["reputation"] == 0.25

    conn.execute("UPDATE note SET status = 'helpful', decided_at = now()")
    conn.commit()
    assert me(client, writer)["reputation"] > 0.25
    assert me(client, writer)["notes"][0]["status"] == "helpful"
    assert me(client, rater)["ratings"] == {"count": 1, "agreed": 1}
