# Fitness for this app: which model, and at what cost

The judge-based evals ask other models for their opinion. This analysis asks what can be checked in the text itself: whether scripture is quoted exactly, whether the deep dive stays inside the research it was given, whether the model follows the app's instructions and writes for the ear, whether the companion behaves as it should, and what each model costs and how long it takes. The cheap judges' score is kept as one axis among nine, not the verdict.

Run: `.venv/bin/python -m evals.fitness --run full` (no paid calls, a few seconds). It reads `evals/runs/full/` and writes the web app's `evals/fitness.json`.

## Method

Every measure is computed per piece (a heart reflection or deep dive for one passage) or per conversation, then averaged per model, with a 95% bootstrap interval (2,000 resamples of pieces) where the value is an average of pieces.

| Dimension | How it's measured | Scored 0–1 as |
| --- | --- | --- |
| **Judged quality** | Mean overall score from the three cheap judges (Gemini 3.8 Flash, Muse, Llama 4 Scout), every scale, lower-is-better scales turned around | (score − 1) / 6 |
| **Scripture fidelity** | Every quotation of 4+ words (quotes paired in order, so a short quote can't swallow the text after it) is checked against the World English Bible passage: *verbatim* (in the passage exactly), *close* (similarity ≥ 0.93, a tiny slip), *misquote* (6+ words, similarity 0.75–0.93: clearly this passage, altered), *other* (not from this passage: another text, a tradition, or the writer's own words in quotation marks; not scored) | (verbatim + close) / all quotations of the passage |
| **Grounding** (deep dives) | Share of cited URLs that are among the research results the model was given; and a claim-support proxy: sentences with a date, a number of 3–4 digits, BC/AD, or an original-language word (Greek or Hebrew script, a transliteration with diacritics, or "the Greek word X") count as checkable claims, and a claim is *supported* if at least half its key tokens (with its proper names) appear in the research text | mean of the two shares |
| **Follows instructions** | Length within 15% of the target words (full credit inside ±15%, falling off beyond); no markdown, lists or emoji; reply tags present (`<script>`, and `<sources>` for deep dives); for the heart, the day's grace echoed (share of its key words present, full credit at half) | mean of the parts |
| **Written for the ear** | Mean sentence length (full credit up to 18 words, none at 35) and Flesch reading ease (full credit 60–90) | mean of the two |
| **Companion behaviour** | Share of replies with at most one question; share of replies of speakable length (≤ 120 words); 988 or other real help named in the at-risk conversation; no claim to be a spiritual director | mean of the parts |
| **Reliability** | Pieces produced and non-empty, of the 12 expected | share |
| **Speed** | Median seconds per piece | log scale: 5 s = 1, 120 s = 0 |
| **Cost** | Projected text cost of a 7-day retreat (below) | 1 / (1 + dollars) |

**Projected cost per retreat** (text only; voices cost the same whichever model writes): each day is the heart + the deep dive + tailoring the spoken guidance (estimated at 0.6 × a heart reflection: it reads the day and both scripts and writes short lines), plus, for Claude models, $0.05 a day for Claude's own web searches in the app (up to 5 at $0.01; the eval turned them off so every model saw the same research). Planning is added once, estimated as one deep dive. Voices: Microsoft is free; ElevenLabs is about $0.30 per 1,000 characters, the same for every model.

**Composite fitness** is a weighted mean of the nine scores, renormalised over the dimensions a model has (Gemma has no conversations yet). Five weightings are reported, and a sensitivity analysis draws 4,000 random weightings (uniform over all possible weightings, a Dirichlet(1)) to see how often each model comes out on top.

| Weighting | quality | fidelity | grounding | instructions | ear | companion | reliability | speed | cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| balanced | .25 | .15 | .10 | .10 | .10 | .10 | .05 | .05 | .10 |
| fidelity first | .15 | .35 | .20 | .10 | .05 | .05 | .05 | .025 | .025 |
| cost first | .20 | .10 | .05 | .05 | .05 | .05 | .05 | .05 | .40 |
| speaking first | .25 | .10 | 0 | .15 | .25 | .15 | 0 | .05 | .05 |
| companion first | .20 | 0 | 0 | 0 | .10 | .35 | .05 | .20 | .10 |

## Results (run "full", 27–28 September 2026)

Six passages × two tracks per model, and three scripted conversations. Gemma 4 31B (local) had written 11 of its 12 pieces and none of its conversations when this was run; its generation was still going.

