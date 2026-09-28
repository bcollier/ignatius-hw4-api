# Scale study: getting the judges off the ceiling

**Question.** The current rubric's raw scores pile up at the top: 79% of the cheap judges' composite scores fall in the top two points of the 1–7 scale. Within a track, the judges barely agree about which pieces are better (Krippendorff's α = −0.26). Which way of asking gives scores with more spread, a more bell-shaped distribution, better agreement and better discrimination?

**Set-up.** The same 48 items from `evals/runs/full` (35 pieces: heart and deep dive, four contestants including Gemma where it has samples; plus 13 companion conversations) were scored in seven new ways by the three cheap/free judges (Gemini 3.8 Flash, Muse Glimmer, Llama 4 Scout). The current-rubric results (V0) were reused, not re-asked. A quarter of the judgments (chosen by a hash, so the same ones for every judge) were asked twice to measure test–retest stability. Every variant used the same reduced dimension set: overall, emotionally engaging, thoughtful, too vague and AI jargon for pieces; overall, listening, spiritual depth and restraint for the companion. The **primary score** of the rubric variants and of V0 is the same composite: the non-overall dimensions, with the "lower is better" ones turned around. That way they are compared like for like, and `overall` is reported separately. The checklist's score is the number of criteria met (0–10). Pairwise gives each item's mean signed margin across its comparisons, plus Bradley–Terry strengths per model. Ranking gives each item's rank position (higher is better).

All reliability figures are computed **within a track** and then averaged, weighted by items. Telling a deep dive from a conversation earns a judge nothing.

**Run:** 1,377 judgments, **0 invalid JSON** after at most one retry (2 retries in total, both for the reference-anchored variant). The paid cost was **$1.64** (Gemini only; Jetstream is free), plus $0.005 for a timing probe. It took 2 h 2 min wall-clock, bounded by the Jetstream limit of two concurrent calls, or about 303 judge-minutes.

## Headline

1. **Comparative judgment beats every absolute scale.** Showing the judges the pieces for the same day side by side did more than any change to the wording of a scale. Pairwise comparison had the best inter-judge agreement by a wide margin: α 0.49, ICC(2,k) 0.74 and judge–judge rank ρ 0.48, against at best α 0.04, ICC(2,k) 0.30 and ρ 0.29 for the absolute scales. It also had the best discrimination between models: η² above chance averaged 0.46, and it was significant on all three tracks, including deep dives. Ranking all the day's pieces together is a close second on the composite ranking, but some of its lead comes from the design itself (see caveats).
2. **Rewording an absolute scale helps a little, and only with one judge.** Gemini and Llama stay at the ceiling whatever the prompt says. In the 0–10 variant, which explicitly told them "5 is average, one in ten scores 8+", 96% of their scores still landed in the top band. Muse is the only judge that follows calibration instructions (its ceiling share was 0–28% across the variants). Most of the "spread" that the absolute variants gain is Muse moving while the other two stay put. That is also why α stays near zero: the judges differ in *leniency*, not only in what they notice.
3. **Among the absolute scales:**
   - **Critique first** cut the pooled ceiling the most (79% → 45%) and had excellent stability (retest ρ 0.95). But agreement between judges fell (α −0.30): the judges find *different* flaws.
   - **Reference exemplars** (weak, typical and strong pieces from another day, labelled 2, 4 and 6) gave the most bell-shaped distribution (distance from normal 0.04, the lowest of any variant). They also gave the best deep-dive rank agreement of any absolute scale (ρ 0.33) and a 65% ceiling. These references would be the anchors to keep.
   - **Behavioural anchors** ("4 = typical competent work, most land 3–5") cut the ceiling to 58%, and they separated the models best of the absolute scales (η² − chance 0.23).
   - **0–10 with a target distribution** did almost nothing: 74% ceiling, the lowest spread of all. The judges simply map "good" to 8. It did have the best retest ρ (0.97), because it is stable in its compression.
   - **Checklist** was the worst: 88% ceiling. Most criteria were met by nearly every item (heart: 7 of 10 criteria met by ≥ 97% of pieces). It barely tracks quality: its convergent ρ with pairwise is 0.02 and with ranking 0.04, and Opus came out on top on only 1 of 3 tracks. The deep-dive criteria (original language, Church reading, open questions) are the only ones that discriminate.
4. **The current rubric correlates with the "surface" variants, not with the comparative ones.** V0's item scores correlate 0.66–0.67 with the checklist and the 0–10 scale, but only 0.14–0.23 with pairwise and ranking. The comparative methods agree with each other (ρ 0.70) and moderately with the anchored and exemplar scales (0.30–0.48). This suggests the current rubric partly measures surface features (the absence of problems) rather than relative quality.
5. **What replicates:** Opus comes out on top on all three tracks under ranking, the anchored scales, exemplars and V0. Under pairwise, GPT-6 Sol edges out Opus on deep dives (Bradley–Terry 0.67 vs 0.43), and Opus leads heart and companion by a wide margin. Gemma and Muse are consistently last on the pieces.

## Which changes were most effective, and why

| Change | Effect | Why |
| --- | --- | --- |
| **Relative instead of absolute judgment** (pairwise, ranking) | Largest: agreement α −0.26 → 0.49 (pairwise); discrimination significant on every track | It removes leniency differences between judges entirely. Every judge has to pick, so a lenient judge can no longer give everything a 6. Humans and LLMs are both better at "which is better" than at "how good is this". |
| **Concrete reference pieces** (exemplars) | Best bell shape; ceiling 79% → 65%; best deep-dive agreement of the absolute scales | The judge places the piece against real examples rather than an abstract number, which gives the middle of the scale a meaning. |
| **Forcing criticism before the score** (critique first) | Biggest ceiling cut of the absolute scales (→ 45%); retest 0.95 | Having quoted three flaws, the judge can't consistently give 7. But the flaws chosen are idiosyncratic, so agreement doesn't improve. |
| **Describing every point** (anchored) | Moderate ceiling cut (→ 58%); best model separation of the absolute scales | It gives the judges permission to use 4 for "competent". Only Muse takes it fully. |
| **More points / distribution targets** (0–10) | None, or slightly harmful | The judges ignore base-rate instructions and compress into the top band of whatever scale they are given. |
| **Yes/no checklist** | Harmful for spread | The criteria were too easy for professional-quality pieces. A checklist measures compliance, not quality. |

## Recommendation for the expensive run

1. **Primary quality measure: pairwise comparison within each day's pieces, asked in both orders.** Pairwise showed a position bias: the first-shown piece won only 41% of comparisons. Asking both orders cancels it and doubles the data, which also addresses pairwise's weakest number, its retest ρ of 0.73. With four contestants that is 6 pairs × 2 orders = 12 calls per day per judge. Score models by Bradley–Terry, with a bootstrap over days for intervals. `eval_scale_pairwise.md` is ready to use. Ranking (`eval_scale_ranking.md`) is a cheaper alternative (1 call per day), but its judge–judge agreement is weaker (ρ 0.14).
2. **For per-dimension diagnostics** (what is better, not just whether it is better), keep an absolute rubric, but use the **reference-exemplar anchoring plus the behavioural anchors** (combine `eval_scale_exemplar.md` and `eval_scale_anchored.md`). Also **z-score each judge's scores** before pooling, because the leniency differences are large and stable.
3. **Drop the 0–10 scale and the checklist** as quality measures. The deep-dive checklist items could survive as a separate compliance check (original language, Church reading, open questions).
4. Caveat for the expensive judges: this study only used cheap judges, and the ceiling behaviour belongs mostly to Gemini and Llama. Stronger judges may use absolute scales better. A small calibration batch (e.g. 12 items under V0 and exemplar) would show it before committing.

## Caveats

- **Ranking's shape scores are built in.** Ranking four pieces always gives a uniform distribution, so its spread, entropy and no-ceiling ranks (1, 1, 1) are structural rather than earned. On the criteria it has to earn (agreement and retest), it is middling: ICC(2,k) 0.29 and judge–judge ρ 0.14. Pairwise is also partly relative (each day's item scores sum to zero), but its agreement numbers are earned. That is why pairwise is the recommendation.
- **The sample is small:** 48 items and 11–12 per model per track. The deep-dive and companion η² values have wide intervals, and the p-values are permutation tests with no correction across variants.
- **V0 has no retest or JSON failure figures** (it was not re-run), so it is ranked on seven criteria rather than nine.
- **The exemplars were chosen from the V0 scores of other days** (the lowest, median and highest items of the same track). They are not the same piece as the item being rated, but the same *model's* work can appear as a reference.

