# Ignatius at Home: next-level improvement spec

A spec for one extended Claude Code run of about 4 hours. Everything in it ships; there is no optional tier. It reshapes the web app from a control panel into a place to pray, adds the few backend pieces that make a week-by-week retreat feel like a practice, and leaves the working parts alone.

Repos: the web app is `~/Code/ignatius-hw4-web` (plain HTML, CSS, JS on GitHub Pages); the API is `~/Code/ignatius-hw4-api` (FastAPI on Render). Read `docs/ARCHITECTURE.md` first. Everything below refers to those two repos.

## What's wrong today (the review)

Observed on the live layout with a real seven-day retreat, one day built.

1. **One long page does three jobs.** Upload form, voice and model menus, cost estimates, prompt editors, and the seven day cards sit in one scrolling column. Someone opening the app on a phone at 7 AM to pray Day 3 scrolls past "Model for planning · $1 in / $5 out per million tokens" to get there. It reads as a studio, not a prayer app.
2. **The wrong things are visible.** Every unbuilt day shows a cost estimate line (seven copies of "Estimated cost $0.79: writing about $0.12 with Claude Haiku 4.5, ElevenLabs 2,250 characters ≈ $0.67"), while the scripture passage, the one thing worth reading, is collapsed under a "Passage" toggle.
3. **No sense of where you are.** All days look the same. Nothing says which day is today, which days you've prayed, or what to do next. For a nine-month retreat prayed week by week this is the main thing the app should know.
4. **The returning user is treated like a new one.** After sign-in, the first thing is a small "My retreats" list and then a large upload form. The 95% case (someone coming back to pray) has to find their retreat below the form. The library is a flat list; a series of 36 weeks would be 36 identical lines.
5. **Setup is far from the moment it matters.** Prayer order and silence lengths live in "2. Choose voices and a model," far above the day's "Pray this day" button.
6. **Praying happens in a small bar over the admin page.** The player is a fixed bottom bar with the browser's native audio control. It works (lock screen, continues when the phone locks) but the day's painting isn't shown while praying, there's no progress through the parts, and the page's settings are visible behind it.
7. **Making a retreat is two stages and eight clicks.** Plan first, then build each day separately, each with a text status line and a wait. There's no "upload and go."
8. **Nothing is captured.** No record of what was played, so a missed or half-finished day can't be told apart from a prayed one, and nothing syncs between phone and laptop. The design spec's word-you-paused-on and journal don't exist.
9. **Phone install is generic.** No manifest, no icon, no theme color, so "Add to Home Screen" gives a blank icon and opens in a browser frame. The app is phone-first for praying, so this matters.
10. **Copy is admin-speak.** "1. Upload your material," "2. Choose voices and a model, then build a day," "Build audio," "Rewrite and record," "Re-record with these voices," "Connected." Three different verbs for building; a status line meant for the developer.

What's already good and must be kept: sign-in and guest mode, the plan/build pipeline and its resume behavior, the lectio sequence and the bell-framed silences, the PDF, the costs and logs (for the owner), the research services, the series feature, dark mode, the tests.

## The shape of the change

**One step, not two.** Today a retreat is planned first, then each day is built by hand. That goes away: the user uploads a document, presses **Go**, and the whole retreat is made, planned, written and recorded for every day, while they watch one progress panel. Options are all set up front, in two tabs:

| Tab | What's in it |
| --- | --- |
| **Simple** (default) | The file, "part of a series" with the picker, the rights checkbox, and **Make my retreat**. Nothing else. Everything uses the defaults below. For premium users, one quiet line: "About $6 with Claude Opus 5 and ElevenLabs voices." |
| **Advanced** | Everything that exists today, in this order: models (planning; reflection and deep dive), web research service (free models only), voices for the four sections, prayer order and the two silence lengths, start date, the planning prompt, the reflection prompt with its two presets, the deep-dive prompt, the spoken guidance editor. Changes here are remembered in the browser and become that user's defaults for Simple. A **Reset to defaults** link. |