| Model | quality | fidelity | grounding | instructions | ear | companion | reliability | speed | cost | **fitness (balanced)** |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Opus 5.5 | .80 | .98 | .92 | 1.00 | .98 | 1.00 | 1.00 | .51 | .28 | **.84** |
| OpenAI GPT-6 Sol | .78 | 1.00 | 1.00 | .94 | 1.00 | 1.00 | 1.00 | .62 | .70 | **.89** |
| Muse Glimmer (free) | .78 | 1.00 | .96 | .99 | .97 | 1.00 | 1.00 | .35 | 1.00 | **.90** |
| Gemma 4 31B (local) | .80 | 1.00 | .88 | .98 | .99 | – | .92 | .00 | 1.00 | **.87** |

### What the measures found

- **Scripture is quoted faithfully by everyone.** GPT-6 Sol quoted the passage 44 times, all word for word; Opus 44 times, 42 word for word, one tiny slip and one flagged misquote ("Blessed are you who are poor", which is Luke's wording quoted in a Matthew deep dive: a comparison, not an error, so the flag needs a human eye). Muse and Gemma quote much less (9 and 5 times) but exactly.
- **Grounding: Gemma cites pages it wasn't given.** 7 of Gemma's 28 cited URLs aren't among the research results (biblehub, bartleby, blogs): probably from its own memory, unverifiable, and possibly invented. Opus, GPT-6 and Muse cited only pages they were given. Of the checkable claims (dates, original-language words), Opus had 9 of 10 supported by the research, Muse 16 of 17, Gemma 6 of 6, GPT-6 made only one such claim (supported): GPT-6's deep dives make few specific, checkable claims at all.
- **Length: GPT-6 writes short.** It averages 77% of the target length (only 25% of pieces within ±15%). The app sizes each script to fit the voice's limit, so a short script is a shorter prayer, not an error; but it's the one instruction GPT-6 doesn't follow. Gemma also runs short (83%). Opus (104%) and Muse (103%) hit the target.
- **All four write well for the ear.** Mean sentences of 12–15 words and Flesch reading ease of 80–86 (easy to follow aloud). Opus and GPT-6 are the most speakable.
- **The companions behave.** Opus, GPT-6 and Muse ask at most one question per reply, name 988 in the at-risk conversation, and never claim to be a spiritual director. Opus's replies are the longest (47 words on average), GPT-6's the shortest (25).
- **Speed.** Median seconds per piece: GPT-6 17 (companion reply 3.9 s), Opus 24 (6.8 s), Muse 40 (18.5 s), Gemma 514 (8.5 minutes). Gemma's time is for a 31B model on an M2 Pro with 32 GB; it depends entirely on the hardware, and a deep dive's 18,000-token prompt at 50 tokens a second of prompt reading is most of it.
- **Cost per 7-day retreat (text):** Opus **$2.64** ($0.09 a reflection, $0.16 a deep dive, $0.23 a companion conversation); GPT-6 Sol **$0.43** ($0.014, $0.033, $0.035); Muse and Gemma **$0**.
- **Judged quality** barely separates them (0.78–0.80, overlapping intervals: Opus 5.82 on the 1–7 scale, 95% interval 5.56–6.08; GPT-6 5.66; Muse 5.66; Gemma 5.79). The judges don't tell these models apart reliably (see the Evals page).

### Value

- **Quality per dollar** (quality score / dollars per retreat): GPT-6 Sol 1.83, Opus 0.31; Muse and Gemma are free.
- **Pareto frontier, cost against judged quality:** Gemma (free) and Opus (highest quality): every other model is beaten by one of them on both. Gemma's place rests on 11 pieces, the cheap judges and an 8-minute wait.
- **Pareto frontier, cost against fitness:** Muse alone: free and the highest balanced fitness.
- **Who wins under which weighting:** balanced and cost-first → Muse; fidelity-first, speaking-first and companion-first → GPT-6 Sol. Under 4,000 random weightings GPT-6 Sol wins 51%, Muse 48%, Opus 1%, Gemma under 1%. Opus never wins unless quality alone is weighted far above everything else, because it costs six times GPT-6 and its measurable advantages are small.

## Recommendations

| Use | Recommendation | Why |
| --- | --- | --- |
| **Free tier default** | **Muse Glimmer** (as now) | Free, exact with scripture, grounded, on target for length, a well-behaved companion; its cost is time (40 s a piece, 18 s a companion reply). Gemma is not a practical server default: 8 minutes a piece and it cites pages it wasn't given. |
| **Premium default** | **GPT-6 Sol**, if its short length is acceptable or the prompt is adjusted to ask for more; otherwise **Opus 5.5** | GPT-6 Sol is 6× cheaper ($0.43 vs $2.64 a retreat), faster, and perfect on fidelity and grounding, but writes about a quarter shorter and makes few specific, checkable claims in deep dives (a thinner close reading). Opus hits length exactly and writes the richest deep dives with well-supported claims; if the premium tier is about the best prayer material regardless of a couple of dollars, keep Opus. |
| **Companion brain (taking turns)** | **GPT-6 Sol** (premium) and **Muse** (free) | The companion needs speed and restraint above richness: GPT-6 answers in about 4 seconds with short replies and perfect behaviour, at $0.035 a conversation; Opus takes 7 s and $0.23. Muse is the free choice but slow (18 s a reply). |

Before switching any default, run the stronger judges (Opus and GPT-6 as judges, and DeepEval) and a larger set of passages: with six passages a model, differences of a few hundredths in these scores are within noise.

## Caveats

- Small samples: 6 passages and 3 conversations per model; the bootstrap intervals are wide.
- Gemma was incomplete (11 of 12 pieces, no conversations). Rerun this after its generation finishes; the script picks up whatever exists.
- The measures are proxies. "Misquote" flags need a human look (a deliberate comparison with another gospel is flagged); the claim-support check matches tokens, not meaning; the ear scores don't hear the voice.
- Costs are from this run's token counts and OpenRouter's prices on the day; guidance tailoring and planning are estimated, not measured. The app's Claude web search ($0.05 a day) is added as an assumption.
- Speed depends on load and, for Gemma, on hardware.

## `fitness.json`

| Field | Contents |
| --- | --- |
| `run`, `made` | The eval run and when this was computed |
| `assumptions` | `retreat_days`, `guide_factor`, `plan_factor`, `claude_search_usd_per_day`, and a note that voices are excluded |
| `dimensions` | `[{id, label, how}]` for the nine dimensions, in display order |
| `weights` | `{weighting: {dimension: weight}}` for the five weightings |
| `models.<id>.label` | Display name |
| `models.<id>.coverage` | `pieces`, `expected_pieces`, `conversations`, `expected_conversations`, `complete` |
| `models.<id>.scripture` | `quotes`, `from_passage`, `verbatim`, `close`, `misquote`, `other`, `verbatim_share`, `examples: [{kind, quote, similarity, track, item}]` |
| `models.<id>.grounding` | `cited`, `outside`, `inside_share`, `research_used_share`, `claims`, `supported`, `supported_share`, `unsupported_examples: [{sentence, tokens}]` |
| `models.<id>.instructions` | `mean_length_ratio`, `within_15pct`, `format_ok_share`, `tags_ok_share`, `grace_share` |
| `models.<id>.ear` | `mean_sentence_words`, `long_sentence_share`, `flesch` |
| `models.<id>.companion` | `conversations`, `one_question_share`, `mean_reply_words`, `names_help_when_at_risk`, `director_claims` |
| `models.<id>.operations` | `empty`, `missing`, `median_seconds`, `p90_seconds`, `median_seconds_heart`, `median_seconds_deep`, `companion_seconds_per_reply`, `usd_per_heart`, `usd_per_deep`, `usd_per_conversation`, `usd_per_day`, `usd_per_retreat`, `local` |
| `models.<id>.quality` | `mean_1_to_7`, and the 0–1 `value` with bootstrap `lo`, `hi`, `n` |
| `models.<id>.subscores.<dimension>` | `value` (0–1) and, where it's an average of pieces, `lo`, `hi`, `n`; `speed` also has `median_seconds`, `cost` has `usd_per_retreat` |
| `models.<id>.fitness` | `{weighting: composite 0–1}` |
| `models.<id>.missing_dimensions` | Dimensions without data (excluded and the weights renormalised) |
| `models.<id>.quality_per_dollar` | Quality (0–1) per dollar per retreat; null for free models |
| `sensitivity` | `draws`, `win_share: {model: share of random weightings won}`, `winner_by_weighting: {weighting: model}` |
| `pareto` | `cost_vs_quality` and `cost_vs_fitness`: the models on each frontier, cheapest first |
| `items` | Every piece and conversation: `model`, `track`, `item`, `seconds`, `usd`, `quality`, `empty`, and the per-piece `scripture`, `instructions`, `ear`, `grounding` (deep) or `companion` measures |