## Files

- `evals/scale_study.py`: collection (cached under `evals/runs/full/scale_study/<variant>/`), statistics and the tables below. Run `.venv/bin/python -m evals.scale_study --collect` (resumable) or `--analyse`.
- `app/agent_prompts/eval_scale_{anchored,ten,exemplar,critique,checklist,pairwise,ranking}.md`: the judge prompts (sections `pieces` or `companion`; the checklist has `heart`, `deep` and `companion`).
- `~/Code/ignatius-hw4-web/evals/scales.json`: all results (a pretty-printed copy is at `evals/runs/full/scale_study/scales.json`).

## `scales.json` fields

```
made, run                     ISO timestamp; run directory name
judges                        ["gemini", "muse", "llama-scout"]
judge_labels, model_labels    id -> display name
ranking.order                 variant ids, best first
ranking.mean_rank             variant -> mean rank across the criteria
ranking.criteria.<key>        {label, values: {variant: number}, rank: {variant: 1..n}}
                              keys: spread, entropy, no_ceiling, normal, agreement, alpha, retest,
                              discrimination, valid_json (all oriented so higher = better)
convergent                    {names: [variant ids], matrix: Spearman ρ between variants' item means, names x names}
variants.<id>
  label, kind, change         display name; existing|rubric|checklist|pairwise|ranking; one-line description
  range                       [lo, hi] of the primary score
  shape                       {n, mean, sd, sd_norm (sd / range), distinct, entropy (0-1), ceiling, floor
                               (share in the top / bottom 2/7 of the range), skew, kurtosis (excess),
                               normal_tv (total-variation distance from a fitted normal; 0 = perfect),
                               hist (counts), hist_labels (lower edge or value per bin)}
                              Discrete variants (checklist, ranking) have one bin per value; the others
                              have 7 equal bins over the range.
  per_judge.<judge>           the same shape object for one judge's scores (use for small multiples)
  reliability                 {alpha, icc1 (ICC(2,1)), icck (ICC(2,k)), kappa (mean pairwise quadratic κ;
                               null for relative variants), rho (mean pairwise judge–judge Spearman),
                               items, by_track.<track>: the same per track}
  discrimination.<track>      {eta2, null (mean η² under permutation), p, model_means, order, opus_top}
  test_retest                 {n, spearman, icc, exact (share identical), mean_abs_diff}
  dimensions.<dim>            rubric variants only: {shape, reliability, lower_is_better}
  criteria.<track>.<cN>       checklist only: share of items meeting each criterion
  bradley_terry.<track>       pairwise only: model -> log-strength (0 = average)
  position_bias_A             pairwise only: share of wins for the first-shown piece
  calls, attempts, failed, failure_rate, retry_rate, usd, seconds
  convergent_mean             mean ρ with the other variants
  items                       "model|track|item" -> mean primary score across judges
  judgments                   [{judge, model, track, item, score}] first-run primary scores
```

