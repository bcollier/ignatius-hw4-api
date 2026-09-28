"""Step 1: every contestant writes the pieces both eval systems will score.

- For each passage: the reflection for the heart, then the deep dive, which (as in the
  app) reads that model's own heart reflection and the shared web research.
- For each companion scenario: a Talk it over conversation in turns, the person's lines
  scripted, the companion's replies written by the model with the app's instructions.

  .venv/bin/python -m evals.generate [--run NAME] [--models opus,muse] [--passages 2] [--scenarios 3]
"""

import argparse
import asyncio

import httpx

from app import llm, prompts, talk, talk_turns

from .common import (DEFAULT_MODELS, WORDS, ModelError, RunDir, chat, check_models, day_context, load_passages,
                     load_scenarios, research, scripture, system_for)


async def write_day(run: RunDir, model_id: str, p: dict, words: int) -> None:
    text = await scripture(p)
    heart = await _piece(run, model_id, p, "heart", day_context(p, text), words)
    if heart:
        found = await research(p)
        user = llm._deep_user(day_context(p, text, heart=heart["script"]), found["results"])
        await _piece(run, model_id, p, "deep", user, words)


async def _piece(run: RunDir, model_id: str, p: dict, track: str, user: str, words: int) -> dict | None:
    async def make():
        print(f"  writing {track:5} {p['id']:9} {model_id}")
        reply = await chat(model_id, system_for(track, words), user)
        script, sources = llm._split_script(reply["text"])
        return {**reply, "model": model_id, "track": track, "passage": p["id"], "input": user,
                "script": script, "sources": sources, "words": len(script.split()), "target_words": words}
    try:
        return await run.once(run.path("samples", model_id, track, p["id"]), make)
    except (ModelError, httpx.HTTPError) as exc:
        print(f"  FAILED {track} {p['id']} {model_id}: {exc}")
        return None


def companion_system(s: dict, p: dict) -> str:
    """The companion's instructions as talk.context builds them, then the turn-taking addendum."""
    retreat = (f"They are praying the retreat \"{p['retreat']}\". Today is Day {p['day']}: {p['title']} "
               f"({p['ref']}). Grace: {p['grace']}")
    parts = [talk.COMPANION, prompts.BACKGROUND, "Right now: " + s["when"], s["last"], retreat]
    return "\n\n".join(parts) + "\n\n" + talk_turns.SPOKEN


async def converse(run: RunDir, model_id: str, s: dict, passages: dict) -> None:
    async def make():
        print(f"  talking {s['id']:11} {model_id}")
        system, turns, usd, seconds = companion_system(s, passages[s["passage"]]), [], 0.0, 0.0
        for line in [None] + s["turns"]:
            if line:
                turns.append({"role": "user", "content": line})
            said = "\n".join(f"{'Them' if t['role'] == 'user' else 'You'}: {t['content']}" for t in turns)
            ask = (f"The conversation so far:\n{said}\n\nReply with only what you say next." if said else
                   "The person has just started the conversation. Greet them and ask your first question. "
                   "Reply with only what you say.")
            reply = await chat(model_id, system, ask)
            turns.append({"role": "assistant", "content": reply["text"].strip().strip('"')})
            usd, seconds = usd + reply["usd"], seconds + reply["seconds"]
        return {"model": model_id, "scenario": s["id"], "system": system, "turns": turns,
                "usd": round(usd, 5), "seconds": round(seconds, 1)}
    try:
        await run.once(run.path("conversations", model_id, s["id"]), make)
    except (ModelError, httpx.HTTPError) as exc:
        print(f"  FAILED conversation {s['id']} {model_id}: {exc}")


async def main(args) -> None:
    run = RunDir(args.run)
    models = args.models.split(",")
    check_models(models)
    passages = load_passages(args.passages)
    scenarios = load_scenarios(args.scenarios)
    by_id = {p["id"]: p for p in load_passages()}
    print(f"{len(models)} models: {len(passages)} passages x 2 tracks, {len(scenarios)} conversations")
    for p in passages:  # the scripture and research first, once each, so the models share them
        await scripture(p)
        await research(p)
    results = await asyncio.gather(*(write_day(run, m, p, args.words) for m in models for p in passages),
                                   *(converse(run, m, s, by_id) for m in models for s in scenarios),
                                   return_exceptions=True)  # one failure doesn't stop the rest
    for r in results:
        if isinstance(r, Exception):
            print(f"  FAILED: {type(r).__name__}: {r}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", default="default")
    parser.add_argument("--models", default=DEFAULT_MODELS)
    parser.add_argument("--passages", default=None, help="ids, or a number for the first N (default: all)")
    parser.add_argument("--scenarios", default=None, help="ids, or a number (default: all)")
    parser.add_argument("--words", type=int, default=WORDS)
    asyncio.run(main(parser.parse_args()))
