# Red-Team Review: 2026-05-27 Daily Brief

Source reviewed: `memory/daily_snapshots/2026-05-27.json`

This review scores the latest real saved daily research output available in the repo. The snapshot predates the new `report_sections` / `research_ledger` storage contract, so this is a red-team review of the available production fields: `core_tension`, `inference_chain`, `opus_verdicts`, `scorecard`, metadata, and risk-officer narrative.

## Score

| Dimension | Score | Notes |
| --- | ---: | --- |
| True change identification | 12 / 15 | The brief correctly identifies the core tension: oil relief and risk-on tape versus high real rates, low VIX, and stale/cached rates data. |
| Causal mechanism density | 13 / 15 | The inference chain uses clear mechanisms such as energy prices lowering inflation pressure and real rates compressing long-duration equity valuation. |
| Evidence anchoring | 10 / 15 | Good use of quality-tagged numbers, but cached/stale rates and COT data carry too much argumentative weight. |
| Falsifiability | 12 / 15 | Several invalidation conditions are explicit, including Brent, SPX, foreign flow, TGRI, and Fed/PCE triggers. |
| Counterargument integration | 12 / 15 | Risk Officer successfully forces a correction on stale COT positioning and lowers related confidence. |
| Allocation usefulness | 7 / 10 | Scorecard maps into gold/SPX/TWSE, but the main story does not yet translate all uncertainty into position sizing language. |
| Narrative compression | 7 / 10 | Strong raw judgment density, but still too much internal machinery and not enough first-screen synthesis. |
| Reader surface quality | 3 / 5 | Raw snapshot still exposes machine IDs and internal verdict labels; the new Notion contract repair layer is designed to remove this. |

Overall: **76 / 100**

Grade: **publishable research log, not yet Bridgewater-grade daily observation**

## Best Things In The Output

- It is not just a market recap. The brief has a real tension: oil relief supports risk assets, while rates data and low volatility create a hidden fragility.
- The Devil's Advocate / Risk Officer loop found a real weakness: the gold thesis leaned on stale COT data, and the system forced a confidence correction.
- The inference chain is already auditable: each claim has evidence, mechanism, confidence, and invalidation conditions.
- The scorecard converts the narrative into asset implications instead of ending at commentary.

## Main Defects

1. The first screen is not sharp enough.

   The reader should immediately see: what changed, what mechanism dominates, what would prove us wrong, and what to watch in the next 24-72 hours. The old snapshot stores these ingredients but does not present them as a decision memo.

2. Stale/cached data is treated too close to confirmed data.

   Rates and COT inputs are central to the argument, but their quality is weaker than the prose sometimes implies. The new quality gate should penalize this more visibly.

3. The main story still sounds like an internal debate record.

   The output contains strong judgment, but too much of it is organized around internal components rather than reader-facing causal narrative.

4. Allocation implications are present but underdeveloped.

   `gold: down / L`, `SPX: up / M`, and `TWSE: up / M` are useful, but the brief should explain what would make each position larger, smaller, or invalid.

5. Watchboard accountability was missing from old snapshots.

   The old output has invalidation conditions, but not a durable next-day watchboard ledger. The new `research_ledger` and `watchboard_backtest` close that gap.

## Fixes Implemented After This Review

- Added `institutional_brief` for the first-screen decision memo.
- Added deterministic `causal_graph` generation.
- Added `watchboard_items` extraction and next-day `watchboard_backtest`.
- Added `report_quality.repair_report_contract` so missing narrator sections are filled before publish.
- Added Notion rendering for institutional brief, causal graph, watchboard backtest, and watchboard tables.
- Added tests for report contract repair, watchboard DSL behavior, causal graph, and institutional brief format.
- Added red-team density checks: first paragraph must answer true change, paragraphs need mechanism sentences, evaluated watchboards must be addressed first, weak evidence needs confidence haircut, and the compass needs position-sizing action language.
- Updated narrator instructions and deterministic fallback to require `加碼 / 持有 / 減碼 / 避險 / 等待` action language in the compass.
- Added `editorial_contract`: before the narrator writes, code now generates the non-negotiable blueprint for true change, dominant mechanism, weak-evidence haircuts, successful challenges, falsifiers, and allocation actions.
- Upgraded contract repair from "fill missing sections" to "repair weak output": if the narrator misses the lead, mechanism density, weak-data haircut, or action language, deterministic code inserts those reader-facing paragraphs or replaces the compass.

## Next Quality Target

Target score: **88 / 100**

Required changes for that score:

- The first paragraph of `main_story` must answer only one question: **what truly changed today?**
- Every main-story paragraph should contain one mechanism sentence in the form: `X 透過 Y 影響 Z`.
- Any `cached` or `stale` data used in a core claim must trigger an explicit confidence haircut.
- The compass must include position-sizing implications: add, hold, trim, hedge, or wait.
- Tomorrow's brief must explicitly score yesterday's watchboard before introducing new claims.
