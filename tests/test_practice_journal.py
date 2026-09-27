"""The guided exercises' journal: private, kept per person."""

from fastapi.testclient import TestClient

from app import main
from app.auth import User, current_user


def test_journal_entries_are_kept_per_person():
    with TestClient(main.app) as client:
        main.app.dependency_overrides[current_user] = lambda: User("pj-1", "a@b.c")
        assert client.get("/api/practice/journal").json()["entries"] == []
        r = client.post("/api/practice/journal", json={"session": "daily", "question": "What happened here?", "answer": " Peace. "})
        assert r.status_code == 200 and r.json()["answer"] == "Peace."
        assert client.post("/api/practice/journal", json={"session": "daily", "question": "q", "answer": "  "}).status_code == 400
        assert client.get("/api/practice/journal").json()["entries"][0]["question"] == "What happened here?"
        main.app.dependency_overrides[current_user] = lambda: User("pj-2", "b@b.c")
        assert client.get("/api/practice/journal").json()["entries"] == []
        main.app.dependency_overrides.clear()
