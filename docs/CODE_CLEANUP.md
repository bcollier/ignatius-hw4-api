# Code cleanup: a readability pass

A complete readability pass over both repositories: the API (`ignatius-hw4-api`, Python) and the web app (`ignatius-hw4-web`, HTML, CSS and JavaScript). The goal was code a person can read top to bottom: small functions with names that say what they do, constants instead of unexplained numbers, and comments that explain *why*.

This was a pure refactor. No route, response, stored format, prompt or piece of UI text changed. Section 5 describes how that was checked.

## 1. Principles applied

These follow Robert C. Martin's *Clean Code*, adapted to a small app with no build step:

- **Small functions that do one thing.** A long function became a short one that reads like a table of contents, calling well-named steps. Each step does one thing at one level of detail.
- **Names that say what they mean.** For example `_DayBuild._write_deep`, `readyDayActions`, `costRetreatCard`, `_check_can_rebuild`, `useExample`, `wireDropZone`.
- **No magic numbers.** Limits and tuning values are named constants with a short comment when the reason isn't obvious, such as `PREVIEW_CHARS`, `MAX_TURNS`, `CHARS_PER_WORD`, `WORD_BUDGET_MARGIN`, `PROGRESS_REPORT_MS`, `UNKNOWN_STEP_SECONDS` and `ELEVENLABS_FORMAT`.
- **One place for one idea.** Repeated code became a single helper:
  - "Click again to confirm" is now `confirmTwice`.
  - The log label (email or "guest") is now `User.log_email`.
  - The model price list is now `pricing.model_menu`.
  - Reading an upload within the size limit is now `routes/uploads.read_upload`.
- **Files organized by responsibility.** The 730-line `main.py` became a thin app module plus `checks.py`, `access.py` and one routes module per area. The 2,391-line `app.js` became thirteen scripts, one per view.
- **Comments that explain why, in the app's own plain voice.** The pass removed comments that no longer matched the code (for example, "few retreats" on free accounts, a cap that no longer exists). It added comments where a choice isn't obvious: why check order matters, why a failed clip is retried, why prices appear only in Advanced.
- **No dead code.** Unused functions, variables, imports and CSS rules left behind by earlier changes were removed.

## 2. API (`ignatius-hw4-api`)

### `app/main.py` → `main.py` + `checks.py` + `access.py` + `routes/`

`main.py` went from 730 lines to 62. It now only creates the app, sets CORS and the error format, prepares storage at start-up, and includes the routers.

| New file | What it holds |
| --- | --- |
| `app/checks.py` | `check_prompt`, `check_date`, `check_title` (new; was inline in the rename route), `check_series`, `check_model`, `BuildRequest`, and `resolve_build`. `resolve_build` was split into `_check_voices`, `_check_guide` and `_check_search_provider`, and keeps the original order of checks, so when several things are wrong the same error is reported first. |
| `app/access.py` | `my_retreat`, `readable_retreat`, `save_retreat` and `view_of`. `day_state` and `now_iso` were `_day_state` and `_now`. `PERSONAL_DAY_FIELDS` names the fields a visitor may change in an example. |
| `app/routes/meta.py` | `/`, `/api/health`, `/api/options`, `/api/me` |
| `app/routes/retreats.py` | The library, making a retreat, reading one, research notes, the printable PDF, rename or re-date, delete. |
| `app/routes/days.py` | Prayed, progress, rebuild, retry. |
| `app/routes/build_log.py` | The build log. `log_row` (was `main._log_row`) is split into `_hide_private_notes`, `_preview` and `_prompt_text`. |
| `app/routes/cost_report.py` | `/api/costs` |
| `app/routes/conversation.py` | Talk it over (`/api/talk/*`) |
| `app/routes/about_me.py` | `/api/profile*`, with `_text_of` for uploaded files. |
| `app/routes/example_documents.py` | `/api/examples*` |
| `app/routes/local_files.py` | `/api/files/*`, for local development only. |
| `app/routes/uploads.py` | `read_upload` and `too_big`: reading an upload without ever holding more than the limit in memory. |

Handlers that did several jobs were split:

- **`create_retreat`** (52 → 32 lines): `_build_options` and `_source_bytes` (the example or the upload).
- **`list_retreats`**: `_example_summaries` and `_add_cover_urls`.
- **`research`**: `_research_for_day` and `_saved_research`.
- **`script`**: `_days_to_print`, `_series_titles`, `_load_day_image` and `_pdf_filename`.
- **`build_day`**: `_check_can_rebuild`.
- **`cost_report`**: `_prices`.

Other changes in this area:

