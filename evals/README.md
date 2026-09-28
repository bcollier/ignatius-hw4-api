# Evals: comparing the models, and checking the judges

Two independent systems score the same pieces, and an analysis checks whether the scoring can be trusted before any ranking is.

| Step | Command | What it does |
| --- | --- | --- |
| 1. Write | `python -m evals.generate --run full` | Every contestant (`--models`, default Claude Opus 5.5, GPT-6 Sol, Muse Glimmer, Gemma 4 31B on Ollama) writes the heart reflection and deep dive for each passage in `passages.json`, and plays the companion in each conversation in `companion_scenarios.json`, with the app's real prompts. Research is gathered once per passage and shared. |
| 2a. System A | `python -m evals.llm_judge --run full --judges gemini,muse,llama-scout` | LLM as judge: each judge scores every piece blind, 1–7, on the rubric in `app/agent_prompts/eval_judge.md` (and `eval_judge_companion.md`). |
| 2b. System B | `python -m evals.deepeval_suite --run full` | All DeepEval: JevEval (Jev, needs `TYPESAFE_API_KEY`), G-Eval (Grok 4.7), Faithfulness, Hallucination, Prompt alignment, Role adherence, Turn relevancy, and rule checks. |
| 3. Check the judges | `python -m evals.analysis --run full` | Writes the data behind the app's Evals page (Settings → Debug mode → Eval analysis): distributions, leniency, Krippendorff's alpha, ICC, Cohen's weighted kappa, scale redundancy, η² with permutation tests, bootstrap intervals for the models, and power. |
| 4. Compare | `python -m evals.compare --run full` | A report of both systems side by side and how far they agree. |

Everything is cached under `evals/runs/<run>/` (not in git), so a rerun only does what's missing. Install the extra packages with `uv pip install -r evals/requirements.txt`. Nothing here touches the production database.
