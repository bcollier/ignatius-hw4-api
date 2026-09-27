"""The Agents page: every agent listed with its default prompt, and a person's own
version and model saved to their account and used where that agent runs."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import main, profile, prompts, talk
from app.auth import User, current_user


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


def as_user(user):
    main.app.dependency_overrides[current_user] = lambda: user


def agent(listing, agent_id):
    return next(a for a in listing["agents"] if a["id"] == agent_id)


def test_every_agent_is_listed_with_its_default(client):
    as_user(User("a-full-1", "me@x.y"))
    listing = client.get("/api/agents").json()
    ids = {a["id"] for a in listing["agents"]}
    assert {"plan", "heart_companion", "heart_christ", "deep_dive", "guide_tailor", "inspiration", "companion",
            "companion_spoken", "companion_memory", "my_examen"} <= ids
    assert agent(listing, "deep_dive")["default"] == prompts.DEEP_INSTRUCTIONS
    assert {f["id"] for f in listing["fixed"]} >= {"background", "house_style", "plan_rules"}


def test_a_custom_prompt_is_saved_used_and_reset(client):
    user = User("a-full-2", "me@x.y")
    as_user(user)
    listing = client.put("/api/agents/guide_tailor", json={"prompt": "Tailor the lines gently."}).json()
    assert agent(listing, "guide_tailor")["custom"] == "Tailor the lines gently."

    async def in_a_job():
        await profile.use_agents(user.id)
        return prompts.custom("guide_tailor", prompts.GUIDE_TAILOR)
    assert asyncio.run(in_a_job()) == "Tailor the lines gently."

    listing = client.put("/api/agents/guide_tailor", json={"prompt": ""}).json()
    assert agent(listing, "guide_tailor")["custom"] == ""


def test_the_companion_uses_the_same_saved_prompt_as_the_talk_page(client):
    as_user(User("a-full-3", "me@x.y"))
    client.put("/api/agents/companion", json={"prompt": "You are a quiet listener."})
    assert client.get("/api/profile").json()["companion_prompt"] == "You are a quiet listener."
    client.put("/api/agents/companion", json={"prompt": ""})
    assert agent(client.get("/api/agents").json(), "companion")["custom"] == ""
    assert agent(client.get("/api/agents").json(), "companion")["default"] == talk.COMPANION


def test_a_model_can_be_chosen_without_touching_the_prompt(client):
    as_user(User("a-full-4", "me@x.y"))
    client.put("/api/agents/my_examen", json={"prompt": "Write my Examen briefly."})
    listing = client.put("/api/agents/my_examen", json={"model": "anthropic/claude-haiku-4.5"}).json()
    assert agent(listing, "my_examen")["model"] == "anthropic/claude-haiku-4.5"
    assert agent(listing, "my_examen")["custom"] == "Write my Examen briefly."


def test_bad_changes_are_refused(client):
    as_user(User("a-full-5", "me@x.y"))
    assert client.put("/api/agents/companion_memory", json={"prompt": "Summarize."}).status_code == 400  # needs {limit}
    assert client.put("/api/agents/my_examen", json={"model": "someone/unknown"}).status_code == 400
    assert client.put("/api/agents/plan", json={"model": "anthropic/claude-haiku-4.5"}).status_code == 400  # chosen in New retreat
    assert client.put("/api/agents/nobody", json={"prompt": "x"}).status_code == 400


def test_saving_about_me_keeps_the_agent_prompts(client):
    as_user(User("a-full-6", "me@x.y"))
    client.put("/api/agents/inspiration", json={"prompt": "Choose psalms only."})
    client.put("/api/profile", json={"about": "I pray at dawn."})
    assert agent(client.get("/api/agents").json(), "inspiration")["custom"] == "Choose psalms only."


def test_mine_only_is_just_the_custom_prompts(client):
    as_user(User("a-full-7", "me@x.y"))
    client.put("/api/agents/plan", json={"prompt": "Plan five days."})
    assert client.get("/api/agents?mine_only=true").json() == {"custom": {"plan": "Plan five days."}}