Defaults for Simple: the server's default model (Claude Opus 5 for premium users, Muse Glimmer for free mode), the default research service, the voices Ava / Andrew / Andrew / Christopher, lectio order, 15 seconds after the grace, 30 seconds of silence, start date today.

Three views in the same static page, chosen by the URL:

| View | URL | For |
| --- | --- | --- |
| **Library** | `/` | Choosing a retreat, or making one. Retreats grouped by series with one "Continue" card; **New retreat** opens the two-tab form. |
| **Retreat** | `/?r=<id>` | Praying. Title, image, a day strip, the selected day's grace and passage in full, one big "Pray this day". While the retreat is being made, this view shows the progress panel instead. |
| **Praying** | `/?r=<id>&pray=<day>` | The prayer itself. Calm full-screen: image, current part, guidance text, progress through the parts, big play/pause, Back / Skip / Stop. |

Backend: creating a retreat takes all the build options and, once planned, builds every day one after another without another request. A start date, "prayed" marks and a journal entry per day are added. Everything else on the server stays as it is.

## The work (all of it ships, about 4 hours)

### 1. One-shot creation (API)

- `POST /api/retreats` accepts, alongside `file`, `model`, `plan_prompt`, `series` and a new `start_date`, all of today's single-day build fields: `voices`, `write_model` (the model for reflection and deep dive), `search_provider`, `heart_prompt`, `deep_prompt`, `guide`. Send them as JSON in one form field `options`. Validate them exactly as `build_day` does now (free-mode rules included) before the upload is accepted, so a bad option fails fast.
- Store them on the retreat as `build_options`. When planning succeeds, the job continues straight into building: set every day to `status: "queued"`, then build them one at a time, in order, using the existing `_build_day` (so the semaphore, heartbeat, logging and costs all apply unchanged). Retreat `status` goes `planning` → `building` → `ready`; add `progress: {done, total, current_day}` to the retreat.
- A failed day doesn't stop the others; it's left `failed` with its reason, and the retreat still ends `ready`, with `progress.failed` listing the days. The retreat view shows a **Try again** on such a day.
- Resume after a restart: the resume logic in `pipeline.get()` already restarts planning and per-day builds. Extend it so a retreat found in `building` with no running task continues from the first day that isn't `ready` or `failed`, using `build_options`. Test it.
- The old `POST /days/{n}/build` stays for Rewrite, Re-record and Try again from the retreat view. Remove nothing from the API that the page still uses.
- Library summary adds `progress`, `start_date`, `days_prayed`, `last_prayed_at`.

### 2. The form: Simple and Advanced tabs (web)

- The upload form is the only place options are set. Two tabs as in the table above; the tab strip is plain buttons with `aria-selected`, no library.
- Simple shows the estimate for premium users only, computed from the defaults or the user's remembered Advanced choices: planning plus seven days of writing plus premium-voice characters, using the same `estimateDay()` math that exists today.
- **Make my retreat** posts once. The page then opens the Retreat view, which shows the progress panel: "Planning… (a few minutes)", then a list of days with one line each ("Day 1 · The Father Runs · recording deep dive"), a check as each completes, and a note that it's fine to close the page and come back. Polling as today.
- When it's ready, the panel becomes the day strip and the first day is selected. If some days failed, they show as failed chips with **Try again**.

### 3. Library view

- Group retreats by series. A series is the chain formed by each retreat's `series` list (already returned by `GET /api/retreats`): the newest retreat that lists others is the head; walk the ids. Retreats in no series are their own group.
- One **Continue** card at the top: the most recently prayed or made retreat, with "Week N · Day D" and a "Pray Day D" button straight into the Praying view. Day D is the first day that's ready and not yet prayed. A retreat still being made shows "Being made · Day 3 of 7" instead.
- Each group: title, weeks count, days prayed / days total across the series, a row of small week chips. Tap a week to open it.
- **New retreat** opens the two-tab form (open by default when the user has no retreats). Delete (two clicks) and Sign out stay. The free-mode banner becomes one quiet line here. Remove the "Connected." line; show server status only when something is wrong.

### 4. Retreat view

