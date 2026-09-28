# Agent prompts: how to change what the agents say and do

Every prompt the app sends to an AI model is a plain-text file in this folder. To change how an agent behaves, edit its file here, run the tests (`.venv/bin/python -m pytest -q`), and push: Render redeploys and the new wording is used from the next call. Nothing else needs to change. The code loads the files through `app/prompts.py` (`_prompt("name")` reads `name.md`).

**In the app, without touching code:** Settings → **Agents** (`?agents`) lists every agent below with its prompt. Anyone can read them, make their own version (saved to their account and used wherever that agent runs, for them only), go back to the default, and, where it makes sense, choose a different model. Editing a file here changes the default for everyone; the Agents page changes it for one person.

A diagram of every agent and its tools: [docs/feature-flows/08-agents.png](../../docs/feature-flows/08-agents.png).

Three kinds of file:

- **Instructions**: how an agent thinks and writes. Safe to rewrite freely.
- **Format rules**: what shape the reply must take (tags, JSON, length). The code parses the reply, so keep the tags and placeholders.
- **Shared blocks**: added to many agents at once.

Placeholders in `{braces}` are filled in by the code; keep them. Files with `## name` headings are read section by section; keep the heading names.

## The agents

### Making a retreat

| Agent | File | When it runs | What it's given | Model | Editable in the app? |
| --- | --- | --- | --- | --- | --- |
| **Planner** | [`plan.md`](plan.md) + format rules [`plan_rules.md`](plan_rules.md) (`{max_days}`) | Once, after an upload | The document's text and images, and any earlier retreats in the series | The chosen model: Claude Opus 5.5 by default, or Muse Glimmer on Jetstream2 (free) | Yes: New retreat → Advanced → "How the retreat is planned" |
| Planner, short form | [`plan_compact.md`](plan_compact.md) | Only when a free model's plan is too long for one reply | As above | Jetstream2 | No |
| **Idea to passages** | [`inspiration.md`](inspiration.md) | "Start from an idea or a photo" | The idea, the number of days, and the photo | The chosen model | No |
| **For the heart** (companion's voice) | [`heart_companion.md`](heart_companion.md) + [`heart_format.md`](heart_format.md) (`{words}`) | Each day | The day's passage, grace, focus, painting, and the retreat so far | The chosen model | Yes: New retreat → "The reflection for the heart" (choose the voice), or Advanced → "The reflection for the heart" (edit it) |
| **For the heart** (Jesus speaking) | [`heart_christ.md`](heart_christ.md) + [`heart_format.md`](heart_format.md) | Each day, when chosen | As above | The chosen model | Yes, as above |
| **Deep dive** (the book study) | [`deep_dive.md`](deep_dive.md) + [`deep_format.md`](deep_format.md) (`{search_note}`, `{words}`) | Each day, after the heart | As above, plus the heart reflection and web research | The chosen model; Claude also searches the web itself | Yes: New retreat → Advanced → "The deep dive" |
| Research notes | [`research.md`](research.md): `on`, `results`, `both`, `off`, `queries` | Deep dive | `queries` writes the search queries; the others tell the writer how to use what was found | The chosen model | No |
| **Spoken guidance** | [`guide_lines.md`](guide_lines.md) (`{day}`, `{title}`, `{grace}`) and [`guide_tailor.md`](guide_tailor.md) | Each day | The default lines, tailored to the day's heart and deep dive | The chosen model | The lines, yes: New retreat → Advanced → "The spoken guidance" (and a checkbox to turn tailoring off). The tailoring prompt, no |

### Talking it over

| Agent | File | When it runs | What it's given | Model | Editable in the app? |
| --- | --- | --- | --- | --- | --- |
| **The companion** (the live conversation, "Talk now") | [`companion.md`](companion.md) | Every conversation | The person's notes, **the time where they are**, **when they last talked** ("about 3 hours ago"), a memory of older talks, recent transcripts, and the retreat day by day (built in `app/talk.py`, `context()`) | OpenAI gpt-live-1 or xAI Grok voice (live), or in turns: Muse Glimmer (free), Claude Fable 5.1 / Opus 5.5 / Haiku 4.5, or OpenAI GPT-5.5 / 5.4 mini | Yes, per person: Talk it over → "The companion's instructions (advanced)" |
| Turn-taking addendum | [`companion_spoken.md`](companion_spoken.md) | Added to the companion in turn-taking mode | | The turns brain | No |
| **Companion's memory** | [`companion_memory.md`](companion_memory.md) (`{limit}`) | When older transcripts get long | The existing memory and the older transcripts | Claude Opus 5.5 (premium) or Jetstream2 | No |

### Practices

| Agent | File | When it runs | Model | Editable in the app? |
| --- | --- | --- | --- | --- |
| **Your own Examen** | [`my_examen.md`](my_examen.md) | Premium: "Make my Examen" | Claude Opus 5.5 | No |
| Guided practices (built once, by hand) | [`practice_writer.md`](practice_writer.md), [`practice_dossier.md`](practice_dossier.md), [`practice_examen.md`](practice_examen.md) | `tools/make_practice.py`, when the built-in practices are rewritten | Claude Opus 5.5 | No (rerun the tool) |

### Shared blocks

| File | Added to |
| --- | --- |
| [`background.md`](background.md) | Every call: the Spiritual Exercises, retreats, lectio divina, how a day is prayed in this app. Shown read-only under Advanced → "What every prompt starts with" |
| [`house_style.md`](house_style.md) | Every script writer (heart, deep dive, guidance): writing for the ear and for prayer. Shown read-only under Advanced → "What every prompt starts with" |
| [`about_the_person.md`](about_the_person.md) | Every call for someone with About me notes: how to use the notes, followed by the notes |
| [`about_me_condense.md`](about_me_condense.md) (`{limit}`) | Only when About me notes are too long: condenses them |
| [`retreat_so_far.md`](retreat_so_far.md) | The heart and deep dive writers from day 2 on, before what earlier days said |
| [`series.md`](series.md) | Every call for a retreat in a series, before the earlier retreats |

## Eval prompts (not used by the app)

These are used only by the offline evals in `evals/`, never by the app itself.

| File | Used by |
| --- | --- |
| [`eval_judge.md`](eval_judge.md), [`eval_judge_companion.md`](eval_judge_companion.md) | `evals/llm_judge.py`: the 1–7 rubric for pieces and for companion conversations |
| `eval_scale_anchored.md`, `eval_scale_ten.md`, `eval_scale_exemplar.md`, `eval_scale_critique.md`, `eval_scale_checklist.md`, `eval_scale_pairwise.md`, `eval_scale_ranking.md` | `evals/scale_study.py`: seven other ways of asking the judges, compared in [docs/evals/SCALE_STUDY.md](../../docs/evals/SCALE_STUDY.md) |

## Changing behavior that isn't a prompt

Some behavior is a setting rather than wording:

- **Models:** `app/config.py` for the defaults (`LLM_MODEL`, `JETSTREAM_MODELS`) and `app/pricing.py` for the menu.
- **Voices:** `app/tts.py` (`FREE_VOICES`, `PREMIUM_VOICES`).
- **Talk it over:**
  - `app/talk_turns.py` for the brains and the reply length;
  - `app/talk.py` for how much history the companion sees (`CONTEXT_MAX_CHARS`) and the time and last-conversation lines.
- **Lengths of each part**: set by the voice's limit, in `app/pipeline.py` (`_words_for`) and `app/tts.py` (`max_chars`).

## Where these came from

Claude Fable 5.1 wrote the main prompts on 26 September 2026 from [`brief.md`](../../docs/prompt-design/brief.md). That brief describes the whole app, what each part receives and produces, where it sits in the listener's day, and Anthropic's prompt-design guidance. [`write_prompts.py`](../../docs/prompt-design/write_prompts.py) is the script that ran it: the shared background and house style first, then each prompt in its own call, building on them. The prompts they replaced are kept in [`previous_prompts.py`](../../docs/prompt-design/previous_prompts.py) and [`companion_current.txt`](../../docs/prompt-design/companion_current.txt).

The Jesus reflection (`heart_christ.md`) was later rewritten from the author's own voice guide. The smaller format rules and notes were moved here from the code on 27 September 2026, unchanged.
