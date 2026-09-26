# Ignatius at Home: API

Backend for **Ignatius at Home**, which turns material you have rights to (a prayer handout, a few scripture passages, a reading, with images) into a guided audio retreat. For each day it produces three MP3 tracks:

1. **The reading**: the day's passage, word for word from your document.
2. **For the heart**: a short reflection addressed to the listener.
3. **Deep dive**: the theology, history and hermeneutics of the passage, researched with web search, with sources listed on screen.

The frontend (GitHub Pages) is in [ignatius-hw4-web](https://github.com/bcollier/ignatius-hw4-web). It plays each day in a lectio sequence with a bell-framed pause to reflect.

Built with FastAPI and deployed on Render. Claude (Opus 5, through OpenRouter) plans the retreat and writes the scripts; Microsoft neural voices (free, via `edge-tts`) or ElevenLabs (premium) record them; Supabase handles sign-in, saved retreats and file storage.

## How it works

**Full documentation with diagrams:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). It covers the system and hosting diagrams, the database ERD, sign-in and job sequence diagrams, the prayer player, status lifecycles, the complete API reference, costs, security, and failure handling.

```
browser ── POST /api/retreats (PDF or .docx) ──▶ extract text, images, scanned pages (PyMuPDF, python-docx)
        ◀── 202 {id, status: "planning"} ─────── background job: Claude plans the days (structured JSON)
        ── GET /api/retreats/{id} every few seconds until status is "ready"
        ── POST /api/retreats/{id}/days/{n}/build ─▶ background job, three tracks in parallel:
                                                      reading: passage text ─▶ text to speech
                                                      heart:   Claude ─▶ text to speech
                                                      deep:    Claude + web search ─▶ text to speech
        ── GET /api/retreats/{id} until the day is "ready"; tracks carry signed audio URLs
```

Long work runs as background jobs and the browser polls, so no request waits minutes for a model. Each step is saved to Supabase, so a retreat built on a laptop can be played from a phone after signing in with the same email.

**Plan modes.** If the document already has days ("Day 1", "Day 2"...), they are kept in order with their passages. If it is loose material, Claude composes about seven days from it. Passages are copied word for word; the model may only remove page furniture and verse numbers.

## Endpoints

Every error response has the shape `{"error": {"status": 400, "message": "..."}}`, with a message meant to be shown to the user.

When Supabase is configured, the endpoints marked 🔒 need `Authorization: Bearer <Supabase access token>`. A user only ever sees their own retreats; someone else's retreat returns 404.

| Method and path | Parameters | Returns |
| --- | --- | --- |
| `GET /api/health` | none | `{ok, llm, model, tiers, sign_in}` |
| `GET /api/options` | none | Voice tiers and voices, default prompts and guidance, upload limits, the Claude models with live OpenRouter prices, the ElevenLabs balance, and the public Supabase URL and publishable key for the sign-in form |
| `GET /api/me` 🔒 | none | `{id, email}` |
| `GET /api/retreats` 🔒 | none | `{retreats: [{id, title, filename, created_at, status, days, days_built}]}`, newest first |
| `POST /api/retreats` 🔒 | multipart: `file` (.pdf or .docx, up to 15 MB and 40 pages), optional `plan_prompt`, `model`, and `series` (comma-separated ids of earlier retreats this one continues) | **202** with the retreat, `status: "planning"`. 400 for unreadable or empty files, 413 if too large |
| `GET /api/retreats/{id}` 🔒 | none | The retreat: `status` (`planning`, `ready`, `failed`), `source` stats, `images` (with signed `url`), `plan` (title, summary, mode, days with passage, grace, image), and `days` (build state per day, tracks with `status`, `script`, `url`, `sources`) |
| `GET /api/retreats/{id}/script.pdf` 🔒 | query: `day` (omit for the whole retreat), `order` (`lectio` or `simple`), `grace_silence`, `pause` (seconds) | A printable PDF of the script in prayer order, with images, guidance, silences and sources. The whole retreat adds a cover and contents; unbuilt days show their passage and grace |
| `DELETE /api/retreats/{id}` 🔒 | none | `{deleted: id}`; removes the row and its files. 409 while a job is running |
| `POST /api/retreats/{id}/days/{n}/build` 🔒 | JSON: `voices` (a voice id from `/api/options` for each of `guide`, `reading`, `heart`, `deep`; free and premium can be mixed), optional `heart_prompt`, `deep_prompt`, `guide` (spoken guidance text by name; empty skips a clip), `keep_scripts` (re-record with new voices without rewriting), `model` | **202** with the retreat, that day `status: "building"`. 400 for an unknown voice or overlong text, 404 for a missing day, 409 if the plan isn't ready or the day is already building |

Each built day has three `tracks` (reading, heart, deep) and a set of short `guide` clips: the opening, which asks for the day's grace, instructions before each of the four readings and the silence, and a closing. Every clip records its `voice` and its length in `seconds`, so the player can show the total time of the prayer.