- **Limits named.** `MAX_WORD_CHARS`, `MAX_NOTE_CHARS`, `MAX_PART_NAME_CHARS`, `MAX_PARTS_PER_REPORT`, `MAX_LAST_PART_CHARS`, `PDF_SLUG_CHARS`, `MAX_TITLE_CHARS`, and in the build log `PREVIEW_CHARS`, `LIVE_PAGE_ROWS`, `FULL_PAGE_ROWS` and `ROW_FIELDS`.
- **The price list moved.** `model_options` moved from `main.py` to `pricing.model_menu`, which `meta` and `cost_report` share.

### `app/auth.py`
- Added `User.log_email` in place of seven copies of `user.email or ("guest" if user.anonymous else None)`.
- Fixed the out-of-date comment on `User.full`.

### `app/pipeline.py`

- **`_build_day` (155 lines) is now an 11-line function that runs a small `_DayBuild` class.** Its steps are methods, in the order a day is made:

  | Method | What it does |
  | --- | --- |
  | `run` | Sets up the job, runs the steps below, gathers the recordings. |
  | `_record_reading` | Records the reading (it needs no writing). |
  | `_write_heart` | Writes the reflection for the heart. |
  | `_write_deep`, `_write_new_deep` | Writes the deep dive, knowing the reflection. |
  | `_write_guidance` | Tailors the spoken guidance to both. |
  | `_ready`, `_words_for`, `_start_recording`, `_record` | Recording. |
  | `_finish`, `_mark_failed` | Marks failed parts, the day's status and cost, and progress. |

  These used to be closures sharing local variables. They now share clearly named attributes.
- **New module-level helpers:**
  - `_image_descriptions`
  - `_research_label` (it replaces a hard-to-read nested conditional expression)
  - `_word_timings`
