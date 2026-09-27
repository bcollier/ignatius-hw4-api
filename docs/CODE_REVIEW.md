# Code review guidance

How to review a change to Ignatius at Home (this API and the [web app](https://github.com/bcollier/ignatius-hw4-web)), for a person or an agent. The principles are the ones the [code cleanup](CODE_CLEANUP.md) applied; the checklists come from real bugs in this project, each noted where it happened, so every item is here for a reason.

## 1. How a change should arrive

- **Start from a spec.** A large change starts from a written spec (the [original spec](original-spec/), [IMPROVEMENTS.md](IMPROVEMENTS.md), [VISUAL_REDESIGN.md](VISUAL_REDESIGN.md)), and the review checks the change against it: what it asked for, what's missing, what was added that it didn't ask for.
- **One request, one change.** Each prompt is logged in [PROMPT_LOG.md](../PROMPT_LOG.md) with what was done, in both repositories. A review checks that the log says what the code does.
- **Small enough to read.** A change a reviewer can't hold in their head gets split.

## 2. Readability (the Clean Code principles)

- **Small functions that do one thing**, reading like a table of contents of well-named steps at one level of detail. The model is `_build_day`: once 155 lines, now 11, running a small `_DayBuild` class whose methods are the steps in the order a day is made.
- **Names that say what they mean**: `_check_can_rebuild`, `wireDropZone`, `readyDayActions`. No `data2`, `tmp`, `handle`.
- **No magic numbers.** Limits are named constants with a comment when the reason isn't obvious (`MAX_TRIES`, `CODE_SECONDS`, `STALE_SECONDS`, `POLL_MS`).
- **One place for one idea.** If the same logic appears twice, it becomes a helper (`fileUrl`, `postJson`, `_use_notes`).
- **Comments explain why**, in plain words, not what the next line does. A comment that no longer matches the code is a bug.
- **No dead code**: unused functions, variables, imports and CSS rules go.
- **Match the surrounding code**: its naming, its comment density, its idiom (plain scripts and `el()` in the web app; routes modules, `HTTPException` with a message for people, and dataclasses in the API).

## 3. Correctness and safety (API)

- **Tests.** `.venv/bin/python -m pytest -q` passes, and new behavior has a test (the suite uses `LLM_MODE=stub`, so tests never call a model). Edge cases: a free account, a guest, a missing file, a wrong code, a service that's down.
- **Errors people can read.** Anything a person might see is an `HTTPException`, `LLMError` or `TTSError` with a message written for them ("That Google Doc isn't shared publicly…"); anything else is logged with its traceback and shown as a short general message.
- **A background job always ends.** Every job (planning, a day build, your own Examen, choosing passages for an idea) must end as ready or failed, even on an unexpected exception, and must survive a restart (a heartbeat and a resume, or a clear "please try again"). *Bug fixed: a retreat from an idea stayed "planning" forever after an unexpected error.*
- **Every model and voice call is logged** to `llm_calls` with its purpose, tokens and cost, including failures and replies cut off at the output limit. *Bug fixed: a cut-off plan was logged as "ok."*
- **Free and premium rules hold on the server**, not just in the page: free accounts use the free models and voices, guests can't hand off a session, and paid calls are limited.
- **Secrets** stay in Render's environment and `.env`, never in code, logs, responses or the repository. Browser-side code gets only the publishable Supabase key.
- **Scripture is never invented.** Passages are copied from the source (or fetched from the World English Bible); models choose references, never write verses. Copyrighted handouts and translations stay out of the public repositories.
- **Words.** Nothing is called spiritual direction; the interface says "Make my retreat," not "build."

## 4. The web app (and iPhones in particular)

Most of the app's bugs were on iPhones. Check each change in WebKit (Safari's engine) at iPhone size (393 × 852), in light and dark mode, not only in a desktop browser.

- **Every page still starts.** A missing element must never stop the app: start-up wiring runs per area and catches its own errors, and the start-up guard in `index.html` shows and reports any error. Load every view (`?`, `?me`, `?practice`, `?new`, `?about`, a retreat, the prayer screen) and check the console is clean. *Bug fixed: a `<form>` placed inside another `<form>` is silently dropped by the browser, the start-up code crashed looking for it, and the app was blank for everyone.*
- **Nothing wider than the phone.** A flex or grid child with text that shouldn't wrap needs `min-width: 0`, or its content widens the whole page. *Bug fixed: a long part name on the prayer screen pushed the close button off the screen.*
- **Text boxes at 16px or more**, or iPhone browsers zoom in when one is tapped.
- **Audio that keeps going with the screen off.** On an iPhone a page's timers stop when the screen is off unless audio is playing, and a page that isn't playing can't start a sound. Long sessions play one continuous audio element (silences as a near-silent loop), time countdowns by the clock, and set lock-screen controls (Media Session). Volume goes through Web Audio, since iPhones ignore an audio element's `volume`. *Bugs fixed: guided exercises stalled with the screen off; the music's volume did nothing on an iPhone.*
- **Home Screen apps are separate.** An app added to the Home Screen has its own storage and never receives the email's sign-in link; sign-in there uses the eight-digit code.
- **Cache.** Bump `?v=` on every script and stylesheet in `index.html` with any web change, so browsers and the auto-reload pick it up. GitHub Pages lets a page be cached for ten minutes, and the old page must be able to update itself.
- **The server may be asleep.** The first request after a quiet spell can take a minute; the page shows the waiting screen rather than nothing.
- **Accessibility.** Buttons are buttons with labels, motion stops under `prefers-reduced-motion`, color comes from tokens with a dark-mode value, and text on a painting-colored background stays readable.
- **Mermaid diagrams render on GitHub**: no semicolons inside node labels.

## 5. Before merging

1. The tests pass (API) and every view loads without errors in WebKit at iPhone size (web).
2. The change matches its spec and its prompt log entry.
3. Anything that costs money (a model, a voice, a search) was run only as much as the change needed, and the cost is noted.
4. The README and docs still say what the code does (the default model, the services, the diagrams).
5. Both repositories are pushed, the site's version is live, and the API's new routes answer on Render.