- Header: retreat title, "Week N of a series" if applicable, the image once.
- **Day strip:** n tappable chips with state: ready (filled), started (half-filled), prayed (filled with a check), missed (a small dot), today (ring), failed (outline with a mark), being made (spinner). Default selection: a started-but-unfinished day, else a missed day, else today if the start date puts today in this week, else the first ready-and-unprayed day, else day 1.
- **Day panel:** title, source reference, the grace in italics, and the **passage in full, in the serif face, larger than body text**, not collapsed. Then one large **Pray this day** button with the duration, and a quieter row: Prayed ✓ / Mark as prayed, Printable script (PDF), and a "…" menu with Rewrite, Re-record and, when the day failed, Try again. The whole-retreat PDF button lives in the header and appears once at least one day has been written; a day's PDF only once that day is ready. Both rules exist today and must be kept.
- The three track players and their scripts move under a **Listen to a part** toggle, collapsed by default.
- Journal: the word paused on and the note, editable, shown when present.
- A small **gear** opens playback settings only: prayer order and the two silence lengths (they cost nothing and belong with the player). Everything that costs money to change (voices, models, prompts) is reached through the day's "…" menu, which opens the Advanced options pre-filled, with a **Re-record** or **Rewrite** button.

### 5. Praying view

- Opens from "Pray this day". The single `<audio>` element and `buildSequence()` stay exactly as they are; this is a new skin over the existing player. Media Session stays, and its artwork is set to the day's image so the lock screen shows it too.
- **The day's images are the screen.** While the prayer plays, the view shows the images associated with the day (see "Images per day" below), full-bleed, `object-fit: contain` on a dark backdrop so paintings aren't cropped. With more than one image, they cross-fade slowly: one per part of the prayer, or every 45 seconds within a long part. With no images, a quiet backdrop with the day's title and grace in the serif face.
- **Phone layout (under 700 px wide, the main case):** the image fills the screen above a **small player docked at the bottom**, about 120 px tall plus the safe-area inset: the current part's name, a thin progress bar segmented by part, play/pause, Back, Skip, and the time left. Tapping the player expands it into a sheet with Stop, the guidance or silence text, and the list of parts; tapping the image or swiping down collapses it. During the silence, the guidance line ("Stay with one word or phrase…") shows over the bottom of the image.
- **Wide layout:** image on the left two-thirds, the part list and guidance text on the right, controls below.
- When the sequence ends: a short **After praying** screen over the image: "What word or phrase stayed with you?" (one input) and an optional note, **Save** and **Done**.
- Remember position (see "Listening progress"): reopening a day that was stopped partway offers "Continue Day 3 at For the heart?"
- Request a screen wake lock while praying if the browser supports it; release on stop.

### 5a. Listening progress (API and web)

The app remembers what has been played, per day, so a missed or interrupted day is obvious later.

- `POST /api/retreats/{id}/days/{n}/progress` with `{step, part, seconds, completed_parts: [...], finished: bool}`. The player sends it when a part starts, when a part ends, every 15 seconds while playing, and on Stop (use `navigator.sendBeacon` on page hide). The server stores `days[n].listening = {parts_played: [...], last_step, last_part, seconds_in_part, started_at, updated_at, finished_at}` and writes it at most once every 10 seconds per day (merge in memory, save on the heartbeat) so the database isn't hammered.
- A day counts as **prayed** automatically when the sequence finishes (`finished: true`); the journal is optional on top. "Mark as prayed" stays for someone who prayed from the PDF.
- **Day states** used everywhere (day strip, library, Continue card): *not started*, *started* (some parts played; show which: "Reading and For the heart played"), *prayed*, plus *missed*, meaning the start date puts the day in the past and it's not prayed.
- **The day panel** shows the listening state in words: "You listened to the first reading and For the heart on Tuesday, then stopped" with **Continue** (resumes at `last_step`) and **Start over**.
- **The Continue card** in the Library picks the earliest day that's missed or started-but-unfinished, not just the next day in order, and says so: "You missed Day 3 · Continue where you left off" or "Day 3 · not started yet". A retreat's chips in the Library show prayed / started / missed at a glance.
- Listening progress is per retreat and belongs to the retreat's owner; it syncs across devices because it's on the server (start on the phone, see it on the laptop).
- The PDF marks prayed days with the date.
- Tests: progress merges and throttles, `finished` sets prayed, missed is computed from the start date, the summary returns per-day states.

