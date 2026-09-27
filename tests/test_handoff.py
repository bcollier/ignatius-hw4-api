"""Signing in on another device with a code."""

from fastapi.testclient import TestClient

from app import auth, main
from app.auth import User, current_user
from app.routes import handoff


def test_a_code_signs_in_once(monkeypatch):
    monkeypatch.setattr(auth, "enabled", lambda: True)

    async def token(email):
        assert email == "a@b.c"
        return "hashed"

    monkeypatch.setattr(handoff, "_sign_in_token", token)
    handoff._codes.clear()
    handoff._wrong.clear()
    with TestClient(main.app) as client:
        main.app.dependency_overrides[current_user] = lambda: User("u1", "a@b.c")
        code = client.post("/api/handoff").json()["code"]
        assert len(code) == 8
        main.app.dependency_overrides.clear()
        r = client.post("/api/handoff/redeem", json={"code": f"{code[:4]} {code[4:]}"})
        assert r.json() == {"token_hash": "hashed", "type": "magiclink"}
        assert client.post("/api/handoff/redeem", json={"code": code}).status_code == 400  # works once


def test_guests_and_guessing_are_refused(monkeypatch):
    monkeypatch.setattr(auth, "enabled", lambda: True)
    handoff._codes.clear()
    handoff._wrong.clear()
    with TestClient(main.app) as client:
        main.app.dependency_overrides[current_user] = lambda: User("g", "", anonymous=True)
        assert client.post("/api/handoff").status_code == 400
        main.app.dependency_overrides.clear()
        codes = [client.post("/api/handoff/redeem", json={"code": "12345678"}).status_code for _ in range(handoff.MAX_TRIES + 1)]
        assert codes[:handoff.MAX_TRIES] == [400] * handoff.MAX_TRIES and codes[-1] == 429