- **New constants:** `CHARS_PER_WORD` and `WORD_BUDGET_MARGIN` replace the unexplained `cap / 6 * 0.85`.
- **`_plan`** (48 → 30 lines): `_log_planning_start` (the build log's first steps, with the long f-string broken into named pieces) and `_apply_plan`.
- **`create_retreat`**: `_store_images` and `_source_summary`.
- **`_resume`**: `_give_up`, `_resume_day_builds` and `_kept_scripts`. `_kept_scripts` was duplicated in `_build_all`.

### `app/llm.py`
- **`_call`** (58 → 20 lines): `_stream_turns`, `_check_stop_reason` and `_log_call`. An `_Exchange` dataclass carries what came back across paused turns. It keeps a partial answer for the log if a later turn fails, as before. `MAX_TURNS` names the 4.
- **`write_deep`** (55 → 11 lines) now only chooses a path: `_deep_claude` or `_deep_jetstream`. Shared pieces:
  - `_deep_system`, which picks the right research note: head start, results only, search on, or search off.
  - `_deep_user`.
  - `_research_record`, the research page's record, once for both paths.
  - `_with_search_fallback`: try with web search, and write without it if the provider refuses the tool.
  - `_server_research`: the Jetstream model's queries, run through the search services.
  - `_passage_ref`, which replaces the unexplained `context.splitlines()[2]`.

### `app/talk.py`
- **`context`** (67 → 15 lines): `_about_them`, `_past_conversations`, `_the_retreat`, `_day_lines` and `_day_status`.
- **Named constants:** `CONTEXT_MAX_CHARS`, `TRANSCRIPT_TAIL_CHARS`, `PASSAGE_CHARS`, `HEART_OPENING_CHARS` and `NOTE_CHARS`. The recent-conversation count now uses the existing `HISTORY_KEEP`.

### `app/costs.py`
- **`report`** (51 → 19 lines): `_group_rows`, `_retreat_costs`, `_add_voices`, `_by_vendor` and `_add_to_totals`.

### `app/script_pdf.py`
- **`day_html`** (72 → 56 lines): `_day_header`, `_sources` and `_after_praying`. `IMAGE_WIDTH` named.
- The rest deliberately mirrors the player's order line by line (see section 4).

### `app/tts.py`
- **`_elevenlabs`** (43 → 17 lines):
  - `_eleven_post` does one piece with retries.
  - `_is_busy` tells too-many-at-once from out-of-credits.
  - `_check_eleven_response` explains each failure.
  - `_eleven_audio` decodes the audio and timings.
  - `_backoff` sets the wait between retries.
- **Named constants:** `ELEVENLABS_FORMAT`, `PREVIOUS_TEXT_CHARS` and `ELEVENLABS_TIMEOUT`.

### `app/search.py`
- **`_run_provider`** (41 → 11 lines): `_run_query` (one query; never raises) and `_log_query`.
- The table of search functions is built once, as `SERVICES`, instead of on every call.
- **Named constants:** `ERROR_CHARS` and `WAIT_GRACE_SECONDS`.

### `app/jetstream.py`
- **`_complete`** (47 → 22 lines): `_user_content` (text plus images), `_post`, `_check_status` and `_read_reply`.
- **Named constants:** `REQUEST_TIMEOUT` and `LOGGED_ERROR_CHARS`.
- A comment explains the "images not accepted" retry.

### `app/examples.py`
- `file_for` returns "not found" for an unknown file type instead of raising a `KeyError`. Before, `/api/examples/x.zip` answered 500; now it answers 404. This is the only intentional behavior change, and it fixes a bug.

### Dead code and imports
- Removed `demos.day_state`, which was never called.
- Removed unused imports in `tests/test_build_log.py`, `tests/test_costs.py`, `tests/test_one_shot.py`, `tests/test_talk_profile.py` and `tools/build_demo.py`.
- In `samples/examples/make_examples.py`, removed an unused variable and two f-strings with nothing to fill in.
- Checked with `ruff` (the pyflakes rules). It now reports nothing.

### Tests
- **New: `tests/test_elevenlabs.py`**, run against a fake ElevenLabs server. It covers recording, with timings kept and a busy reply retried, and each explained failure (401, 402, 429, 500). This path had no tests before the refactor. The new tests pass against both the old and new code.
- `tests/test_build_log.py` imports `log_row` from its new home.

## 3. Web app (`ignatius-hw4-web`)

### `app.js` (2,391 lines) → `js/` (13 files)

The split follows the old file's own section markers. Every non-blank line was accounted for: one section heading was renamed, and nothing else was lost or duplicated.

The scripts are still plain scripts with no bundler. They share one global scope and `index.html` loads them in order, each with the `?v=` cache-busting number:

| File | What it holds |
| --- | --- |
| `js/core.js` | Constants, DOM and date helpers, the API client, shared state, `confirmTwice`. Its header describes all the files. |
| `js/settings.js` | The Advanced tab: models, voices and voice sets, research, prayer settings, prompts, guidance. It also holds the estimate, which was under a misleading "costs (premium only)" heading. |
| `js/router.js` | The URL chooses the view; sign-in and guests. |
| `js/library.js` | The library. |
| `js/new-retreat.js` | New retreat. |
| `js/retreat.js` | A retreat. `markPrayed`, rebuild, retry and prayer settings moved here from the research section, where they had ended up; the printable PDF is here too. |
| `js/build-log.js` | The build log. |
| `js/research.js` | Research notes. |
| `js/pray.js` | The prayer player. |
| `js/about-me.js` | About me. |
| `js/talk.js` | Talk it over. The conversation voice picker moved here from the settings code, since it now lives on the Talk page. |
| `js/costs.js` | The Costs page. |
| `js/start.js` | Wiring and start-up, loaded last. |

### Functions split

| Before | After |
| --- | --- |
| `fillSettings` (95 lines) | 11 lines calling `fillModelSelects`, `fillVoiceSelects`, `fillPlaybackFields`, `fillPromptFields`, `fillGuideFields` and `wireResetButtons`. `modelOptionLabel` is a small helper. `PLAYBACK_DEFAULTS` and `SETTING_KEYS` are named. |
| `wireForms` (68 lines) | 7 lines calling `wireSignIn`, `wireNewRetreat` (with `wireDropZone`), `wireRetreatPage` and `wireAboutMeAndTalk`. |
| `renderDay` (65 lines) | 18 lines. `dayHeading`, `listeningLine`, `dayActions` (which calls `readyDayActions` or `failedDayActions`) and `journalBox`. |
| `renderLibrary` (56 lines) | 7 lines. `renderContinueCard` (with `continueCard` and `beingMadeCard`) and `renderGroups`. |
| `openCosts` (56 lines) | 20 lines. `costTable`, `costDetail`, `costTotalsCard`, `costRetreatCard`, `costOtherCard` and `costPricesCard`. |
| `openTalk` (47 lines) | 28 lines. `talkContextLine`, `wireVoiceChange` and `resetTalkControls`. |
| `fillTalkSettings` (46 lines, with a nested closure) | 17 lines. `fillTalkVoices`, `showSampleButton` and `playTalkSample` are top level. |
| `wirePlayer` (45 lines) | 25 lines. `fillCurrentSegment`, `wirePlayerButtons` and `wireLockScreenControls`. `PROGRESS_REPORT_MS` named. |
| `makeRetreat` (42 lines) | 24 lines. `newRetreatForm` builds and checks the request, throwing the message to show. `chosenSourceFile` handles a file or pasted text, and `resetNewForm` clears the form. `DEFAULT_MAX_UPLOAD_MB` named. |
| `startPrayer` (39 lines) | 18 lines. `resetStage` and `buildProgressBar`. `UNKNOWN_STEP_SECONDS` named. |
| `renderRetreat` (36 lines) | 19 lines. `renderSeriesLine`, `renderHeaderButtons` and `renderFootLinks`. |
| `renderResearchDay` (38 lines) | 14 lines. `researchFound`, `citedSources`, `noResearchReason`, `urlIn` and `serviceName`. |
| `loadExampleDocs` | `exampleDocCard` and `useExample`. |
| `playStep` | `updateLockScreen`, plus a comment on why a short quiet keeps showing the part just heard. |

- **`confirmTwice`** replaces the two copies of the "click again to confirm" pattern (deleting a retreat, forgetting conversations).
- **`buildSequence`** has a header comment laying out the prayer's order, blocks and steps.

### Dead code
- Removed the unused `bal` variable in the settings code, left over from the removed ElevenLabs balance line.
- Removed the CSS rules for the removed cost panel: `.costs summary` and `.cost-info`.
- A check of every top-level name in `js/` against the scripts and `index.html` finds no other unused names.

## 4. Left as it is, on purpose

These are still a little long. Splitting them would make them harder to follow:

- **`script_pdf.day_html`** (56 lines) and **`buildSequence`** (53) are the prayer's order written out step by step, and they mirror each other. Reading them in one piece is the point.
- **`onGrokEvent`** (43) is a `switch` over the Grok voice events, one short case each.
- **`llm_log.record`** (39) and **`storage.summary`** (34) mostly build one dictionary, one field per line.
- **`extract._extract_pdf`** (39), **`llm.plan_retreat`** (36), **`llm.tailor_guide`** (32), **`auth.current_user`** (32), **`search._brave_answers`** (32), **`pipeline.create_retreat`** (35) and **`routes.retreats.create_retreat`** (32) each read top to bottom as one short procedure with a clear docstring.
- **Prompt texts in `prompts.py`** are long strings on purpose: they're the words the models read.
- **`style.css` and `index.html`** are organized by view and design token already. Only dead rules were removed.

## 5. The largest functions, before and after

| Function | Before (lines) | After (lines) |
| --- | --- | --- |
| `pipeline._build_day` | 155 | 11 (plus the `_DayBuild` steps: at most 22 each) |
| `fillSettings` | 95 | 11 |
| `script_pdf.day_html` | 72 | 56 |
| `wireForms` | 68 | 7 |
| `talk.context` | 67 | 15 |
| `renderDay` | 65 | 18 |
| `llm._call` | 58 | 20 |
| `renderLibrary` | 56 | 7 |
| `openCosts` | 56 | 20 |
| `llm.write_deep` | 55 | 11 |
| `main.create_retreat` → `routes.retreats.create_retreat` | 52 | 32 |
| `costs.report` | 51 | 19 |
| `pipeline._plan` | 48 | 30 |
| `jetstream._complete` | 47 | 22 |
| `openTalk` | 47 | 28 |
| `fillTalkSettings` | 46 | 17 |
| `wirePlayer` | 45 | 25 |
| `tts._elevenlabs` | 43 | 17 |
| `makeRetreat` | 42 | 24 |
| `search._run_provider` | 41 | 11 |

| File | Before (lines) | After |
| --- | --- | --- |
| `app/main.py` | 730 | 62, with the routes in `app/routes/` (none over 250 lines) |
| `app.js` | 2,391 | 13 files in `js/`, the largest 493 lines (`pray.js`) |

## 6. How it was checked

- **API tests:** 85 before, 90 after (85 plus the five new ElevenLabs tests). All pass after every step.
- **Same routes, same answers.**
  - The generated OpenAPI document (every route, method, parameter, status code and docstring) was compared between `main` and `cleanup`, and is identical.
  - The companion's context text (`talk.context`) was compared on sample data with conversation history, prayed and started days, and notes. The output is byte-for-byte identical.
  - The new ElevenLabs tests pass against both the old and the new code.
- **Web:** `node --check` passes on every script. A headless Chrome smoke test on the local servers opens each view and checks what it shows. The results are the same as before the refactor, with no console errors:

  | View | What was checked |
  | --- | --- |
  | Library | Its cards |
  | A retreat | The day panel, research notes and build-log links |
  | Prayer screen | The highlighted word moves with the audio |
  | Research notes | 3 days, 60 results |
  | Costs | Every table |
  | Talk it over | The voice picker with 28 Grok voices, genders and samples |
  | New retreat | Examples, paste box, and the Advanced estimate at the Opus 5.5 default |
  | Build log | All rows, ending with "end of log" |
  | About me, About | The form; the voice sample players |

- **End to end:** on a separate local server with placeholder models, the check exercised the new-retreat path:
  - "Make my retreat" with nothing chosen shows its message.
  - Pasting text switches the upload box to the pasted text.
  - "Use this example", with "watch" on, goes to the new retreat, and the terminal starts showing the build steps live.
- **Local server quirk:** the local development file server (Python's `http.server`) sometimes drops a connection on a fresh browser's very first page load. That can leave one of the thirteen scripts unloaded. It isn't a problem in the app, and repeated loads came through cleanly. GitHub Pages serves the files over HTTP/2 and doesn't do this.