### 5b. Images per day (API)

- Today each planned day has one `image_index`. Add `image_indexes: [int]` to the plan schema (all images the planner judges belong with the day, in order), keeping `image_index` as the first of them so the PDF and older retreats keep working. Update the planning prompt's fixed part to ask for this. A retreat planned before the change uses `[image_index]` when it's not -1.
- The Retreat view's day panel shows the day's first image; the Praying view uses all of them.

### 6. Backend: start date, prayed marks, journal (API)

- `start_date` (ISO date) on create, default the date the page sends; `PATCH /api/retreats/{id}` with `{start_date}` to change it (from the gear).
- `POST /api/retreats/{id}/days/{n}/prayed` with `{prayed: true|false, word?: string, note?: string}`: sets `days[n].prayed_at` and `days[n].journal = {word, note, at}`. Word ≤ 100 characters, note ≤ 2,000. Returns the retreat.
- The PDF includes the journal entry for a day when present (after the closing).
- Tests for each, plus the one-shot create: options validated up front, days queued after planning, sequential build, a failed day not stopping the rest, resume from `building`.

### 7. PWA basics (web)

- `manifest.webmanifest`: name "Ignatius at Home", short name "Ignatius", `display: standalone`, `start_url: "./"`, theme and background colors from the CSS tokens, icons.
- Icons: `tools/make_icons.py` draws a simple mark (a plain cross or a single candle flame in the accent color on the parchment background) at 192, 512 and 180 (apple-touch-icon). No text in the icon. Commit the PNGs.
- `<link rel="manifest">`, `<link rel="apple-touch-icon">`, `<meta name="theme-color">` for light and dark, `apple-mobile-web-app-capable`. No service worker in this tier.

### 8. Copy and noise

- Buttons: "Make my retreat", "Pray this day", "Rewrite", "Re-record", "Try again". No "Build audio" anywhere.
- Headings: "New retreat", "Simple" / "Advanced", "Listen to a part", "After praying". No numbered headings.
- Costs: for premium users only, one line under Make my retreat (the estimate) and one line in the retreat header after it's made ("This retreat cost $5.80"). The full breakdown (tokens, searches, per day, ElevenLabs balance) goes in a **Costs** `<details>` at the bottom of the retreat view.

### 9. Finishing touches

- **Today awareness in the Library:** "Today is Day 4 of Week 12" computed from start dates; the Continue card uses it.
- **Notifications:** when a retreat finishes being made and the tab is open, a small toast; if `Notification.permission` is granted, a system notification. Ask for permission only from a button on the progress panel ("Tell me when it's ready"), never on load.
- **Passage typography:** a slightly larger serif, wider line height, and a drop-cap-free but generous first line; verse breaks preserved from the source where they exist (the extractor keeps line breaks; render `\n` inside the passage as line breaks).
- **Series in the PDF:** the whole-retreat PDF cover says "Week N of the series …" and lists the earlier weeks' titles.
- **Empty and loading states:** a skeleton card while planning (title placeholder, seven grey chips) instead of the status sentence; a friendly empty Library ("No retreats yet. Upload a handout or a few passages to make your first week.").

### 10. Added during the run (all required)

These came in while the run was under way and are part of the definition of done.