**Costs.** Each model call's token usage and web searches are priced with OpenRouter's live rates. The totals are saved as `costs.plan` on the retreat and as `cost` on each built day: model dollars, characters per voice tier, and ElevenLabs dollars. ElevenLabs dollars use `ELEVENLABS_USD_PER_1K_CHARS` (default $0.30), since the real rate depends on the plan. The page also shows an estimate before each build.

Track statuses move `waiting` → `writing` (Claude) → `speaking` (text to speech) → `ready`, or `failed` with an `error`. A job interrupted by a server restart is marked `failed` with a message to try again.

**Custom prompts.** The frontend shows the default prompts and lets the user edit them. The server always appends the fixed part: output format, length limit, and the web-search instruction. That keeps a custom prompt from breaking parsing. Prompts are capped at 8,000 characters.

## Run it locally

Needs Python 3.12. `ffmpeg` is not required.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env        # then fill in keys; all are optional for a first run
.venv/bin/uvicorn app.main:app --reload --port 8000
```

- With no keys at all, set `LLM_MODE=stub`: plans and reflections are placeholders, the free voices still record real audio, and storage is local under `DATA_DIR` with no sign-in.
- Serve the frontend on port 5500 (for example `python3 -m http.server 5500` in the web repo); `config.js` points at `localhost:8000` automatically.
- Interactive API docs: <http://localhost:8000/docs>.
- Tests: `.venv/bin/python -m pytest` (no network or keys needed; Supabase is faked).
- Sample uploads in `samples/` are public domain: World English Bible passages, Rembrandt's *Return of the Prodigal Son* and Tanner's *The Annunciation*. `samples/make_samples.py` rebuilds them.

## Full mode and free mode

| | Full mode (emails on `ALLOWED_EMAILS`) | Free mode (everyone else, and guests) |
| --- | --- | --- |
| Sign-in | Email link | Email link, or **Try it without an account** (anonymous Supabase session, this browser only) |
| Models | Claude on OpenRouter (Opus 5 default; Opus 5.5, Fable 5.1, Sonnet 5, Haiku 4.5) | Jetstream2 open models: Muse Glimmer (default) or Llama 4 Scout |
| Deep dive research | Claude's own web search | Research run by the server with the user's choice of Brave Search, Exa, Tavily, Firecrawl, Linkup (search or deep research) or Brave Answers (whichever have keys), falling back to the next if one is out of credits or failing; the model may cite only returned URLs |
| Voices | Free Microsoft voices and ElevenLabs | Free Microsoft voices |
| Limits | Upload and length caps | Also at most `FREE_MAX_RETREATS` (3) retreats |
| Cost to the site owner | Model and ElevenLabs charges | None (Jetstream is an academic allocation) |

Free mode is on whenever `JETSTREAM_API_KEY` is set. It reaches Jetstream through its Open WebUI proxy at `https://llm.jetstream-cloud.org/api`, which is OpenAI-compatible and reachable from Render; the direct model endpoints only work from inside Jetstream's network. Guests need **Allow anonymous sign-ins** turned on in Supabase (Authentication → Sign In / Providers).

## Supabase setup

1. Create a project. In the SQL editor, run [`sql/001_retreats.sql`](sql/001_retreats.sql), then [`sql/002_llm_calls.sql`](sql/002_llm_calls.sql). The second adds the log of every model call, and a per-user summary view. The first is also shown here:

   ```sql
   create table public.retreats (
     id uuid primary key,
     user_id uuid not null references auth.users on delete cascade,
     title text,
     created_at timestamptz not null default now(),
     updated_at timestamptz not null default now(),
     data jsonb not null
   );
   create index retreats_user_idx on public.retreats (user_id, created_at desc);
   alter table public.retreats enable row level security;
   ```

   Row level security is on with no policies, so the publishable key in the browser can't read the table; only the backend's secret key can.
2. Authentication → URL Configuration: add the frontend URLs as redirect URLs.
3. Copy the project URL, publishable key and secret key into the environment. The private `retreats` storage bucket is created on first start.
4. For guest access, turn on **Allow anonymous sign-ins** under Authentication → Sign In / Providers.

## Deploy on Render

1. Push this repo to GitHub.
2. In Render, choose **New → Blueprint** and pick the repo; `render.yaml` defines a free Python web service. Or create a Web Service by hand with build command `pip install -r requirements.txt` and start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
3. Enter the secret values in the Environment tab.
4. Check `https://<service>.onrender.com/api/health`.

The free instance sleeps after 15 minutes idle and takes about a minute to wake; the frontend says so when the first request fails. Nothing is lost when it sleeps, because retreats and audio live in Supabase.

## Secrets

- Keys live only in environment variables: `.env` locally (listed in `.gitignore`, never committed) and the Render dashboard in production. `.env.example` lists the names with empty values.
- The Supabase **secret** key stays on the server. The browser only gets the **publishable** key, which is designed to be public.
- Audio and images are in a private bucket and reach the browser as signed URLs that expire after 24 hours.
- `ALLOWED_EMAILS` limits who can use the deployed demo, since every build spends API credit.
- CORS only allows the origins in `ALLOWED_ORIGINS`.

## Rights and copyright

Users upload only material they own or have permission to use, and every retreat is private to the account that made it. Nothing is published or shared between users. The demo uses public-domain material only.
