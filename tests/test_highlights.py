import pytest
from fastapi.testclient import TestClient

from app import config, main, notify


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        for h in c.get("/api/highlights").json()["highlights"]:
            c.delete(f"/api/highlights/{h['id']}")
        yield c


def test_highlights_are_saved_listed_and_deleted(client):
    first = client.post("/api/highlights", json={"text": "  the well   is deep ", "retreat_id": "r1", "day": 3, "part": "reading1"}).json()
    assert first["text"] == "the well is deep"
    assert client.post("/api/highlights", json={"text": "the well is deep", "retreat_id": "r1"}).json()["id"] == first["id"]
    client.post("/api/highlights", json={"text": "living water", "retreat_id": "r1", "day": 3})
    listed = client.get("/api/highlights").json()
    assert [h["text"] for h in listed["highlights"]][:2] == ["living water", "the well is deep"]
    assert client.delete(f"/api/highlights/{first['id']}").status_code == 204
    assert len(client.get("/api/highlights").json()["highlights"]) == 1


def test_weekly_needs_a_way_of_sending_and_a_valid_number(client, monkeypatch):
    assert client.put("/api/highlights/weekly", json={"email": True}).status_code == 400  # no Resend key here
    monkeypatch.setattr(notify, "sms_ready", lambda: True)
    assert client.put("/api/highlights/weekly", json={"sms": True, "phone": "412-555"}).status_code == 400
    assert client.put("/api/highlights/weekly", json={"sms": True, "phone": "+1 (412) 555-0100"}).json()["phone"] == "+14125550100"
    client.put("/api/highlights/weekly", json={})


def test_send_weekly_needs_the_secret_and_sends_the_oldest(client, monkeypatch):
    assert client.post("/api/highlights/send-weekly").status_code == 403
    monkeypatch.setattr(config, "CRON_SECRET", "s3cret")
    monkeypatch.setattr(notify, "sms_ready", lambda: True)
    sent = []

    async def fake_sms(to, text):
        sent.append((to, text))

    monkeypatch.setattr(notify, "send_sms", fake_sms)
    client.post("/api/highlights", json={"text": "be still, and know", "retreat_title": "Be Still", "day": 1})
    client.put("/api/highlights/weekly", json={"sms": True, "phone": "+14125550100"})
    r = client.post("/api/highlights/send-weekly", headers={"X-Cron-Secret": "s3cret"}).json()
    assert r["sent"] == 1 and "be still, and know" in sent[0][1] and "Be Still · Day 1" in sent[0][1]
    client.put("/api/highlights/weekly", json={})
