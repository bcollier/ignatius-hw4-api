# Prompt log

HW4 asks for a log of the AI tools and models used and the key prompts. This project was built with **Claude Code** running **Claude Opus 5.5**, in one working session on September 25, 2026. The app itself calls **Claude Opus 5** (through OpenRouter) at run time.

## Tools and models

| Where | Tool or model | Used for |
| --- | --- | --- |
| Building the app | Claude Code with Claude Opus 5.5 | Planning, writing all code and tests, running the local server, testing the page in Chrome, writing these docs |
| Inside the app | Claude Opus 5 via OpenRouter's Anthropic-compatible API | Planning retreats (structured JSON), writing reflections, deep dives with web search |
| Inside the app | Microsoft neural voices via `edge-tts`; ElevenLabs | Text to speech |

## Key prompts, in order

These are the requests that shaped the build, lightly condensed.

1. "Use it as a submission for homework 4 … server side code on render.com. Walk me through step by step setting up the backend server with a web front end where we take a PDF exercise / reading and build three audio files: (1) the straight reading of the exercise, (2) the heart-focused reflection and (3) the deep dive on theology, history, and hermeneutics of the passage."
2. "I have a lot of credits on OpenRouter, but I like the built-in web tools for search and analysis with Claude. Can I use my OpenRouter key with all the modern tooling for Claude?" This led to using the Anthropic SDK against OpenRouter's Anthropic-compatible endpoint, with Anthropic's web search tool.
3. "Explain the copyright thing." This led to keeping the Bridges handouts and copyrighted translations out of the public repo and the demo.
4. "Change the design: the app is more like 'build a practice'. The user uploads a PDF or Word doc that they have rights to … if they upload a PDF with seven days it does exactly what is described; if they upload a PDF with seven random verses and some images it will build a seven-day retreat out of the source material." This became the two plan modes, `follows_source` and `composed`.
5. "Can we do a free version with free Microsoft TTS or a low-cost version as well?" This became the free and premium voice tiers.
6. "Every day would be its own MP3? Is MP3 the right format or M4A?" The answer: MP3 for the web now, M4A for a future iPhone app.
7. "On the web page the prompts should be customizable, and the images should not be described with words but rather shown on the page." This became the editable prompts with a fixed server-side suffix, and the image gallery.
8. "Can you build in sound? How will you handle the pause and reflect?" This became the prayer player: a lectio sequence with a synthesized bell, quiet and a bell, played as real audio so it keeps going when a phone is locked.
9. "The app should have a login so users can upload the materials on their laptop and then play the audio from a phone web browser." With "Supabase", this became Supabase Auth with email links, a `retreats` table and a private storage bucket.

## Prompts the app sends to Claude

The defaults are in `app/prompts.py` and are shown, editable, on the web page:

- **Planning:** decide whether the material already has days or needs a composed arc; copy passages word for word; choose a grace, a focus and an image for each day. The output is constrained to a JSON schema.
- **For the heart:** two presets, a spiritual companion or the voice of Jesus in Ignatian imaginative prayer, with a house style written for listening (no lists, no parentheses, no dashes, no invented facts).
- **Deep dive:** setting, original-language words where well documented, how the church has read the text, and real interpretive questions. Web search is used to check claims, and sources are returned separately for the screen.

## What was checked by hand

- A real run on the public-domain sample produced a sensible seven-day plan and matched the Rembrandt to the prodigal-son days.
- A deep dive that cited Thayer's lexicon, Ambrose and the Hermitage catalogue.
- The player sequence, skip and back, tested in Chrome.
- 17 automated tests: extraction, chunking, API flow, errors, storage and sign-in checks against a fake Supabase.
