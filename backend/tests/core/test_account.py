import logging
import re
import uuid


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


def test_flagging_a_reliable_outlet_costs_reputation_across_the_account(client, caplog, vote):
    phone, laptop = uuid.uuid4(), uuid.uuid4()
    for install in (phone, laptop):
        with caplog.at_level(logging.WARNING, logger="app.api.account"):
            client.post("/auth/magic-link", json={"userId": str(install), "email": "reader@example.org"})
        client.get(re.findall(r"/auth/verify\?token=\S+", caplog.text)[-1])

    vote("misinformation", ["s1"], user=phone, domain="www.reuters.com")
    me = client.get("/me", params={"userId": str(laptop)}).json()
    assert (me["agreed"], me["disagreed"]) == (0, 1)
    assert me["reputation"] < client.get("/me", params={"userId": str(uuid.uuid4())}).json()["reputation"]
