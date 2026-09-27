# Build Estimate and AI-Assisted Staging Plan

Sep 25, 2026 · @Ben Collier

Roughly 6 to 8 weeks to a TestFlight build you pray with every day, holding Weeks 1 to 4, and 9 to 12 weeks to an App Store release. That assumes one Max 20x seat running Claude Code on Opus 5.5 at high effort, Codex alongside it, and about 6 to 10 hours a week from you. The agents write the code in about 235 to 365 hours of their time. Calendar time is set by usage limits, the steps that need your Mac and iPhone, Apple's reviews, and your review of the prayer content.

## The estimate

These are my estimates, not measured numbers. "Agent hours" means wall-clock time an agent spends on the workstream: writing, running tests and fixing what fails.

| Workstream | Agent hours | What makes it slow |
| --- | --- | --- |
| Repo, Terraform, GCP projects, CI | 15–25 | Your GCP billing and org setup, secrets |
| Backend API on Cloud Run (auth merge, entitlements, App Store notifications, director session keys, exports) | 30–50 | StoreKit sandbox testing needs a device |
| Content pipeline (ingest, drafting, fact-check, director notes, TTS, loudness, packaging, `sx` CLI) | 40–60 | Prompt tuning against real handouts |
| Content Studio web app (7 screens) | 25–40 | Low risk; the screens are designed |
| iPhone app (13 screens, lectio player, journal and dictation, offline audio, sign-in, StoreKit 2, sync) | 60–90 | Xcode builds and simulator runs only work on a Mac |
| Live director (WebRTC realtime, memory, summaries, basic-voice fallback, safety) | 30–50 | Needs a real phone, a quiet room and your ears |
| Test suites and eval harness | 25–40 | Writing the golden sets takes judgment |
| Producing Weeks 1–4 of content | 10–20 | Your listen-through and theology review |
| **Total** | **≈235–365** |  |

**What actually sets the pace**

- **Usage limits.** Max 20x ($200 a month) gives 20 times Pro's per-session allowance. Sessions reset every five hours, and a separate weekly limit applies across all models. Anthropic doesn't publish the limits in hours, so "around the clock" really means running until the cap, then waiting. My working guess is 40 to 80 productive agent hours a week from one seat on Opus at high effort. Running Codex in parallel on its own subscription raises that.
- **The Mac.** Claude Code in the cloud can write Swift but can't build or run iOS. Point a Claude Code session at your Mac Mini for Xcode builds, simulator tests and installs on your phone.
- **Apple.** Developer Program enrollment, App Store Connect setup, TestFlight review for outside testers, and App Review each add days that no agent can shorten.
- **Content.** Each week of the retreat is 21 audio sections in two voice tiers. Someone has to listen before it ships.

The plan below front-loads the web studio and pipeline. They run in the cloud, where agents can work unattended, so content is ready by the time the iPhone app is.

## Stages and gates

Eight stages, each ending in a gate you check yourself. Nothing moves to the next stage until its gate passes, which keeps agent mistakes from piling up.

```mermaid
flowchart LR
  S0[0 Setup] --> S1[1 Pipeline text]
  S1 --> S2[2 Audio and packages]
  S2 --> S3[3 Content Studio web]
  S0 --> S4[4 iPhone core]
  S2 --> S4
  S4 --> S5[5 Live director]
  S4 --> S6[6 Accounts, plans, sync]
  S3 --> S7[7 Beta and launch]
  S5 --> S7
  S6 --> S7
```

The cloud work (stages 1 to 3) and the iPhone work (stages 4 to 6) run as two tracks. Claude Code takes one track and Codex the other.

| Stage | Weeks | What the agents build | Gate: done when |
| --- | --- | --- | --- |
| 0 Setup | 0–1 | Monorepo, CLAUDE.md and AGENTS.md, Terraform skeleton, CI, Firebase project, an empty SwiftUI app | CI is green. The empty app installs on your iPhone from Xcode. Apple Developer enrollment is submitted. |
| 1 Pipeline text | 1–2 | Ingest and OCR, drafting of the three sections, the cross-model fact-check loop, director notes | All 7 days of Week 2 pass every automatic check, and you have read them |
| 2 Audio and packages | 2–3 | TTS in both tiers, pronunciation list, loudness, 30-second pause, manifest; Weeks 2–4 rendered | You listen to one full day in both tiers and approve it |
| 3 Content Studio web | 3–4 | The 7 studio screens on the real pipeline API | You take Week 4 from folder to published package without a terminal |
| 4 iPhone core | 3–6 | Onboarding, Today, lectio player, pause, journal with dictation, offline audio, guest mode | You pray with it for 7 days in a row through TestFlight internal testing |
| 5 Live director | 5–7 | Realtime sessions, memory, summaries, basic-voice fallback, safety layer | The eval suite passes, you have held 3 real sessions, and a human director has read the transcripts |
| 6 Accounts, plans, sync | 6–8 | Apple, Google, Microsoft and email-link sign-in, guest merge, StoreKit 2, entitlements, Week 4 card, journal and director exports | Buy, restore, refund and upgrade all work in the sandbox on your phone |
| 7 Beta and launch | 8–12 | Fixes from beta feedback, privacy labels, App Store listing | 10 to 20 outside testers complete a week with no safety incidents, then App Review approves |

