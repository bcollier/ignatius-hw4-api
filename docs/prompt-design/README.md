# How the prompts were written

The app's default prompts live in [`app/prompt_texts/`](../../app/prompt_texts/), one plain-text file each:

| File | Used for |
| --- | --- |
| `background.md` | Prepended to every model call: the Exercises, retreats, lectio divina, how a day is prayed in this app |
| `house_style.md` | Appended to every script writer: writing for the ear and for prayer |
| `plan.md` | Planning a retreat from the uploaded document |
| `heart_companion.md`, `heart_christ.md` | The reflection for the heart (a companion's voice, or the voice of Jesus) |
| `deep_dive.md` | The deep dive: history, language, tradition, research |
| `guide_tailor.md` | Tailoring the spoken guidance to the day |
| `companion.md` | Talk it over, the live conversation companion |

They were written by Claude Fable 5.1 on 26 September 2026 from [`brief.md`](brief.md): a description of the whole app, what each part receives and produces, where it sits in the listener's day, and Anthropic's prompt-design guidance. [`write_prompts.py`](write_prompts.py) is the script that ran it: the shared background and house style first, then each prompt in its own call, building on them. The prompts they replaced are kept in [`previous_prompts.py`](previous_prompts.py) and [`companion_current.txt`](companion_current.txt).

In the app, the planning, heart and deep dive prompts are editable under New retreat → Advanced (with the background and house style shown read only), and the companion's under Talk it over → "The companion's instructions (advanced)", saved per person.
