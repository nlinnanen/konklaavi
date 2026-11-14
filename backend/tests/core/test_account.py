import uuid


def me(client, user):
    return client.get("/me", params={"userId": str(user)}).json()


def credentials(user, password="correct horse"):
    return {"userId": str(user), "email": " Reader@Example.org ", "password": password}


def test_registering_signs_the_install_in(client):
    user = uuid.uuid4()
    assert me(client, user)["email"] is None

    response = client.post("/auth/register", json=credentials(user))
    assert response.status_code == 200
    assert response.json()["email"] == me(client, user)["email"] == "reader@example.org"
    assert client.post("/auth/register", json=credentials(uuid.uuid4())).status_code == 409


def test_signing_in_links_another_install_to_the_same_account(client):
    first, second = uuid.uuid4(), uuid.uuid4()
    client.post("/auth/register", json=credentials(first))

    assert client.post("/auth/login", json=credentials(second)).status_code == 200
    assert me(client, second)["email"] == "reader@example.org"
    client.post("/auth/logout", json={"userId": str(second)})
    assert me(client, second)["email"] is None
    assert me(client, first)["email"] == "reader@example.org"


def test_a_wrong_password_is_rejected(client):
    user = uuid.uuid4()
    client.post("/auth/register", json=credentials(uuid.uuid4()))

    assert client.post("/auth/login", json=credentials(user, "wrong password")).status_code == 401
    assert me(client, user)["email"] is None


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


def test_successful_sign_ins_are_not_rate_limited(client):
    user = uuid.uuid4()
    client.post("/auth/register", json=credentials(user))

    for _ in range(15):
        assert client.post("/auth/login", json=credentials(user)).status_code == 200