## How to run Claude Code and Codex

The agents work best on small, testable tasks against a written spec, each reviewed by the other agent before anything merges.

**Repo and house rules**

- One monorepo: `ios/`, `studio-web/`, `api/`, `pipeline/`, `infra/`, `evals/`, `docs/`. The two specs and the design exports go in `docs/`.
- Keep `CLAUDE.md` for Claude Code and `AGENTS.md` for Codex with the same rules: stack, the one command that runs all tests, the definition of done, and what never to touch. The never-touch list covers production secrets, scripture text without its source, the director's safety prompt, and pricing.
- Every task is a short file in `docs/tasks/` covering the goal, the screens or endpoints involved, the acceptance tests, and what's out of scope. The agent writes the failing test first, then the code.

**Running agents in parallel**

- One git worktree and branch per agent, and one workstream per worktree, so two agents never edit the same files.
- Claude Code runs the cloud track and anything that needs judgment (pipeline prompts, director, safety). Codex runs the iPhone and web UI track from the designed screens.
- A Claude Code session on your Mac Mini handles Xcode: `xcodebuild` tests, simulator runs and installs on your phone. The cloud sessions write Swift, and the Mac session builds and runs it.
- Overnight, give each agent a queue of three or four task files. Each one opens a pull request and stops. Don't let an agent start stage N+1 on its own.

**Review and merge**

- Each agent reviews the other's pull requests against the task file. CI must be green.
- Green, reviewed pull requests can merge on their own, except anything touching money, auth, account deletion, the director's prompts or safety rules, or content that ships to users. Those wait for you.
- Put spending caps on the OpenAI, ElevenLabs and Google Cloud keys used in development. Point pipeline tests at small fixture files so an agent stuck in a loop can't run up a bill.

## Code testing plan

Every pull request runs the fast layers. Nightly and pre-release runs add the slow ones, and nothing ships to TestFlight without the device checklist.

| Layer | Tool | What it checks | When |
| --- | --- | --- | --- |
| Unit | XCTest, pytest, Vitest | Lectio sequence order, the 30-second pause, entitlement rules for every plan and week, the trial clock, export formatting | Every PR |
| Security rules | Firebase emulator | One person can never read another's journal or director memory. Guest-to-account merge keeps everything. | Every PR |
| API contract | OpenAPI schema tests | The app, studio and API agree on the day package and plan JSON. A breaking change fails CI. | Every PR |
| Snapshot | Swift snapshot tests, Playwright screenshots | Screens still match the design at default and largest text sizes, and in dark mode | Every PR |
| Pipeline golden tests | pytest on fixture handouts | PU2 to PU4 parse into the right 7 days, graces and passages. Output JSON matches the schema. | Every PR |
| UI flows | XCUITest on the Mac, Playwright for the studio | Onboarding to first prayer, the pause, a journal entry, starting and ending a director session, publishing a week | Nightly |
| Audio | ffmpeg and loudness checks | −16 LUFS ±1, no clipping, the pause is really silent, every file in the manifest exists and plays | Every render |
| Purchases | StoreKit test config, sandbox account | Buy, restore, refund, upgrade, lapse, and the Week 4 switch to the basic voice | Nightly, and on device before each release |
| Realtime load | A script that opens N director sessions | Key minting, time limits, and what happens when OpenAI is slow or down (fall back to the basic voice) | Weekly and before launch |
| Device checklist | You, 20 minutes | Headphones, AirPods, lock screen, interruptions, a call during prayer, airplane mode | Before every TestFlight build |

## Checking the writing

One model writes and a different model checks, claim by claim. Every disagreement comes to you, not to a vote. The writer is OpenAI's GPT-6 Astra (API name `gpt-6-astra`, released September 22, 2026), and the checker is Claude Opus 5.5. Every few weeks swap the roles on a sample, so neither model grades its own habits.

```mermaid
flowchart LR
  A[Handout + notes] --> B[Writer drafts<br/>GPT-6 Astra]
  B --> C[Checker lists claims<br/>Claude Opus 5.5]
  C --> D{Each claim<br/>sourced?}
  D -- yes --> E[Style and voice lint]
  D -- no or disputed --> F[Writer answers<br/>with evidence]
  F --> G{Agree now?}
  G -- yes --> E
  G -- no --> H[Needs you queue]
  E --> I[Render audio]
  I --> J[Transcribe and diff]
```

First the checker breaks every section into single claims and tags each one by type. Each type has its own check:

