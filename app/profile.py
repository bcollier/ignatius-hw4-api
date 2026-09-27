"""What a person tells the app about themselves, saved as "user info.md" in their own
storage folder, and what they're looking for in the conversation companion.

It informs every model call made for them (planning, writing, tailoring, and the
live conversation). If what they give is longer than PROFILE_MAX_CHARS, a model
condenses it and the profile is marked as a summary, so the page can say so.
"""

import json
import logging
import time

from . import config, llm, llm_log, pricing, prompts
from .storage import StorageError, store

log = logging.getLogger(__name__)

MAX_NOTES = 4000  # what they want from the companion
MAX_COMPANION_PROMPT = 60_000  # the default companion prompt is about 21,000 characters
MAX_INPUT = 200_000  # anything longer is cut before condensing

CONDENSE = prompts._prompt("about_me_condense")


def _paths(user_id: str) -> tuple[str, str]:
    return f"{user_id}/user info.md", f"{user_id}/profile.json"


async def load(user_id: str) -> dict:
    md_path, meta_path = _paths(user_id)
    try:
        meta = json.loads(await store.get_file(meta_path))
    except (StorageError, ValueError):
        meta = {}
    try:
        about = (await store.get_file(md_path)).decode()
    except StorageError:
        about = ""
    return {
        "file": "user info.md",
        "about": about,
        "summarized": bool(meta.get("summarized")) and bool(about),
        "original_characters": meta.get("original_characters", len(about)),
        "companion_notes": meta.get("companion_notes", ""),
        # The companion's instructions, if the person replaced the default (empty = default).
        "companion_prompt": meta.get("companion_prompt", ""),
        "updated_at": meta.get("updated_at"),
        "max_characters": config.PROFILE_MAX_CHARS,
    }


async def _meta(user_id: str) -> dict:
    try:
        return json.loads(await store.get_file(_paths(user_id)[1]))
    except (StorageError, ValueError):
        return {}


async def agent_settings(user_id: str) -> dict:
    """The person's own agent prompts and models, from the Agents page:
    {"prompts": {agent id: text}, "models": {agent id: model id}}. The companion's
    prompt is kept where it always was (companion_prompt) and appears here as "companion"."""
    meta = await _meta(user_id)
    custom = dict(meta.get("agents") or {})
    if meta.get("companion_prompt"):
        custom["companion"] = meta["companion_prompt"]
    return {"prompts": custom, "models": dict(meta.get("agent_models") or {})}


async def save_agent(user_id: str, agent_id: str, prompt: str | None, model: str | None) -> None:
    """Set the person's prompt and model for one agent: None leaves it as it is, ""
    goes back to the default."""
    meta = await _meta(user_id)
    if prompt is None:
        pass
    elif agent_id == "companion":
        meta["companion_prompt"] = prompt.strip()[:MAX_COMPANION_PROMPT]
    else:
        agents = dict(meta.get("agents") or {})
        if prompt.strip():
            agents[agent_id] = prompt.strip()
        else:
            agents.pop(agent_id, None)
        meta["agents"] = agents
    models = dict(meta.get("agent_models") or {})
    if model:
        models[agent_id] = model
    elif model is not None:
        models.pop(agent_id, None)
    meta["agent_models"] = models
    meta["updated_at"] = time.time()
    await store.put_file(_paths(user_id)[1], json.dumps(meta).encode(), "application/json")


async def about_text(user_id: str) -> str:
    try:
        return (await store.get_file(_paths(user_id)[0])).decode()
    except (StorageError, UnicodeDecodeError):
        return ""


async def save(user_id: str, about: str | None, companion_notes: str | None, full: bool, source: str = "typed",
               companion_prompt: str | None = None) -> dict:
    current = await load(user_id)
    summarized, original = current["summarized"], current["original_characters"]
    if about is not None:
        about = about.strip()[:MAX_INPUT]
        original, summarized = len(about), False
        if len(about) > config.PROFILE_MAX_CHARS:
            about = await condense(about, full)
            summarized = True
        await store.put_file(_paths(user_id)[0], about.encode(), "text/markdown")
    notes = current["companion_notes"] if companion_notes is None else companion_notes.strip()[:MAX_NOTES]
    prompt = current["companion_prompt"] if companion_prompt is None else companion_prompt.strip()[:MAX_COMPANION_PROMPT]
    meta = {**await _meta(user_id),  # keeps the agent prompts and models
            "summarized": summarized, "original_characters": original, "companion_notes": notes, "companion_prompt": prompt,
            "updated_at": time.time(), "source": source}
    await store.put_file(_paths(user_id)[1], json.dumps(meta).encode(), "application/json")
    return await load(user_id)


async def condense(text: str, full: bool) -> str:
    """Shorten long notes with a model: the default Claude model for premium users,
    the default free model otherwise. Falls back to cutting at the limit."""
    limit = config.PROFILE_MAX_CHARS
    free_models = [m for m, _ in pricing.jetstream_models()]
    model = config.LLM_MODEL if full or not free_models else free_models[0]
    meter = pricing.Meter(model, await pricing.prices())
    llm_log.tag(purpose="profile")
    try:
        summary = await llm.condense_text(CONDENSE.format(limit=limit), text, meter)
    except Exception:
        log.warning("couldn't condense a profile; cutting it instead")
        summary = ""
    summary = summary.strip()
    if not summary:
        cut = text[:limit]
        summary = cut[: cut.rfind("\n") if cut.rfind("\n") > limit // 2 else limit]
    return summary[: limit + 500]


async def use_agents(user_id: str) -> None:
    """Make the person's own agent prompts apply to every model call in the current job.
    Never fails the job: without them, the defaults are used."""
    try:
        prompts.CUSTOM.set((await agent_settings(user_id))["prompts"])
    except Exception:
        log.warning("couldn't load the agent prompts for %s", user_id)
        prompts.CUSTOM.set({})


async def use_for_job(user_id: str) -> None:
    """Make the person's notes part of every model call in the current job. Never
    fails the job: without notes, the calls simply don't include any."""
    try:
        prompts.PERSON.set(await about_text(user_id))
    except Exception:
        log.warning("couldn't load the notes for %s", user_id)
        prompts.PERSON.set("")
