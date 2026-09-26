# Ignatius at Home: next-level improvement spec

A spec for one extended Claude Code run of about 3 to 4 hours. It reshapes the web app from a control panel into a place to pray, adds the few backend pieces that make a week-by-week retreat feel like a practice, and leaves the working parts alone.

Repos: the web app is `~/Code/ignatius-hw4-web` (plain HTML, CSS, JS on GitHub Pages); the API is `~/Code/ignatius-hw4-api` (FastAPI on Render). Read `docs/ARCHITECTURE.md` first. Everything below refers to those two repos.

## What's wrong today (the review)

Observed on the live layout with a real seven-day retreat, one day built.

1. **One long page does three jobs.** Upload form, voice and model menus, cost estimates, prompt editors, and the seven day cards sit in one scrolling column. Someone opening the app on a phone at 7 AM to pray Day 3 scrolls past "Model for planning · $1 in / $5 out per million tokens" to get there. It reads as a studio, not a prayer app.
2. **The wrong things are visible.** Every unbuilt day shows a cost estimate line (seven copies of "Estimated cost $0.79: writing about $0.12 with Claude Haiku 4.5, ElevenLabs 2,250 characters ≈ $0.67"), while the scripture passage, the one thing worth reading, is collapsed under a "Passage" toggle.
3. **No sense of where you are.** All days look the same. Nothing says which day is today, which days you've prayed, or what to do next. For a nine-month retreat prayed week by week this is the main thing the app should know.
4. **The returning user is treated like a new one.** After sign-in, the first thing is a small "My retreats" list and then a large upload form. The 95% case (someone coming back to pray) has to find their retreat below the form. The library is a flat list; a series of 36 weeks would be 36 identical lines.
5. **Setup is far from the moment it matters.** Prayer order and silence lengths live in "2. Choose voices and a model," far above the day's "Pray this day" button.
6. **Praying happens in a small bar.** The prayer player is a fixed bottom bar with the browser's native audio control. It works (lock screen, continues when the phone locks) but it doesn't feel like prayer: no calm full-screen view, no big play/pause, no progress through the parts, the page's admin content still visible behind it.
7. **Building a week is seven clicks and seven waits.** Each day is built separately, with a text status line. There's no "prepare the whole week."
8. **Nothing is captured.** The design spec calls for the word you paused on and a short journal; neither exists. A prayed day leaves no trace.
9. **Phone install is generic.** No manifest, no icon, no theme color, so "Add to Home Screen" gives a blank icon and opens in a browser frame. The app is phone-first for praying, so this matters.
10. **Copy is admin-speak.** "1. Upload your material," "2. Choose voices and a model, then build a day," "Build audio," "Rewrite and record," "Re-record with these voices," "Connected." Three different verbs for building; a status line meant for the developer.

What's already good and must be kept: sign-in and guest mode, the plan/build pipeline and its resume behavior, the lectio sequence and the bell-framed silences, the PDF, the costs and logs (for the owner), the research services, the series feature, dark mode, the tests.

## The shape of the change

Three views in the same static page, chosen by the URL, with the prayer view as the default for anyone who has a retreat:

| View | URL | For |
| --- | --- | --- |
| **Library** | `/` | Choosing a retreat. Retreats grouped by series, with one prominent "Continue" card. "New retreat" opens the upload form. |
| **Retreat** | `/?r=<id>` | Praying. Title, image, a day strip, the selected day's grace and passage in full, one big "Pray this day" button. Setup lives in one collapsed panel. |
| **Praying** | `/?r=<id>&pray=<day>` | The prayer itself. Calm full-screen: image, current part, guidance text, progress through the parts, big play/pause, Back / Skip / Stop. |

Small backend additions: a start date per retreat, "prayed" marks and a journal entry per day, and a "build the whole week" job. Everything else on the server stays as it is.

## Tier 1: must ship (about 2.5 hours)

### 1. Routing and views (web)