## Ranking

Each variant ranked on nine criteria (1 = best); the table is ordered by the mean rank.

| Rank | Variant | Mean rank | Spread (normalized SD) | Values used (entropy) | Not piled at the top | Close to a bell curve | Judges agree (ICC(2,k)) | Krippendorff's alpha | Same answer twice (retest ρ) | Separates the models (η² above chance) | Returns valid JSON |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Rank the day's pieces | 3.00 | 1 | 1 | 1 | 6 | 3 | 2 | 4 | 2 | 7 |
| 2 | Pairwise comparison | 3.33 | 2 | 2 | 2 | 8 | 1 | 1 | 7 | 1 | 6 |
| 3 | Anchored to reference pieces | 4.11 | 6 | 4 | 5 | 1 | 4 | 4 | 3 | 7 | 3 |
| 4 | Anchored 1–7 | 4.44 | 7 | 6 | 4 | 3 | 6 | 5 | 5 | 3 | 1 |
| 5 | Critique first, then 1–7 | 4.56 | 4 | 3 | 3 | 5 | 8 | 8 | 2 | 4 | 4 |
| 6 | 0–10 with a target distribution | 4.78 | 8 | 8 | 6 | 2 | 5 | 6 | 1 | 5 | 2 |
| 7 | Current rubric (1–7) | 5.57 | 3 | 5 | 7 | 4 | 7 | 7 | – | 6 | – |
| 8 | Checklist (10 yes/no) | 5.67 | 5 | 7 | 8 | 7 | 2 | 3 | 6 | 8 | 5 |