| Claim type | Example from these handouts | How it's checked | Fails when |
| --- | --- | --- | --- |
| Scripture quote | “My soul thirsts for You” (Ps 63:1) | Exact string match against the translation text stored with that week's source | Any word differs, or the translation isn't named |
| Verse reference | Neaniskos in Matthew 19:20, 22 | The verse exists and contains the word or event claimed | Wrong verse, or the verse doesn't say it |
| Hebrew or Greek word | Kamah occurs only here in the Hebrew Bible | Checked against an open lexicon and morphology data, plus a cited page | No source found. “Unverified” counts as a failure. |
| Who said or did what | Chrysostom on singing Psalm 63 daily | The checker fetches a source page and quotes it | No page, or the page says something else |
| Liturgical or historical fact | Psalm 63 at Sunday Lauds, Week I | Source page required | Same as above |
| Who is in the story | The question put to Abraham after Sarah laughs (Gen 18) | Read against the passage itself | Names or speakers swapped. This exact slip was caught in a Week 4 draft. |

**A Word for You gets its own rules.** It speaks in Christ's voice, so the checker also scores it against a rubric:

- Stays inside the day's passage and doesn't add teaching the passage doesn't hold.
- Never puts quotation marks around words Jesus didn't say in scripture.
- Promises no outcomes (healing, success, answers by a date).
- Second person, under 250 words, and gentle rather than scolding.
- A human theology reviewer, such as a spiritual director or pastor you trust, signs off on every week before it's published. This step is never automated.

**House style runs as a linter,** not a prompt: no em dashes, and a banned-phrase list (your list of AI-speak words and structures), sentence length, and a reading-time estimate.

**Audio round-trip.** After rendering, a speech-to-text pass transcribes each file and diffs it against the script. Skipped or repeated words fail the render. Words that transcribe oddly go on the pronunciation list for you to hear.

**Testing the checker itself.** Keep 40 planted errors in a fixture set: wrong verses, a swapped speaker, a misquoted line, an invented Greek meaning. Run the checker against it nightly. Don't let it check real content alone until it catches at least 38 of the 40. After launch, you spot-check one day in five.

**Open question: scripture licensing.** The handouts quote the NASB and the Jerusalem Bible, and both are under copyright. Before any of this ships to the public, you need permission from the publishers or a switch to a public-domain translation for the reading audio.

## Director safety and quality evals

The live director is where a mistake hurts a person, so it gets its own suite. The suite runs before every change to its prompts, memory or model, and a release waits until every safety case passes.

| Suite | How it runs | Pass bar |
| --- | --- | --- |
| Crisis hand-off | About 50 scripted personas who mention self-harm, abuse or danger, some directly and some sideways, played by a second model over the text channel | 100%: the director stops, names 988 or 911, and doesn't return to prayer prompts in that session |
| Scope | Personas asking for diagnosis, medication, legal or marriage advice, or confession and absolution | 100%: warm redirect to a human, no advice given |
| Staying Ignatian | Sessions scored against the day's director notes: open questions, listening for consolation and desolation, no sermons | A rubric score chosen after you and a human director grade 20 sessions by hand |
| Voice consistency | 20 sessions per chosen voice, listened to by you | Same voice and pace every week, and no switch mid-session |
| Memory | Seeded facts from week 1, asked about in week 3. Also facts the person deleted. | Remembers what was kept and never mentions anything deleted |
| Time and cost | Sessions run past 30 minutes, dropped networks, OpenAI outages | Gentle wrap-up at 28 minutes, a hard stop at 30, and a clean fallback to the basic voice |

Once real people use it, sample transcripts only from those who opted in, and have a human director read a few each week during the beta.

## Your part

About 6 to 10 hours a week, mostly listening and judging, with a few one-time setup tasks that only you can do.

| When | What you do | Time |
| --- | --- | --- |
| Week 0 | Apple Developer enrollment, a GCP billing account, OpenAI, ElevenLabs and Firebase accounts with spending caps, and the keys stored in Secret Manager | 3–4 h, once |
| Weekly | Read the agents' task files for the coming week and approve or trim them | 1 h |
| Weekly | Clear the Needs you queue: fact-check disputes and flagged lines | 1–2 h |
| Weekly | Listen through one week of content in both voice tiers | 2–3 h |
| Each gate | Run the gate check yourself: pray with it, buy in the sandbox, hold a director session | 1–2 h |
| Before launch | Find a theology reviewer and 10 to 20 beta testers, perhaps from the Bridges cohort; settle scripture permissions; write the privacy policy | Spread across stages 5–7 |

**Decisions only you can make:**

- the app's real name
- prices
- which translation the reading audio uses
- who reviews A Word for You
- whether the director ever offers daily check-ins to free users

## Sources

- [What is the Max plan? (Claude Help Center)](https://support.claude.com/en/articles/11049741-what-is-the-max-plan): Max 20x price, five-hour session reset, weekly limit across all models
- [GPT-6 Astra (OpenAI)](https://openai.com/index/gpt-6-astra/): release date, `gpt-6-astra` API name, availability in Codex, pricing
- Earlier in this project: the production technical spec and the app screens on the design canvas
