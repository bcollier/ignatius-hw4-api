# Feature flows

One slide per feature. Each has three columns:
- **The screen:** a real iPhone screenshot of that feature.
- **Server and data:** the FastAPI endpoints and modules on Render behind it, and what they read and write in Supabase (the `retreats` table, the private `retreats` bucket, `llm_calls`, the profile).
- **Models and services:** the AI models and outside services it uses, with the model ids and defaults from the code.

| # | Slide | Screen shown |
| --- | --- | --- |
| 1 | [Signing in](01-sign-in.png) | Signed out on the phone, and "Sign in on your phone" (QR code) on a laptop |
| 2 | [Making a retreat from a document](02-new-retreat.png) | New retreat |
| 3 | [Building a day](03-build-day.png) | The build progress screen, and the research page for a deep dive |
| 4 | [Praying a day](04-pray.png) | The player |
| 5 | [Talk it over and Talk now](05-talk.png) | A turns conversation with the companion |
| 6 | [Your own Examen](06-my-examen.png) | Practice → Write a new Examen |
| 7 | [A retreat from an idea or a photo](07-idea.png) | New retreat → or start from an idea or a photo |

Each slide has an HTML source (`NN-name.html`, styled by `slides.css`, with its screenshots in `screens/`). The PNGs are those pages rendered at 1600 × 900 in headless Chrome. To change a slide:
1. Edit its HTML.
2. Open it in a browser window set to 1600 × 900.
3. Take a screenshot.

The facts on them match the code as of September 27, 2026, including:
- the defaults in `app/config.py`, `app/pricing.py`, `app/talk.py`, `app/talk_turns.py`, `app/tts.py` and `app/search.py`;
- the storage paths in `app/pipeline.py`, `app/talk.py`, `app/my_examen.py` and `app/profile.py`.

If one of those changes, the slide should change with it.