1. **Parts that know about each other.** A day's parts are written in listening order: the reflection first; the deep dive sees the reflection and builds on it; the spoken guidance is then tailored to both (e.g. the line before the second reading points back to what the reflection invited the listener to notice). Default or customized guidance stays the basis; tailoring can be switched off in Advanced; any failure falls back to the plain text. Re-recording keeps tailored guidance.
2. **Background for every model.** Every prompt to every model (planning, reflection, deep dive, tailoring, search questions, conversation) starts with a briefing on the Spiritual Exercises, retreats in daily life (Annotation 19), lectio divina (Guigo II; Verbum Domini 87) and how a day in this app is prayed.
3. **About page.** What the Exercises are, what a retreat is, what lectio divina is, how a day here is prayed, with checked links to IgnatianSpirituality.com, CCEL, Verbum Domini, Saint John's Abbey, Creighton and Wikipedia; a plain statement that the writing and voices are AI.
4. **Architecture document featured** at the top of both READMEs (done on `main`).
5. **Talk it over: a live spoken conversation** about the retreat. Never called "spiritual direction" in the app; the companion is modeled on how spiritual directors accompany someone: mostly questions and gentle probing, little advice, helping the person notice where God is at work and engage with the retreat; says it's an AI if asked; crisis guidance (988). Context: the retreat's days, which were listened to or prayed, the words and notes saved after praying.
   - Providers, chosen in Advanced with a voice: **OpenAI GPT-Live** (`gpt-live-1`, WebRTC, the SDP offer relayed by the server so the key stays there, server-side hangup at the limit) and **xAI Grok voice** (WebSocket with an ephemeral token minted by the server, PCM16 audio in the browser). OpenRouter can't carry live voice, so these need `OPENAI_API_KEY` and `XAI_API_KEY`.
   - Free users: 60 seconds a day (enforced by the server's daily allowance and, for OpenAI, a server hangup); premium: up to 30 minutes a call.
   - **Memory:** every conversation's transcript is saved to Supabase (the user's storage folder) and the companion is given the recent ones, with older ones condensed into a memory summary, so it remembers past conversations. The person can clear this memory. Transcripts are also in `llm_calls`.
6. **About me ("user info.md").** Upload a text, Markdown, Word or PDF file about yourself, or type it; it's saved as `user info.md` and informs every model call made for you: planning, writing, tailoring and the conversation. Over 6,000 characters, a model condenses it and the page warns that a summary is being used. A separate box for what you want from the conversation companion.
7. **Robustness found by testing:** free-voice requests are retried with backoff (a real build lost two guidance clips to dropped connections).
8. **Keep building until everything above and in sections 1–9 is done**, testing continuously with free builds (Jetstream models, Microsoft voices) on the Mac mini, not only at the end.

## Out of scope for this run

Payments, a native app, offline audio, a shared or public gallery, changing the pipeline or prompts, changing the research services, redesigning the PDF, replacing the Microsoft or ElevenLabs voices.

## How to work

- Branch `redesign` in both repos; merge to `main` only at the end, since both `main`s auto-deploy. Test against the local API with `LLM_MODE=stub` and a `DATA_DIR` that already has a built retreat (copy `/tmp/ignatius-dev5` if it still exists, or build one day with the free voices; it takes about a minute).
- Keep `app.js` as one file, but split it into clearly labelled sections: router, api, library, retreat, praying, setup, player. No build step, no framework. Keep the `?v=N` cache-busting on the script and stylesheet links and bump it on every push.
- Check every view in Chrome at 1280 wide and at 390 wide (use the device toolbar; the extension's resize doesn't take effect). Take screenshots before and after for the write-up.
- Keep dark mode working: every new color is a token on `:root` with a dark override.
- Run the API tests after each backend change (`.venv/bin/python -m pytest -q -p no:warnings`); add tests for every new endpoint. Update `docs/ARCHITECTURE.md` (views, new endpoints, new fields in the retreat document) and the two READMEs.
- Don't touch the pipeline, prompts, research, series, or logging code except where an endpoint above needs a field.
- When done: merge, push, wait for Render and Pages, load the live site, open the Praying view once, and report what was and wasn't finished, with the screenshots.

## Definition of done

A new user uploads a handout, presses **Make my retreat**, and comes back twenty minutes later to a week ready to pray, without touching another button. A returning user opens the site on an iPhone and, in two taps (Continue → Pray), is praying the right day, including one they missed or stopped partway, with the day's painting filling the screen and a small player at the bottom; the day is marked prayed when the audio finishes, and the laptop shows the same. Everything adjustable is in the Advanced tab, and nothing there is required.
