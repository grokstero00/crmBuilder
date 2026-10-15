from crmbuilder_web.config import Settings
from crmbuilder_web.main import create_app
from crmbuilder_web.security import Cipher, hash_password, verify_password


def test_password_hash_roundtrip():
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$") and "correct horse" not in stored
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("x", "garbage")


def test_cipher_needs_the_same_key():
    token = Cipher("k" * 40).encrypt("https://p.bitrix24.ru/rest/1/s/")
    assert "bitrix24" not in token
    assert Cipher("k" * 40).decrypt(token) == "https://p.bitrix24.ru/rest/1/s/"
    try:
        Cipher("z" * 40).decrypt(token)
    except ValueError as exc:
        assert "CRMBUILDER_SECRET_KEY" in str(exc)
    else:
        raise AssertionError("decrypted with a wrong key")


def test_register_login_logout(client):
    assert client.get("/api/auth/me").status_code == 401
    r = client.post("/api/auth/register", json={"email": "Me@Mail.kz", "password": "12345678"})
    assert r.status_code == 201
    assert client.get("/api/auth/me").json()["email"] == "me@mail.kz"

    again = client.post("/api/auth/register", json={"email": "me@mail.kz", "password": "12345678"})
    assert again.status_code == 409

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401

    bad = client.post("/api/auth/login", json={"email": "me@mail.kz", "password": "wrong-pass"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/login", json={"email": "ME@mail.kz", "password": "12345678"})
    assert ok.status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_short_password_and_bad_email(client):
    r = client.post("/api/auth/register", json={"email": "a@b.kz", "password": "short"})
    assert r.status_code == 422
    assert r.json()["detail"] == "Пароль: слишком короткое значение"
    r = client.post("/api/auth/register", json={"email": "nope", "password": "12345678"})
    assert r.json()["detail"] == "Email: неверный формат"


def test_signup_can_be_closed(tmp_path):
    from fastapi.testclient import TestClient

    from crmbuilder_web.migrations import upgrade

    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade(url)
    app = create_app(Settings(database_url=url, secret_key="s" * 40, allow_signup=False))
    r = TestClient(app).post("/api/auth/register", json={"email": "a@b.kz", "password": "12345678"})
    assert r.status_code == 403


def test_secret_key_is_required(monkeypatch):
    import pytest

    monkeypatch.setenv("CRMBUILDER_SECRET_KEY", "short")
    with pytest.raises(RuntimeError, match="CRMBUILDER_SECRET_KEY"):
        Settings.from_env()


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"
