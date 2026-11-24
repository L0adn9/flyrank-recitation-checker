from datetime import UTC, datetime, timedelta

import jwt

from tests.conftest import signup


def test_signup_login_me(client):
    signup(client, email="Sara@Example.com")
    r = client.post("/auth/login", json={"email": "sara@example.com", "password": "correct-horse"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == "sara@example.com"
    assert "password_hash" not in me.json()


def test_duplicate_email_is_409(client):
    signup(client)
    r = client.post("/auth/signup", json={"email": "sara@example.com", "password": "another-pass", "name": "S"})
    assert r.status_code == 409


def test_signup_validation_is_400(client):
    r = client.post("/auth/signup", json={"email": "not-an-email", "password": "short", "name": ""})
    assert r.status_code == 400
    assert set(r.json()["fields"]) == {"email", "password", "name"}


def test_wrong_password_and_unknown_email_look_the_same(client):
    signup(client)
    a = client.post("/auth/login", json={"email": "sara@example.com", "password": "wrong-password"})
    b = client.post("/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_protected_routes_need_a_valid_token(client):
    assert client.get("/attempts").status_code == 401
    assert client.get("/attempts", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    r = client.get("/auth/me")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def test_expired_and_forged_tokens_are_rejected(client):
    from app.config import settings
    signup(client)
    past = datetime.now(UTC) - timedelta(hours=1)
    expired = jwt.encode({"sub": "1", "exp": past}, settings.jwt_secret, algorithm="HS256")
    forged = jwt.encode({"sub": "1", "exp": past + timedelta(days=1)}, "someone-elses-secret" * 3, algorithm="HS256")
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401 and "expired" in r.json()["error"]
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
