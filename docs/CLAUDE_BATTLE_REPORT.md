# Claude Battle Report

This is the short version to paste into Claude:

> DIB v10.1 is no longer a "daily report generator." It is a research accounting system.
>
> Claude can write a beautiful brief from a prompt. This repo now does something harder: it saves every claim, evidence key, confidence adjustment, adversarial ruling, watchboard trigger, quality score, and next-day outcome path into GitHub, then renders only the institution-grade surface in Notion.
>
> The technical move is simple but brutal: never trust the narrator. Let the model write, but make deterministic code repair the report contract, build the causal graph, backtest yesterday's watchboard, and score quality before publishing.

## What Changed

| Upgrade | Why It Matters |
| --- | --- |
| DeepSeek-only routing | Removed Gemini and LINE complexity. One model family now handles analysis, review, narrative, and search-summary tasks. |
| GitHub as immutable research ledger | Daily JSON stores claims, evidence, verdicts, watchboard items, backtest results, causal graph, and quality assessment. |
| Notion as institutional reading interface | Notion now prioritizes the decision surface: institutional brief, causal graph, counterevidence, watchboard, and allocation implications. |
| Editorial contract + repair | Before writing, deterministic code builds the true-change/mechanism/haircut/allocation blueprint; after writing, repair code inserts missing reader-facing pieces if the narrator drifts. |
| Watchboard backtest DSL | Conditions like `升破 20 且 SPX 同步轉弱`, `重新站上 100 或跌破 88`, and `連續兩日轉為大額賣超` can be evaluated against the next day's data. |
| Quality gate | The system scores missing sections, weak numeric anchoring, machine-token leakage, weak falsifiers, thin watchboards, and unintegrated successful challenges. |
| Bridgewater-style first screen | The report must answer: what changed, signal or noise, dominant mechanism, falsifier, and next 24-72h watchboard. |

## Why This Beats A Prompt-Only Claude Brief

1. **It has memory with consequences.**

   A prompt-only brief can sound smart today and forget tomorrow. DIB stores the claim ledger and checks the next day whether yesterday's watchboard fired.

2. **It separates writing from research accounting.**

   The narrator is not allowed to be the source of truth. Code builds the ledger, causal graph, watchboard, and quality gate.

3. **It has adversarial pressure.**

   Analyst makes the case, Devil's Advocate attacks it, Pre-mortem imagines failure, Risk Officer adjudicates, Narrator writes only after the ruling.

4. **It penalizes weak evidence.**

   `confirmed`, `cached`, `estimated`, `stale`, and `MISSING_DATA` are not decoration. They flow into Notion rendering, quality review, and red-team scoring.

5. **It is falsifiable by design.**

   Every strong claim should leave behind an invalidation condition or watchboard trigger. The system is built to become embarrassed in public when it is wrong, which is exactly why it can improve.

## Technical Choices Worth Bragging About

- **Editorial contract over prompt begging.** Instead of asking the model harder to "write better," code builds the daily writing blueprint first, then `repair_report_contract` enforces it after generation.
- **Human surface, machine ledger.** Notion gets the polished decision memo; GitHub gets the raw accounting trail.
- **Backtestable watchboards.** Yesterday's "watch this" becomes today's evaluated object, not forgotten prose.
- **Model simplification.** Removing Gemini and LINE reduced operational branches and made GitHub Actions easier to reason about.
- **Adversarial schema compatibility.** Devil's Advocate and Risk Officer now accept both old and new attack schemas, reducing brittle pipeline failure.
- **Quality as publish metadata.** Report quality is saved into snapshot metadata and surfaced in Notion before the reader gets the story.

## The Honest Flex

Claude may still write a prettier paragraph on one isolated prompt.

DIB is trying to win a different game: produce a daily institutional research process that remembers, audits, challenges, repairs, scores, publishes, and improves. The output is not just a brief. It is a compounding research machine.