## The numbers

| Variant | What changed | SD (share of range) | Entropy | Ceiling | Skew | Distance from normal | α (within track) | ICC(2,1) | ICC(2,k) | κw | Judge–judge ρ | Retest ρ | η² − chance (mean of tracks) | Opus on top | Invalid JSON | Cost | Judge-minutes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Rank the day's pieces | All the pieces for a day ranked together: a forced, even distribution. | 0.36 | 1.00 | 21% | 0.08 | 0.16 | 0.16 | 0.15 | 0.29 | – | 0.14 | 0.90 | 0.39 | 3/3 | 0% | $0.09 | 15 |
| Pairwise comparison | Two pieces for the same day: which is better, by 0–3; scored by Bradley–Terry. | 0.22 | 0.82 | 23% | 0.01 | 0.23 | 0.49 | 0.50 | 0.74 | – | 0.48 | 0.73 | 0.46 | 2/3 | 0% | $0.29 | 57 |
| Anchored to reference pieces | Three scored reference pieces (weak 2, typical 4, strong 6) from another day. | 0.11 | 0.62 | 65% | -0.56 | 0.04 | -0.06 | 0.12 | 0.27 | 0.08 | 0.28 | 0.90 | 0.15 | 3/3 | 0% | $0.30 | 43 |
| Anchored 1–7 | Every point described; 4 = typical competent work; told most pieces land 3–5. | 0.10 | 0.60 | 58% | -0.70 | 0.06 | -0.14 | 0.07 | 0.18 | -0.01 | 0.21 | 0.89 | 0.23 | 3/3 | 0% | $0.17 | 37 |
| Critique first, then 1–7 | Must quote 3–5 specific flaws before scoring. | 0.12 | 0.66 | 45% | -0.59 | 0.13 | -0.30 | 0.03 | 0.08 | 0.06 | 0.16 | 0.95 | 0.19 | 2/3 | 0% | $0.29 | 54 |
| 0–10 with a target distribution | Wider scale; 5 = average; told the expected shares at each end. | 0.08 | 0.48 | 74% | -0.53 | 0.04 | -0.15 | 0.12 | 0.26 | 0.19 | 0.29 | 0.97 | 0.16 | 2/3 | 0% | $0.16 | 35 |
| Current rubric (1–7) | The rubric as it is: 19 scales, 1–7, "4 is ordinary, 1 and 7 are rare". | 0.13 | 0.60 | 79% | -1.10 | 0.07 | -0.26 | 0.05 | 0.14 | -0.01 | 0.22 | – | 0.16 | 3/3 | – | $0.00 | 0 |
| Checklist (10 yes/no) | Ten concrete yes/no criteria per track, summed; "when in doubt, no". | 0.12 | 0.58 | 88% | -0.94 | 0.21 | 0.04 | 0.18 | 0.30 | 0.20 | 0.28 | 0.84 | 0.04 | 1/3 | 0% | $0.32 | 62 |

### Separating the models, track by track

