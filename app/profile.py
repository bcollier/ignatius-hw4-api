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
MAX_INPUT = 200_000  # anything longer is cut before condensing

CONDENSE = """Condense these notes a person wrote about themselves, for use by a prayer app that plans retreats and offers a spoken conversation companion. Keep what would help someone accompany them in prayer: their situation and vocation, relationships that matter, what they are carrying or hoping for, their faith background and practice, how they like to pray, images or scripture they return to, and anything they say they want or don't want. Keep their own words where they are vivid. Drop repetition and detail that doesn't serve that purpose. Write in the first person, as plain notes, in no more than {limit} characters."""


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
        "updated_at": meta.get("updated_at"),
        "max_characters": config.PROFILE_MAX_CHARS,
    }


async def about_text(user_id: str) -> str:
    try:
        return (await store.get_file(_paths(user_id)[0])).decode()
    except (StorageError, UnicodeDecodeError):
        return ""


async def save(user_id: str, about: str | None, companion_notes: str | None, full: bool, source: str = "typed") -> dict:
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
    meta = {"summarized": summarized, "original_characters": original, "companion_notes": notes,
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


async def use_for_job(user_id: str) -> None:
    """Make the person's notes part of every model call in the current job. Never
    fails the job: without notes, the calls simply don't include any."""
    try:
        prompts.PERSON.set(await about_text(user_id))
    except Exception:
        log.warning("couldn't load the notes for %s", user_id)
        prompts.PERSON.set("")
