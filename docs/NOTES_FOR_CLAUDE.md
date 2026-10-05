# Working notes for Claude (Ignatius at Home)

Handoff notes so a fresh session can pick up without the old conversation. Last updated 2026-10-05. Keep this file current when you finish a piece of work.

## The project

- **Web:** `~/Code/ignatius-hw4-web` (GitHub `bcollier/ignatius-hw4-web`), plain HTML/CSS/JS with no build step, on GitHub Pages: https://bcollier.github.io/ignatius-hw4-web/
- **API:** `~/Code/ignatius-hw4-api` (GitHub `bcollier/ignatius-hw4-api`), FastAPI on Render: https://ignatius-hw4-api.onrender.com
- **Data:** Supabase holds the `retreats` table (one JSON `data` column per retreat), the `llm_calls` table, and a private storage bucket `retreats`. Each user's files are under `{user_id}/…`.
- **Ben's account:** user id `7423e07a-dff6-4020-885e-236b08edcd28`. The Week 3 retreat (PrepDays PU3) is `b1d81236-646d-4755-8966-fa9ceff92744`; its series is weeks 1 (`fc8e36e6…`) and 2 (`eac523db…`).
- **Secrets** are only in `ignatius-hw4-api/.env` and on Render. Never print, paste or commit them. `.env` has `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, OpenRouter, ElevenLabs, Jetstream, `TYPESAFE_API_KEY` and `CRON_SECRET`.

## Standing rules (from Ben)

1. **Log every prompt** Ben types in `PROMPT_LOG.md` in **both** repos, with the same numbered entry in each (`### N. date, time UTC`, the prompt quoted, then "**What was done:**"), and update the "N in all" count in the header. The logs are at **283**; the next entry is 284. This is a homework requirement.
2. **Commit trailers:** `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: …`, plus any trailer a system reminder gives.
3. **Before pushing the API, check the server is idle.** A push restarts Render and kills running builds. Check the latest `llm_calls.created_at` and the retreats' `days.*.listening.updated_at`. The script `push_when_quiet.sh` in the old scratchpad did this: it pushed after 10 quiet minutes. Rewrite it if needed. Web pushes are always safe.
4. **Never substitute pictures in a user-uploaded retreat.** The handout images are chosen prayer icons. Only fix how they're displayed (small images are framed in `look.js` and CSS).
5. **Feature queue:** always keep 5 designed features ready and 1 being built. The designs live in the Claude Doc "Ignatius at Home: Next Feature Designs": https://claude.ai/code/artifact/e2c511d8-ce5a-4ff2-bebc-83616f693b5a. After building one, mark it shipped there and write a replacement design.
6. Never call anything "spiritual direction". The companion and saints are labelled as not spiritual direction.
7. Copyrighted handouts stay out of public repos and evals. Scripture comes from the World English Bible via bible-api.com.
8. Don't send Ben's email address to unrelated services.
9. Free test builds on Jetstream with edge-tts are pre-approved. Ask before spending real money: premium builds, the expensive eval run, Twilio.

## How to run and test locally

```
cd ~/Code/ignatius-hw4-api
LOCAL_MODE=1 SUPABASE_URL= DATA_DIR=/tmp/ignatius-rd2 ALLOWED_ORIGINS=http://localhost:5500,http://localhost:5501 .venv/bin/uvicorn app.main:app --port 8001
cd ~/Code/ignatius-hw4-web && python3 -m http.server 5501
```

- In the browser, set `localStorage.apiBase = "http://localhost:8001"`.
- A local test retreat is `e6c5c60e-5cf9-4de2-b5e4-bc0ac79ed966` ("Be Still and My Shepherd").
- Tests: `.venv/bin/python -m pytest -q tests` (161 pass).
- Lint: `.venv/bin/ruff check --select F,E9,B --ignore B008 app evals tests`.
- Screenshots use puppeteer-core with system Chrome. The old scripts were in the scratchpad's `shots/`, now likely gone. Pattern: `NODE_PATH=…/node_modules node script.js` with `executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"`.
- **Web cache-busting:** bump `?v=N` in `index.html` on every web change. It's now `v=117`.
- After any inline-script change, run `python3 tools/csp_hash.py` (it checks the CSP hash).
- The web scripts share one global scope, so name clashes break the app. Prefix the globals in new files (e.g. `ev…` in evals.js, `hl…` in highlights.js).

## What was built recently (Sept 27 – Oct 5)

- **Evals:**
  - The page is `?evals` (debug mode on), with charts annotated with how to read them.
  - Fitness and cost: `evals/fitness.py`, documented in `docs/evals/FITNESS.md`.
  - Scale study: `evals/scale_study.py`, results in `docs/evals/SCALE_STUDY.md`. The revised rubric v2 (`eval_scale_v2.md`) cut the top-score pile-up from 79% to 27%. Pairwise comparison is the best way to compare models.
- **Not yet done on evals:**
  - The reduced rubric (5 scales, 2 flags, a fruits checklist), as v3.
  - The expensive judges run (Opus, GPT‑6, DeepEval/Jev, about $25) needs Ben's go-ahead.
  - The slide deck and whitepaper on evals and Jev.
- **Live features shipped this stretch:**
  - Hands-free talking.
  - The file preview (title and description) on upload.
  - Server start-up timing, with a chart in Settings (debug) and a daily GitHub Actions check.
  - Highlights, with a weekly email or text option.
  - The handout's front matter ("Before you begin") above Day 1.
  - Resume (Continue or Start from the beginning) across devices.
  - The calendar feed, with timed events and practices (Settings → Your calendar).
  - "Last prayed …" at the top of each day.
  - Apple Health Mindful Minutes through a Shortcut.
  - Sleep with prayer: 10, 30 and 45 minutes, in the Irish voice Colleen (`1OYA2kgM85gF2eGN8HEp`).
  - 20 background sounds.
  - The motion and performance pass.
  - A photo of your page: handwriting and marks read from the photo, kept as a PDF with the day (`app/page_notes.py`).
- **Ben's setup to-dos (remind him):**
  - Resend: `RESEND_API_KEY` and `MAIL_FROM` on Render, plus a verified domain, for weekly highlight emails.
  - Copy `CRON_SECRET` from `.env` to Render. The GitHub secret is already set.
  - Twilio is optional (paid, needs A2P 10DLC).
  - Rotate the TypeSafe key, which showed partly in a screenshot.
  - Custom SMTP for sign-in emails (see `docs/IMPROVEMENTS.md`).
- **Not yet verified on a real iPhone:**
  - Hands-free microphone.
  - The Apple Health Shortcut flow.
  - Sleep audio with the screen locked.
  - The first real cold-start reading (the daily probe should be filling the Settings chart now).

## Where the docs are

- Architecture: `docs/ARCHITECTURE.md`.
- Prompts: `app/agent_prompts/` (its README is the index).
- Product roadmap (Claude Doc): https://claude.ai/code/artifact/69b4effe-73d9-4fc0-8d23-858d40c67a89
- Feature designs (Claude Doc): https://claude.ai/code/artifact/e2c511d8-ce5a-4ff2-bebc-83616f693b5a

## Next step

Ben was asked to pick which of the 5 designs to build. **I recommended 1, My prayer journal and movements.** Nothing is building yet.