- Keep it a single `index.html` and `app.js`. Add a tiny router: read `r` and `pray` from the query string, show one of three `<section>`s, and update the URL with `history.pushState` so Back works. Re-render on `popstate`.
- Move the upload form into the Library view, collapsed behind a **New retreat** button (or shown open when the user has no retreats).
- Everything currently in "2. Choose voices and a model, then build a day" (model menus, voices, research service, prayer order, silences, prompt editors, spoken-guidance editor, ElevenLabs balance, cost estimates) moves into one `<details>` called **Setup** at the bottom of the Retreat view. Defaults are good; most users never open it.
- Remove the "Connected." line. Show server status only when something is wrong (can't reach, waking up), as now.
- Keep the free-mode banner but make it one quiet line inside the Library view.

### 2. Library view

- Group retreats by series. A series is the chain formed by each retreat's `series` list (already returned by `GET /api/retreats`): the newest retreat that lists others is the head; walk the ids. Retreats in no series are their own group.
- Show one **Continue** card at the top: the most recently prayed or built retreat, with "Week N · Day D" and a "Pray Day D" button that opens the Praying view directly. Day D is the first day that's built and not yet prayed, else the first unbuilt day (then the button says "Prepare Day D").
- Each group: title, weeks count, days prayed / days total across the series, a row of small week chips. Tap a week to open it.
- Delete stays (two clicks), Sign out stays.

### 3. Retreat view

- Header: retreat title, "Week N of a series" line if applicable, the image (once, not repeated per day; each day's own image, if different, shows in the day panel).
- **Day strip:** seven (or n) tappable chips, "1" to "n", with state: not built (outline), ready (filled), prayed (filled with a check), today (ring). Selecting a chip shows that day below. Default selection: today if the retreat has a start date and today falls in the week, else the first built-and-unprayed day, else day 1.
- **Day panel:** title, source reference, the grace in italics, and the **passage in full, in the serif face, larger than body text**, not collapsed. Below it:
  - The whole-retreat PDF button (in the header) appears only once at least one day has been written; a day's own PDF button only once that day is ready. Both rules exist today and must be kept.
  - If ready: one large **Pray this day** button with the duration ("About 14 minutes"), then a quieter row: Prayed ✓ / Mark as prayed, Printable script (PDF), and a "…" menu with Rewrite, Re-record, Rebuild.
  - If not built: **Prepare this day** (with the estimate only for premium users, and only here, in one small line). If a build is running: a small list with one line per section and a check as each finishes (reading, for the heart, deep dive, guidance), instead of the joined text.
  - The three track players and their scripts move under a **Listen to a part** toggle, collapsed by default.
  - Journal (see backend): the word you paused on and a short note, editable, shown here when present.
- A **Prepare the whole week** button in the header (see backend). While it runs, the day chips show a spinner on the day being built.

### 4. Praying view

- Opens from "Pray this day". Full-viewport, calm: the day's image faded behind or above, "Day 3 · The Father Runs", the current part's name, the guidance or pause text large in the serif face, a thin progress bar with one segment per part (the pause as a longer segment), big play/pause, then Back · Skip · Stop, and the time left.
- The single `<audio>` element and `buildSequence()` stay exactly as they are; this is a new skin over the existing player. Media Session stays.
- When the sequence ends: a short **After praying** screen: "What word or phrase stayed with you?" (one input) and an optional note, a **Save** button that also marks the day prayed, and **Done**. Skipping is fine; Stop anywhere marks nothing.
- Remember position: save `{retreat, day, stepIndex}` to localStorage every step; on reopening that retreat, offer "Resume Day 3 at For the heart?" once.
- The screen should not sleep while praying on iOS Safari: request a wake lock (`navigator.wakeLock.request('screen')`) if available, release on stop. Audio continues either way.

### 5. Backend: start date, prayed marks, journal, whole-week build (API)

- `POST /api/retreats` accepts optional `start_date` (ISO date, default: the day of upload, in the user's local date as sent by the page). Stored on the retreat. `PATCH /api/retreats/{id}` accepts `{start_date}` so it can be changed from Setup.
- `POST /api/retreats/{id}/days/{n}/prayed` with `{prayed: true|false, word?: string, note?: string}`: sets `days[n].prayed_at` (ISO time, or null) and `days[n].journal = {word, note, at}`. Word ≤ 100 characters, note ≤ 2,000. Returns the retreat.
- `POST /api/retreats/{id}/build` with `{days: [1,2,...], ...the same fields as a single-day build}`: queues the days and builds them one after another (not in parallel; the existing semaphore and heartbeat cover it). Each day is a normal build so the resume-after-restart logic already applies. Skips days that are already ready unless `rebuild: true`. Returns the retreat with each queued day in `status: "queued"` (add that status; the page treats it like building with a different label).
- Library summary (`GET /api/retreats`) adds `start_date`, `days_prayed`, and `last_prayed_at`.
- The PDF includes the journal entry for a day when present (after the closing).
- Tests for each: start date default and patch, prayed/journal round trip and limits, whole-week build queues and skips ready days, summary fields.

### 6. PWA basics (web)

- `manifest.webmanifest`: name "Ignatius at Home", short name "Ignatius", `display: standalone`, `start_url: "./"`, theme and background colors from the CSS tokens, icons.
- Icons: write `tools/make_icons.py` that draws a simple mark (a plain cross or a single candle flame in the accent color on the parchment background) with Pillow or PyMuPDF, at 192, 512 and 180 (apple-touch-icon). No text in the icon. Commit the PNGs.
- `<link rel="manifest">`, `<link rel="apple-touch-icon">`, `<meta name="theme-color">` for light and dark, `apple-mobile-web-app-capable`.
- No service worker in this tier (audio comes from signed URLs that expire; caching them is a separate design).

### 7. Copy and noise

- Buttons: "Prepare this day" (was Build audio), "Prepare the whole week", "Rewrite" (was Rewrite and record), "Re-record" (was Re-record with these voices), "Pray this day" stays, "Plan my retreat" stays.
- Section headings: "New retreat", "Setup", "Listen to a part", "After praying". Drop the numbered "1." and "2." headings.
- Cost text: show on a day only for premium users and only as one short line under Prepare ("About $0.80 with Claude Opus 5 and ElevenLabs"). The full breakdown (tokens, searches, last build) moves into Setup under a "Costs" `<details>`, along with the ElevenLabs balance and "spent so far".
- Free-mode banner: one sentence, in the Library view.

## Tier 2: if time remains (about 1 hour)

- **Today awareness in the Library:** "Today is Day 4 of Week 12" computed from start dates; the Continue card uses it.
- **Setup: start date control** with the day strip updating live.
- **Build-status notifications:** if the tab is open and a whole-week build finishes, show a small toast; if `Notification.permission` is granted, a system notification. Ask for permission only from a button ("Tell me when the week is ready"), never on load.
- **Passage typography:** a slightly larger serif, wider line height, and a drop-cap-free but generous first line; verse breaks preserved from the source where they exist (the extractor keeps line breaks; render `\n` inside the passage as line breaks).
- **Series in the PDF:** the whole-retreat PDF cover says "Week N of the series …" and lists the earlier weeks' titles.
- **Empty and loading states:** a skeleton card while planning (title placeholder, seven grey chips) instead of the status sentence; a friendly empty Library ("No retreats yet. Upload a handout or a few passages to make your first week.").

## Out of scope for this run

Accounts and payments, a native app, offline audio, a shared or public gallery, changing the pipeline or prompts, changing the research services, redesigning the PDF, replacing the Microsoft or ElevenLabs voices.

## How to work

- Branch `redesign` in both repos; merge to `main` only at the end, since both `main`s auto-deploy. Test against the local API with `LLM_MODE=stub` and a `DATA_DIR` that already has a built retreat (copy `/tmp/ignatius-dev5` if it still exists, or build one day with the free voices; it takes about a minute).
- Keep `app.js` as one file, but split it into clearly labelled sections: router, api, library, retreat, praying, setup, player. No build step, no framework. Keep the `?v=N` cache-busting on the script and stylesheet links and bump it on every push.
- Check every view in Chrome at 1280 wide and at 390 wide (use the device toolbar; the extension's resize doesn't take effect). Take screenshots before and after for the write-up.
- Keep dark mode working: every new color is a token on `:root` with a dark override.
- Run the API tests after each backend change (`.venv/bin/python -m pytest -q -p no:warnings`); add tests for every new endpoint. Update `docs/ARCHITECTURE.md` (views, new endpoints, new fields in the retreat document) and the two READMEs.
- Don't touch the pipeline, prompts, research, series, or logging code except where an endpoint above needs a field.
- When done: merge, push, wait for Render and Pages, load the live site, open the Praying view once, and report what was and wasn't finished, with the screenshots.

## Definition of done

A returning user opens the site on a phone and, in two taps (Continue → Pray), is in a calm full-screen prayer for today's day, with the passage visible before they start, the day marked prayed when they finish, and a word saved. A new user uploads a handout, taps "Prepare the whole week" once, and comes back to a week ready to pray. The admin surfaces still exist, one panel down.