| Variant | Track | η² | chance η² | p | Order (best first) |
| --- | --- | --- | --- | --- | --- |
| Rank the day's pieces | heart | 0.63 | 0.13 | 0.00 | Claude Opus 5.5 > OpenAI GPT-6 Sol > Gemma 4 31B > Muse Glimmer |
| Rank the day's pieces | deep | 0.17 | 0.13 | 0.29 | Claude Opus 5.5 > OpenAI GPT-6 Sol > Gemma 4 31B > Muse Glimmer |
| Rank the day's pieces | companion | 0.88 | 0.24 | 0.03 | Claude Opus 5.5 > OpenAI GPT-6 Sol > Muse Glimmer |
| Pairwise comparison | heart | 0.64 | 0.13 | 0.00 | Claude Opus 5.5 > Muse Glimmer > OpenAI GPT-6 Sol > Gemma 4 31B |
| Pairwise comparison | deep | 0.36 | 0.13 | 0.03 | OpenAI GPT-6 Sol > Claude Opus 5.5 > Gemma 4 31B > Muse Glimmer |
| Pairwise comparison | companion | 0.90 | 0.25 | 0.02 | Claude Opus 5.5 > Muse Glimmer > OpenAI GPT-6 Sol |
| Anchored to reference pieces | heart | 0.54 | 0.13 | 0.00 | Claude Opus 5.5 > Muse Glimmer > OpenAI GPT-6 Sol > Gemma 4 31B |
| Anchored to reference pieces | deep | 0.31 | 0.13 | 0.06 | Claude Opus 5.5 > OpenAI GPT-6 Sol > Muse Glimmer > Gemma 4 31B |
| Anchored to reference pieces | companion | 0.09 | 0.24 | 0.78 | Claude Opus 5.5 > Muse Glimmer > OpenAI GPT-6 Sol |
| Anchored 1–7 | heart | 0.66 | 0.13 | 0.00 | Claude Opus 5.5 > Muse Glimmer > OpenAI GPT-6 Sol > Gemma 4 31B |
| Anchored 1–7 | deep | 0.41 | 0.13 | 0.01 | Claude Opus 5.5 > OpenAI GPT-6 Sol > Gemma 4 31B > Muse Glimmer |
| Anchored 1–7 | companion | 0.15 | 0.25 | 0.66 | Claude Opus 5.5 > Muse Glimmer > OpenAI GPT-6 Sol |
| Critique first, then 1–7 | heart | 0.43 | 0.13 | 0.01 | Claude Opus 5.5 > OpenAI GPT-6 Sol > Muse Glimmer > Gemma 4 31B |
| Critique first, then 1–7 | deep | 0.28 | 0.13 | 0.09 | Claude Opus 5.5 > OpenAI GPT-6 Sol > Muse Glimmer > Gemma 4 31B |
| Critique first, then 1–7 | companion | 0.36 | 0.24 | 0.26 | Muse Glimmer > Claude Opus 5.5 > OpenAI GPT-6 Sol |
| 0–10 with a target distribution | heart | 0.66 | 0.13 | 0.00 | Claude Opus 5.5 > Muse Glimmer > OpenAI GPT-6 Sol > Gemma 4 31B |
| 0–10 with a target distribution | deep | 0.21 | 0.13 | 0.15 | Claude Opus 5.5 > OpenAI GPT-6 Sol > Gemma 4 31B > Muse Glimmer |
| 0–10 with a target distribution | companion | 0.13 | 0.25 | 0.70 | Muse Glimmer > Claude Opus 5.5 > OpenAI GPT-6 Sol |
| Current rubric (1–7) | heart | 0.60 | 0.13 | 0.00 | Claude Opus 5.5 > Muse Glimmer > OpenAI GPT-6 Sol > Gemma 4 31B |
| Current rubric (1–7) | deep | 0.36 | 0.13 | 0.02 | Claude Opus 5.5 > Muse Glimmer > OpenAI GPT-6 Sol > Gemma 4 31B |
| Current rubric (1–7) | companion | 0.06 | 0.28 | 0.92 | Claude Opus 5.5 > Muse Glimmer > OpenAI GPT-6 Sol > Gemma 4 31B |
| Checklist (10 yes/no) | heart | 0.19 | 0.13 | 0.23 | OpenAI GPT-6 Sol > Claude Opus 5.5 > Gemma 4 31B > Muse Glimmer |
| Checklist (10 yes/no) | deep | 0.24 | 0.13 | 0.13 | Claude Opus 5.5 > Muse Glimmer > OpenAI GPT-6 Sol > Gemma 4 31B |
| Checklist (10 yes/no) | companion | 0.20 | 0.25 | 0.56 | Muse Glimmer > Claude Opus 5.5 > OpenAI GPT-6 Sol |

