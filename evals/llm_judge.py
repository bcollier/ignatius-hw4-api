"""Eval system A: LLM as judge.

Every piece and conversation from evals.generate is rated blind (no model name) by
each judge, on the 1-7 rubric in app/agent_prompts/eval_judge.md (heart and deep
dive) and eval_judge_companion.md (Talk it over). Five judges are the default
(Claude, GPT-6, Gemini and the two free Jetstream2 models), so no one's taste, or a
preference for its own family's writing, decides alone; the report shows each judge
separately, and each contestant without its own model's judgments.

  .venv/bin/python -m evals.llm_judge [--run NAME] [--judges opus,gpt6-sol,gemini,muse,llama-scout]
"""

import argparse
import asyncio

import httpx

from app import prompts, search

from .common import (COMPANION_SCALES, DEFAULT_JUDGES, SCALES, ModelError, RunDir, chat, check_models,
                     day_context, load_passages, parse_json, research, scripture)

JUDGE = prompts._prompt("eval_judge")
JUDGE_COMPANION = prompts._prompt("eval_judge_companion")


async def piece_prompt(sample: dict, p: dict) -> str:
    parts = [f"Kind of piece: {'For the heart' if sample['track'] == 'heart' else 'Deep dive'}",
             "<day>\n" + day_context(p, await scripture(p)) + "\n</day>"]
    if sample["track"] == "deep":
        parts.append(search.as_prompt((await research(p))["results"]))
        parts.append("<sources_it_listed>\n" + "\n".join(sample["sources"]) + "\n</sources_it_listed>")
    parts.append("<piece>\n" + sample["script"] + "\n</piece>")
    return "\n\n".join(parts)


def conversation_prompt(conv: dict) -> str:
    lines = "\n\n".join(f"{'Person' if t['role'] == 'user' else 'Companion'}: {t['content']}" for t in conv["turns"])
    return f"<companion_instructions>\n{conv['system']}\n</companion_instructions>\n\n<conversation>\n{lines}\n</conversation>"


async def judge(run: RunDir, judge_id: str, kind: str, key: tuple, system: str, user: str, scales: list[str]) -> None:
    async def make():
        print(f"  judging {' '.join(key):32} by {judge_id}")
        for _ in range(2):
            reply = await chat(judge_id, system, user, json_reply=True)
            verdict = parse_json(reply["text"])
            scores = (verdict or {}).get("scores", {})
            if all(isinstance(scores.get(s), (int, float)) for s in scales):
                return {**verdict, "judge": judge_id, "key": list(key), "usd": reply["usd"]}
        raise ModelError(f"{judge_id} didn't return every score as JSON")
    try:
        await run.once(run.path(kind, judge_id, *key), make)
    except (ModelError, httpx.HTTPError) as exc:
        print(f"  FAILED judging {key} by {judge_id}: {exc}")


async def main(args) -> None:
    run = RunDir(args.run)
    judges = args.judges.split(",")
    check_models(judges)
    by_id = {p["id"]: p for p in load_passages()}
    jobs = []
    for sample in run.samples("samples"):
        key = (sample["model"], sample["track"], sample["passage"])
        user = await piece_prompt(sample, by_id[sample["passage"]])
        jobs += [judge(run, j, "llm_judge", key, JUDGE, user, SCALES) for j in judges]
    for conv in run.samples("conversations"):
        key = (conv["model"], "companion", conv["scenario"])
        jobs += [judge(run, j, "llm_judge", key, JUDGE_COMPANION, conversation_prompt(conv), COMPANION_SCALES)
                 for j in judges]
    print(f"System A (LLM as judge): {len(jobs)} judgments")
    await asyncio.gather(*jobs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", default="default")
    parser.add_argument("--judges", default=DEFAULT_JUDGES)
    asyncio.run(main(parser.parse_args()))
