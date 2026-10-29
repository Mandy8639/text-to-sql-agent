# Hold-out review (blind set, 20 questions)

Written by the project owner without seeing prompts or conventions (evals/holdout.yaml).
Agent: **v5-abstain, frozen at commit fd3c706** (no agent change between seeing the questions
and running them). Run once per model:
`20261029T175302Z_v5-abstain_holdout.json` (Opus 5.5), `20261029T175337Z_v5-abstain_holdout.json` (Haiku 5.5).

## Result

| | Opus 5.5 | Haiku 5.5 |
|---|---:|---:|
| Auto-graded answerable (10) | 10 / 10 | 10 / 10 |
| Manual-graded answerable (7, rubric below) | 6 / 7 | 6 / 7 |
| Refuse / clarify (3) | 3 / 3 | 3 / 3 |
| **Total** | **19 / 20 (95%)** | **19 / 20 (95%)** |
| Excluding the 6 questions with a dev example ≥ 0.85 similar | 13 / 14 | 13 / 14 |
| Cost for the 20 questions | $0.69 | $0.02 |

The harness's automatic score for this file (76.5% execution accuracy) counts the open-ended
questions with fixed-shape matching. The rubric verdicts below replace it for the 7 manual questions.

## Manual verdicts (rubric = the `notes` of each question in holdout.yaml)

| Q | Opus | Haiku | Evidence |
|---|:-:|:-:|---|
| h06 Which states have the highest average order value? | ✗ | ✗ | Both excluded canceled/unavailable orders. A7 says an average of payments includes every status, which changes the ranking from 3rd place on (reference PB, AC, **RO**, AP; agents PB, AC, **AP**, AL, RO). Same AOV judgement as test join-24. |
| h10 Do customers who pay in more installments spend more per order? | ✓ | ✓ | Per-order totals by max installments. Opus matches the reference exactly; Haiku bucketed (1, 2–3, 4–6, 7–10, 11+). Both show order value rising with installments. |
| h11 Categories with the most 1-star reviews as a share of orders | ✓ | ✓ | Per-order share, identical top 10 to the reference; both warn that tiny categories (2 orders) dominate. |
| h12 Monthly revenue 2018 vs. same months 2017 | ✓ | ✓ | Jan–Aug, payments; numbers match the reference; both say why they stop at August. |
| h14 Late deliveries vs. review scores | ✓ | ✓ | Late 2.57 vs. on-time 4.29, as in the reference; Haiku adds "association, not causation". |
| h15 Best 2018 month for each top-3 category | ✓ | ✓ | Opus ranked the top 3 by 2018 revenue, Haiku all-time (both stated); same categories and best months (bed_bath_table Jan, watches_gifts May, health_beauty Aug). |
| h16 Sellers with sales up every quarter of 2018 | ✓ | ✓ | Same 54 sellers as the reference; **both warned that Q3 has only July–August and Q4 is missing.** Haiku's first query had a typo; self-correction fixed it on retry. |

Refuse/clarify: h18 (profit margin) and h19 (marketing channel) refused because the data does not
exist; h20 ("best customers") asked how "best" should be measured.

## Similarity to dev examples

| ≥ 0.85 | h01 0.97 (≈ dev flt-05), h13 0.90 (flt-13), h17 0.88 (win-10, same question), h03 0.86, h12 0.86, h09 0.85 |
|---|---|
| < 0.85 | the other 14 questions |

h01 and h17 are effectively dev questions. They were written blind, but the example bank already
covered them. The leakage guard exempts content that also exists in dev (D32).

## Takeaways

1. The test-set result **generalised**: 95% on blind questions, with the only miss a convention
   question, not a SQL error.
2. **Average order value is the one recurring disagreement** (test join-24, hold-out h06): both
   models treat AOV as a sales metric (exclude canceled orders), while A7 treats any average of
   payments as all-status. That is evidence the *convention* is counter-intuitive, not that the
   agent is wrong. See D32 for the proposed fix.
3. Open-ended questions (7 of 20) cannot be graded by result-set match. Real users ask them, so
   a rubric review (or an LLM judge calibrated on these reviews) is needed beyond the benchmark.