### Convergent validity (Spearman ρ between variants' item scores)

| | Current rubric (1–7) | Anchored 1–7 | 0–10 with a target distribution | Anchored to reference pieces | Critique first, then 1–7 | Checklist (10 yes/no) | Pairwise comparison | Rank the day's pieces |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Current rubric (1–7) | 1.00 | 0.23 | 0.67 | 0.33 | 0.52 | 0.66 | 0.23 | 0.14 |
| Anchored 1–7 | 0.23 | 1.00 | 0.64 | 0.36 | 0.57 | 0.17 | 0.42 | 0.47 |
| 0–10 with a target distribution | 0.67 | 0.64 | 1.00 | 0.44 | 0.71 | 0.45 | 0.44 | 0.36 |
| Anchored to reference pieces | 0.33 | 0.36 | 0.44 | 1.00 | 0.46 | -0.09 | 0.48 | 0.30 |
| Critique first, then 1–7 | 0.52 | 0.57 | 0.71 | 0.46 | 1.00 | 0.29 | 0.45 | 0.40 |
| Checklist (10 yes/no) | 0.66 | 0.17 | 0.45 | -0.09 | 0.29 | 1.00 | 0.02 | 0.04 |
| Pairwise comparison | 0.23 | 0.42 | 0.44 | 0.48 | 0.45 | 0.02 | 1.00 | 0.70 |
| Rank the day's pieces | 0.14 | 0.47 | 0.36 | 0.30 | 0.40 | 0.04 | 0.70 | 1.00 |

### Each judge's distribution (primary score)

| Variant | Judge | SD (share of range) | Entropy | Ceiling | Mean |
| --- | --- | --- | --- | --- | --- |
| Rank the day's pieces | Google Gemini 3.8 Flash | 0.36 | 1.00 | 21% | 2.42 |
| Rank the day's pieces | Muse Glimmer | 0.36 | 1.00 | 21% | 2.42 |
| Rank the day's pieces | Llama 4 Scout | 0.36 | 1.00 | 21% | 2.42 |
| Pairwise comparison | Google Gemini 3.8 Flash | 0.22 | 0.81 | 25% | -0.00 |
| Pairwise comparison | Muse Glimmer | 0.20 | 0.79 | 23% | -0.00 |
| Pairwise comparison | Llama 4 Scout | 0.23 | 0.78 | 23% | 0.00 |
| Anchored to reference pieces | Google Gemini 3.8 Flash | 0.09 | 0.46 | 95% | 6.01 |
| Anchored to reference pieces | Muse Glimmer | 0.09 | 0.49 | 26% | 5.00 |
| Anchored to reference pieces | Llama 4 Scout | 0.06 | 0.39 | 74% | 5.71 |
| Anchored 1–7 | Google Gemini 3.8 Flash | 0.09 | 0.55 | 79% | 5.82 |
| Anchored 1–7 | Muse Glimmer | 0.08 | 0.44 | 26% | 5.08 |
| Anchored 1–7 | Llama 4 Scout | 0.08 | 0.48 | 68% | 5.38 |
| Critique first, then 1–7 | Google Gemini 3.8 Flash | 0.08 | 0.48 | 65% | 5.53 |
| Critique first, then 1–7 | Muse Glimmer | 0.07 | 0.48 | 0% | 4.20 |
| Critique first, then 1–7 | Llama 4 Scout | 0.04 | 0.31 | 70% | 5.47 |
| 0–10 with a target distribution | Google Gemini 3.8 Flash | 0.06 | 0.31 | 96% | 8.15 |
| 0–10 with a target distribution | Muse Glimmer | 0.06 | 0.40 | 28% | 6.76 |
| 0–10 with a target distribution | Llama 4 Scout | 0.04 | 0.28 | 96% | 7.97 |
| Current rubric (1–7) | Google Gemini 3.8 Flash | 0.07 | 0.26 | 98% | 6.35 |
| Current rubric (1–7) | Muse Glimmer | 0.12 | 0.54 | 47% | 5.18 |
| Current rubric (1–7) | Llama 4 Scout | 0.09 | 0.36 | 93% | 5.88 |
| Checklist (10 yes/no) | Google Gemini 3.8 Flash | 0.12 | 0.52 | 91% | 9.09 |
| Checklist (10 yes/no) | Muse Glimmer | 0.12 | 0.63 | 75% | 8.26 |
| Checklist (10 yes/no) | Llama 4 Scout | 0.08 | 0.42 | 98% | 9.39 |

### Single dimensions (rubric variants)

| Variant | Dimension | SD (share of range) | Ceiling | α | ICC(2,k) |
| --- | --- | --- | --- | --- | --- |
| Anchored to reference pieces | ai jargon ↓ | 0.12 | 0% | -0.01 | 0.37 |
| Anchored to reference pieces | emotionally engaging | 0.11 | 15% | 0.07 | 0.40 |
| Anchored to reference pieces | listening | 0.15 | 78% | -0.26 | -0.39 |
| Anchored to reference pieces | overall | 0.12 | 19% | 0.02 | 0.19 |
| Anchored to reference pieces | restraint | 0.12 | 89% | -0.22 | -0.28 |
| Anchored to reference pieces | spiritual depth | 0.20 | 30% | 0.01 | 0.35 |
| Anchored to reference pieces | thoughtful | 0.12 | 41% | -0.06 | 0.26 |
| Anchored to reference pieces | too vague ↓ | 0.13 | 0% | -0.09 | 0.28 |
| Anchored 1–7 | ai jargon ↓ | 0.09 | 0% | -0.16 | 0.14 |
| Anchored 1–7 | emotionally engaging | 0.13 | 23% | -0.08 | 0.23 |
| Anchored 1–7 | listening | 0.09 | 0% | -0.21 | -1.00 |
| Anchored 1–7 | overall | 0.10 | 28% | 0.03 | 0.18 |
| Anchored 1–7 | restraint | 0.10 | 18% | -0.06 | -0.07 |
| Anchored 1–7 | spiritual depth | 0.14 | 0% | 0.28 | 0.65 |
| Anchored 1–7 | thoughtful | 0.10 | 46% | -0.13 | 0.17 |
| Anchored 1–7 | too vague ↓ | 0.12 | 0% | -0.26 | 0.14 |
| Critique first, then 1–7 | ai jargon ↓ | 0.20 | 0% | -0.38 | 0.03 |
| Critique first, then 1–7 | emotionally engaging | 0.14 | 9% | 0.12 | 0.38 |
| Critique first, then 1–7 | listening | 0.11 | 52% | -0.13 | 0.00 |
| Critique first, then 1–7 | overall | 0.09 | 5% | -0.03 | 0.17 |
| Critique first, then 1–7 | restraint | 0.19 | 56% | -0.13 | 0.11 |
| Critique first, then 1–7 | spiritual depth | 0.19 | 11% | -0.04 | 0.38 |
| Critique first, then 1–7 | thoughtful | 0.13 | 38% | -0.10 | 0.13 |
| Critique first, then 1–7 | too vague ↓ | 0.17 | 0% | -0.39 | 0.02 |
| 0–10 with a target distribution | ai jargon ↓ | 0.10 | 0% | -0.30 | 0.09 |
| 0–10 with a target distribution | emotionally engaging | 0.14 | 53% | -0.12 | 0.23 |
| 0–10 with a target distribution | listening | 0.07 | 85% | -0.35 | -1.95 |
| 0–10 with a target distribution | overall | 0.08 | 52% | -0.10 | 0.23 |
| 0–10 with a target distribution | restraint | 0.06 | 96% | -0.28 | -1.05 |
| 0–10 with a target distribution | spiritual depth | 0.17 | 18% | 0.58 | 0.84 |
| 0–10 with a target distribution | thoughtful | 0.08 | 74% | -0.17 | 0.21 |
| 0–10 with a target distribution | too vague ↓ | 0.10 | 0% | -0.20 | 0.20 |
| Current rubric (1–7) | ai jargon ↓ | 0.12 | 0% | -0.33 | 0.07 |
| Current rubric (1–7) | all scales | 0.12 | 75% | -0.21 | 0.17 |
| Current rubric (1–7) | emotionally engaging | 0.14 | 63% | -0.16 | 0.01 |
| Current rubric (1–7) | listening | 0.17 | 83% | -0.25 | -0.24 |
| Current rubric (1–7) | restraint | 0.14 | 92% | -0.09 | -0.05 |
| Current rubric (1–7) | spiritual depth | 0.24 | 50% | 0.20 | 0.59 |
| Current rubric (1–7) | thoughtful | 0.09 | 84% | -0.06 | 0.23 |
| Current rubric (1–7) | too vague ↓ | 0.17 | 1% | -0.33 | 0.10 |

### Checklist: share of items meeting each criterion

- **heart**: c1 100%, c2 92%, c3 100%, c4 100%, c5 100%, c6 97%, c7 97%, c8 83%, c9 97%, c10 93%
- **deep**: c1 100%, c2 76%, c3 49%, c4 56%, c5 65%, c6 92%, c7 97%, c8 100%, c9 85%, c10 99%
- **companion**: c1 100%, c2 93%, c3 82%, c4 85%, c5 89%, c6 63%, c7 93%, c8 100%, c9 100%, c10 100%

### Bradley–Terry strengths from the pairwise comparisons (log scale, 0 = average)

- **companion**: Claude Opus 5.5 1.42, Muse Glimmer -0.57, OpenAI GPT-6 Sol -0.85
- **deep**: OpenAI GPT-6 Sol 0.67, Claude Opus 5.5 0.43, Gemma 4 31B -0.42, Muse Glimmer -0.68
- **heart**: Claude Opus 5.5 1.94, Muse Glimmer -0.22, OpenAI GPT-6 Sol -0.76, Gemma 4 31B -0.97
- Position bias: the first-shown piece won 41% of comparisons (50% = none).

## Follow-up: the revised rubric (v2), 28 September 2026

The first four recommendations above, combined into one rubric (`app/agent_prompts/eval_scale_v2.md`), then run on the same 48 items with the same three judges:
- **Reference point:** 4 = what a capable AI model typically writes for this app; 7 = master level, shown with Newman's "Hope in God—Creator" (1893) and Augustine's Tractate 15 on John (NPNF, 1888).
- **Anchors** at 2, 4 and 6 for every scale.
- **Critique first:** 2–3 quoted weaknesses before any score.
- **Reference pieces:** weak, typical and strong ones from another day, plus a quote required for any 6 or 7.

It used 216 judgments with none invalid, and cost $0.53.

| | Current rubric (v0) | Revised (v2) |
| --- | --- | --- |
| Share in the top two points | 79% | **27%** |
| Mean (1–7) | 5.80 | 4.85 |
| Skew | −1.10 | **0.14** (nearly symmetric) |
| Krippendorff's α (within track) | −0.26 | 0.05 |
| ICC(2,k), the three judges' average | 0.14 | **0.40** |
| Same answer twice (retest ρ) | – | 0.93 |
| Separates the models (η² over chance, mean of tracks) | 0.16 | **0.24** |
| Deep dives: η² (p) | 0.36 (.02) | **0.64 (<.001)** |
| Companion: η² (p) | 0.06 (.92) | 0.11 (.76) |

Gemini's share of 6s and 7s fell from 98% to 50%, Llama's from 93% to 22%, and Muse's from 47% to 8%. The quote rule demoted only 7 of 251 high scores, so the reference point, the anchors and the critique did most of the work. The judges now agree more, though still well short of the 0.67 usually wanted; pairwise comparison remains the better tool for ranking the models, and the companion track is still not separated by any absolute rubric.

On the mean rank across the nine criteria, v2 ties pairwise for second, behind ranking, whose even spread is built in.
