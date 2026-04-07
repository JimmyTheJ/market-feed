# Position-Driven Market Pipeline

## Goal

Build a daily, low-stimulation market intelligence pipeline that is driven by the current portfolio configuration, automatically enriches position metadata, and produces dated daily artifacts that preserve a full historical record.

The purpose of the system is not just to gather market information, but to do so in a way that reduces cognitive load, lowers the need for high-stimulation visual media, and creates a consistent, auditable daily research process.

---

## High-Level Requirements

The system should:

1. Read a single `positions.yaml` file as the canonical user-maintained portfolio input.
2. Require only minimal user input per position:

   * `ticker`
   * `weight`
3. Automatically enrich each ticker with metadata obtained from a lookup layer.
4. Generate a dated daily snapshot of the enriched positions file.
5. Produce a dated daily market digest.
6. Preserve every daily output permanently.
7. Run automatically every day without manual intervention.
8. Be easy to reconfigure when positions change.
9. Bias toward low-stimulation output formats such as Markdown, plain text, terminal-readable logs, and optionally text-to-speech.

---

## Naming and Historical Artifact Requirements

### Canonical input file

This is the only file you edit manually:

`positions.yaml`

It contains only the current target positions and weights.

Example:

```yaml
positions:
  - ticker: IBIT
    weight: 0.35

  - ticker: QQQ
    weight: 0.30

  - ticker: CPER
    weight: 0.20

  - ticker: UNG
    weight: 0.15
```

### Daily enriched positions snapshot

Each day, the pipeline should generate a dated expanded positions file:

`daily-positions-YYYY-MM-DD.yaml`

Example:

`daily-positions-2026-04-03.yaml`

This file is derived from the current `positions.yaml` plus the metadata lookup layer.

### Daily digest output

Each run should also generate a dated digest file:

`market_digest-YYYY-MM-DD.md`

Example:

`market_digest-2026-04-03.md`

### Daily analysis directory

A clean layout is to store daily artifacts inside a dated directory:

`output/YYYY-MM-DD-analysis/`

Example contents:

```text
output/2026-04-03-analysis/
├── daily-positions-2026-04-03.yaml
├── market_digest-2026-04-03.md
├── raw_articles-2026-04-03.json
├── ranked_articles-2026-04-03.json
├── run_log-2026-04-03.txt
└── summary_payload-2026-04-03.json
```

This gives you both human-readable history and machine-auditable history.

---

## Design Philosophy

The system should be built around one core principle:

**The portfolio determines what information is relevant.**

Instead of consuming everything and filtering mentally, the pipeline should use the portfolio itself as the filter definition.

That means:

* no open-ended media grazing
* no reactive doom scrolling
* no dependence on influencer interpretation
* no permanent attention hijack from visual feeds

Instead, the workflow becomes:

1. Define portfolio exposure.
2. Enrich portfolio metadata.
3. Pull relevant information.
4. Rank it by relevance to positions.
5. Summarize it in a calm, compressed format.
6. Read it once or twice a day.

---

## End-to-End Daily Flow

Each day, the automated run should do the following:

1. Determine the current date.
2. Create a daily output directory using the format `YYYY-MM-DD-analysis`.
3. Load the current `positions.yaml`.
4. For each ticker, perform a metadata lookup.
5. Generate `daily-positions-YYYY-MM-DD.yaml` containing the enriched positions.
6. Build search terms, themes, and relevance rules from the enriched positions.
7. Pull market content from configured sources.
8. Normalize the fetched content into a common schema.
9. Score and rank content against the enriched daily positions.
10. Summarize the most relevant items.
11. Write `market_digest-YYYY-MM-DD.md`.
12. Write supporting JSON and log files.
13. Exit cleanly.

This should be runnable on demand and also schedulable as a daily job.

---

## Proposed Project Structure

```text
market-pipeline/
├── config/
│   ├── positions.yaml
│   ├── sources.yaml
│   └── settings.yaml
├── data/
│   ├── cache/
│   ├── metadata/
│   └── watchlists/
├── output/
│   ├── 2026-04-03-analysis/
│   └── 2026-04-04-analysis/
├── src/
│   ├── main.py
│   ├── models.py
│   ├── date_utils.py
│   ├── positions_loader.py
│   ├── metadata_lookup.py
│   ├── ingestion.py
│   ├── normalization.py
│   ├── scoring.py
│   ├── summarizer.py
│   ├── digest_writer.py
│   └── storage.py
├── tests/
├── requirements.txt
└── README.md
```

---

## File Responsibilities

### `config/positions.yaml`

The user-maintained canonical current positions file.

### `config/sources.yaml`

Defines RSS feeds, APIs, transcript sources, and source priorities.

### `config/settings.yaml`

Defines run-time settings such as:

* output base path
* max articles per position
* summarization model
* whether to use local or remote LLM
* alert thresholds
* cache TTL

### `src/positions_loader.py`

Loads and validates `positions.yaml`.

### `src/metadata_lookup.py`

Resolves each ticker into a richer position definition.

### `src/ingestion.py`

Pulls raw data from RSS, APIs, or transcript sources.

### `src/normalization.py`

Converts heterogeneous source data into a common internal schema.

### `src/scoring.py`

Calculates relevance between articles and the enriched daily positions.

### `src/summarizer.py`

Produces concise per-position and whole-portfolio summaries.

### `src/digest_writer.py`

Creates the dated Markdown digest.

### `src/storage.py`

Handles writing dated outputs and structured run artifacts.

### `src/main.py`

Coordinates the complete pipeline.

---

## Minimal Canonical Input: `positions.yaml`

This file should intentionally remain small and easy to edit.

Example:

```yaml
positions:
  - ticker: IBIT
    weight: 0.35
  - ticker: QQQ
    weight: 0.30
  - ticker: CPER
    weight: 0.20
  - ticker: UNG
    weight: 0.15
```

### Validation rules

* `ticker` must be non-empty
* `weight` must be numeric
* weights should sum to approximately 1.0, or the system should at least warn when they do not
* duplicate tickers should be rejected or merged deterministically

---

## Metadata Enrichment Strategy

The daily positions snapshot should include all derived information necessary for ranking and summarization.

### Enriched fields could include

* `ticker`
* `weight`
* `instrument_type`
* `asset_class`
* `sector`
* `subsector`
* `underlying`
* `themes`
* `keywords`
* `macro_sensitivities`
* `related_terms`
* `risk_factors`
* `region`
* `currency`
* `notes`

### Example enriched daily file

```yaml
date: 2026-04-03
positions:
  - ticker: IBIT
    weight: 0.35
    instrument_type: etf
    asset_class: crypto
    sector: digital_assets
    underlying: bitcoin
    themes:
      - bitcoin
      - crypto_flows
      - macro_liquidity
      - risk_on
    keywords:
      - bitcoin
      - btc
      - spot bitcoin etf
      - etf inflows
      - blackrock
      - crypto regulation
    macro_sensitivities:
      - real_yields
      - usd_liquidity
      - risk_appetite
    related_terms:
      - halving
      - miners
      - custody
      - on-chain
    region: us
    currency: usd

  - ticker: QQQ
    weight: 0.30
    instrument_type: etf
    asset_class: equities
    sector: technology
    underlying: nasdaq_100
    themes:
      - mega_cap_tech
      - rates
      - ai
      - growth
    keywords:
      - nasdaq
      - qqq
      - yields
      - fed
      - semiconductors
      - cloud
      - ai capex
    macro_sensitivities:
      - duration
      - policy_rates
      - earnings_growth
    related_terms:
      - magnificent_7
      - valuation
      - guidance
    region: us
    currency: usd
```

---

## How Metadata Should Be Determined

The user asked that the extra fields not live in the canonical positions file and instead be derived from the ticker.

That means the system needs a metadata lookup layer.

### Recommended approach: layered lookup

Use three tiers in this order:

1. **Local metadata registry**
2. **External API lookup**
3. **Rule-based fallback inference**

### 1. Local metadata registry

Maintain a curated local file:

`data/metadata/ticker_metadata.yaml`

This is the most deterministic and the easiest to audit.

Example:

```yaml
IBIT:
  instrument_type: etf
  asset_class: crypto
  sector: digital_assets
  underlying: bitcoin
  themes: [bitcoin, crypto_flows, macro_liquidity, risk_on]
  keywords: [bitcoin, btc, spot bitcoin etf, etf inflows, blackrock]
  macro_sensitivities: [real_yields, usd_liquidity, risk_appetite]

QQQ:
  instrument_type: etf
  asset_class: equities
  sector: technology
  underlying: nasdaq_100
  themes: [mega_cap_tech, rates, ai, growth]
  keywords: [nasdaq, qqq, yields, fed, semiconductors, ai capex]
  macro_sensitivities: [duration, policy_rates, earnings_growth]
```

This gives you exact control over what the relevance engine considers important.

### 2. External API lookup

If a ticker is not found in the local registry, query an external source such as:

* Yahoo Finance style metadata providers
* Financial Modeling Prep
* Alpha Vantage
* Polygon
* Twelve Data
* another market-data endpoint you trust

The purpose of this lookup is not to get everything. It is mainly to determine baseline structure such as:

* security type
* sector
* industry
* fund name
* description
* country
* exchange
* currency

Then a rules engine can derive the rest.

### 3. Rule-based fallback inference

If full metadata is not available, infer from ticker type or name.

Examples:

* if description contains `bitcoin`, add themes like `bitcoin`, `crypto_flows`, `risk_on`
* if ETF tracks Nasdaq, add `mega_cap_tech`, `rates`, `growth`
* if commodity ETF description includes `natural gas`, add `energy`, `weather`, `storage`, `LNG`

This gives you resilience even if the API is incomplete.

---

## Strong Recommendation About Metadata Quality

The lookup should be allowed to use APIs, but the most important semantic fields should eventually be curated locally for positions you actually care about.

Why:

* external metadata is often too generic
* APIs rarely know what *you* consider relevant
* your relevance engine depends heavily on themes and keywords

So the ideal model is:

* API provides baseline metadata
* local registry overrides or augments it
* the enriched daily file records the final merged state for that day

That gives you deterministic history.

---

## Content Sources

You want low-stimulation, high-density inputs.

### Preferred sources

1. RSS feeds from reputable market and macro publishers
2. API-based news headlines and article bodies
3. Official economic calendars and central bank releases
4. ETF flow sources
5. Earnings releases and transcripts
6. Optional YouTube transcript extraction for selected channels only

### Avoid as primary inputs

* raw YouTube watching
* social media feed scrolling
* influencer-first content pipelines
* algorithmic video recommendations

### Example `sources.yaml`

```yaml
rss:
  - name: macro_news
    url: https://example.com/macro.rss
    category: macro
    priority: 10

  - name: tech_markets
    url: https://example.com/tech.rss
    category: equities
    priority: 8

api_news:
  - name: market_news_api
    provider: example_api
    endpoint: https://api.example.com/news
    priority: 9

transcripts:
  - name: selected_youtube_transcripts
    enabled: false
    channels:
      - Example Macro Channel
      - Example Commodities Channel
```

---

## Internal Normalized Article Schema

Every fetched item should be converted into the same internal structure.

Example:

```json
{
  "id": "unique-article-id",
  "source": "macro_news",
  "title": "Fed comments push yields lower",
  "url": "https://example.com/article",
  "published_at": "2026-04-03T06:30:00Z",
  "content": "Full normalized article text here...",
  "summary": null,
  "tokens": ["fed", "yields", "inflation", "rates"],
  "entities": ["Federal Reserve", "US Treasury"],
  "category": "macro"
}
```

This makes downstream ranking and summarization consistent.

---

## Relevance Engine

The relevance engine is the core of the portfolio-driven design.

Its job is to answer:

**How much should this item matter to this portfolio position today?**

### Inputs

* normalized article
* enriched daily positions

### Scoring dimensions

A robust score can include:

1. keyword overlap
2. theme overlap
3. macro sensitivity overlap
4. direct ticker mention
5. source priority
6. recency
7. portfolio weight multiplier

### Example scoring concept

```text
base_score
+ direct_ticker_match_bonus
+ keyword_match_points
+ theme_match_points
+ macro_overlap_points
+ source_priority_bonus
+ recency_bonus
then multiplied by position_weight_factor
```

### Example direct scoring intuition

* an article about ETF inflows mentioning bitcoin, BlackRock, and spot ETF demand should score highly for IBIT
* an article about long-duration valuation compression from rising yields should score highly for QQQ
* a China industrial demand article should score highly for CPER
* a weather and storage article should score highly for UNG

---

## Portfolio-Level Ranking

In addition to per-position scoring, the system should produce a portfolio-level relevance score.

This can be calculated as the weighted sum of the best position-specific matches.

That allows the digest to surface:

* top 3 portfolio-wide items
* top 2 to 5 items per position
* optional contrarian items

---

## Contrarian and Risk Surface Layer

To avoid building an echo chamber, the system should optionally force inclusion of risk-oriented or opposing viewpoints.

### Examples

For each major position, include one high-quality item that is:

* bearish if the position is long
* bullish if the position is short
* focused on structural risk, valuation risk, policy risk, or liquidity risk

This is valuable because a position-aligned filter can otherwise become too self-confirming.

---

## Summarization Layer

Once the top items are ranked, summarize them into calm, decision-useful text.

### Summary goals

Each summary should answer:

* what happened
* why it matters
* which position it affects
* what time horizon it likely matters over
* whether the impact is bullish, bearish, mixed, or neutral
* how confident the system is that this is materially relevant

### Per-item summary template

* Key facts
* Market impact
* Position relevance
* Time horizon
* Confidence

### Per-position rollup template

* Highest priority developments
* Main bullish inputs
* Main bearish inputs
* Net interpretation for today

### Portfolio rollup template

* Biggest global drivers
* Concentration risks
* Cross-position themes
* What most likely matters today vs what is noise

---

## Market Digest Format

The digest should be concise, structured, and easy to read in plain text or Markdown.

### Filename

`market_digest-YYYY-MM-DD.md`

### Suggested structure

```markdown
# Market Digest - 2026-04-03

## Portfolio Overview
- Main themes driving the portfolio today
- Highest impact macro factors
- Key risks to monitor

## Top Portfolio Signals
### 1. Example signal title
- What happened
- Why it matters
- Affected positions: IBIT, QQQ
- Net impact: mixed

## Position Analysis

### IBIT
**Weight:** 0.35
**Underlying:** Bitcoin
**Today's net bias:** Bullish

#### Key items
1. ETF inflows accelerated
2. Real yields softened
3. Regulatory headline was neutral

#### Interpretation
A short paragraph describing the net picture for this position.

#### Risks
- Risk 1
- Risk 2

### QQQ
**Weight:** 0.30
**Underlying:** Nasdaq 100
**Today's net bias:** Mixed

#### Key items
1. Rate pressure increased
2. AI capex narrative remains supportive
3. Valuation commentary turned more cautious

#### Interpretation
A short paragraph describing the net picture for this position.

## Contrarian View
- One or more strong opposing interpretations worth taking seriously

## What Likely Matters Today
- The few things worth remembering

## What Is Probably Noise
- Lower-signal items filtered out by the system
```

---

## Historical Record Strategy

Because you want every daily run preserved, history should be immutable.

That means:

* never overwrite a prior daily positions snapshot
* never overwrite a prior digest
* always write output to the dated analysis directory

### Suggested retention approach

Keep:

* all Markdown digests permanently
* all daily positions snapshots permanently
* raw article data for a limited time if disk matters
* summarized or ranked JSON permanently if desired

This gives you the ability to later answer questions such as:

* what did the system think mattered on a given date?
* how did position metadata evolve over time?
* which themes were emphasized when a thesis changed?

---

## Automation Schedule

The pipeline should run every day automatically.

### Typical options

#### Cron on Linux

Run once every morning, for example at 6:30 AM:

```bash
30 6 * * * /usr/bin/python3 /path/to/market-pipeline/src/main.py >> /path/to/market-pipeline/logs/cron.log 2>&1
```

#### systemd timer

This is cleaner and more observable on Linux than cron if you want retries and journal integration.

#### Windows Task Scheduler

Useful if the environment is Windows-native.

### Recommendation

If this lives on a homelab Linux box, a systemd timer is probably the cleanest.

---

## Run Idempotency and Same-Day Re-Runs

Because the filenames are date-based, you should define what happens if the job runs multiple times on the same day.

### Recommended behavior

Option A: overwrite only the current day’s files inside that day’s directory.

Option B: preserve the first run and also create timestamped rerun variants.

Example:

```text
output/2026-04-03-analysis/
├── market_digest-2026-04-03.md
├── market_digest-2026-04-03-rerun-143500.md
```

Recommended default:

* daily canonical file name for the latest official run
* optional timestamped backup for additional runs

---

## Suggested Execution Model

### Step 1: Read date

Compute `today = YYYY-MM-DD`

### Step 2: Create output directory

Create:

`output/{today}-analysis/`

### Step 3: Load positions

Read `config/positions.yaml`

### Step 4: Enrich positions

For each ticker:

* look in local metadata registry
* if not found, query external provider
* derive missing fields via rules

### Step 5: Write daily positions snapshot

Write:

`output/{today}-analysis/daily-positions-{today}.yaml`

### Step 6: Pull source content

Fetch RSS/API/transcript content for the relevant date window

### Step 7: Normalize content

Map all content into internal article schema

### Step 8: Score content per position

Generate ranked sets

### Step 9: Summarize top items

Create per-item and per-position summaries

### Step 10: Write digest

Write:

`output/{today}-analysis/market_digest-{today}.md`

### Step 11: Write structured artifacts

Write raw and ranked JSON, plus logs

---

## Recommended Supporting Output Files

The digest and daily positions file are the main artifacts, but a few additional files are very useful.

### `raw_articles-YYYY-MM-DD.json`

All fetched normalized source items.

### `ranked_articles-YYYY-MM-DD.json`

The scored and ranked relevance output.

### `summary_payload-YYYY-MM-DD.json`

A machine-readable version of what fed the digest.

### `run_log-YYYY-MM-DD.txt`

Useful for operational debugging.

These make troubleshooting and later extensions much easier.

---

## Example Daily Positions Snapshot Format

```yaml
date: 2026-04-03
generated_from: config/positions.yaml
positions:
  - ticker: IBIT
    weight: 0.35
    instrument_type: etf
    asset_class: crypto
    sector: digital_assets
    underlying: bitcoin
    themes:
      - bitcoin
      - crypto_flows
      - macro_liquidity
      - risk_on
    keywords:
      - bitcoin
      - btc
      - spot bitcoin etf
      - etf inflows
      - blackrock
    macro_sensitivities:
      - real_yields
      - usd_liquidity
      - risk_appetite

  - ticker: QQQ
    weight: 0.30
    instrument_type: etf
    asset_class: equities
    sector: technology
    underlying: nasdaq_100
    themes:
      - mega_cap_tech
      - rates
      - ai
      - growth
    keywords:
      - nasdaq
      - qqq
      - yields
      - fed
      - semiconductors
      - ai capex
    macro_sensitivities:
      - duration
      - policy_rates
      - earnings_growth
```

---

## Example Market Digest Header

```markdown
# Market Digest - 2026-04-03

Generated from: output/2026-04-03-analysis/daily-positions-2026-04-03.yaml
Positions analyzed: 4
Top portfolio themes: macro liquidity, rates, tech leadership, commodity demand
```

---

## Configuration and Extensibility

A clean design keeps user-editable and system-generated data separate.

### Manually edited

* `config/positions.yaml`
* `config/sources.yaml`
* `config/settings.yaml`
* optional local metadata overrides

### Automatically generated

* `daily-positions-YYYY-MM-DD.yaml`
* `market_digest-YYYY-MM-DD.md`
* structured daily analysis files

This makes the system easy to maintain without losing history.

---

## Why the Daily Enriched Snapshot Matters

It may be tempting to derive metadata dynamically every time and only keep the digest, but the daily positions snapshot is important because it preserves the exact interpretation context used for that day’s analysis.

That means if later you refine:

* ticker metadata
* themes
* lookup rules
* source mappings

You still retain what the system believed on prior dates.

That is useful for both debugging and thesis review.

---

## Practical Recommendation on Metadata Storage

Even though the enriched file should be generated from a lookup, you should still maintain a local metadata registry for high-priority symbols.

Reason:

* your real portfolio names are probably a small set
* you care about correct semantics, not generic public metadata
* quality beats fully automatic inference for a position-driven engine

So the best design is:

* `positions.yaml` stays minimal
* `daily-positions-YYYY-MM-DD.yaml` is generated daily
* local registry and API lookup jointly produce enrichment

---

## Suggested First Implementation Scope

A practical v1 should support:

1. `positions.yaml` as minimal input
2. local metadata registry for known tickers
3. optional API lookup for unknown tickers
4. RSS ingestion
5. normalized JSON storage
6. relevance scoring
7. Markdown digest generation
8. daily dated output directories
9. cron or systemd daily automation

That is enough to replace a large amount of passive video consumption with a calm daily process.

---

## Future Enhancements

Once the core is stable, possible additions include:

* email delivery of the daily digest
* plain-text mobile digest
* text-to-speech generation for walking audio
* portfolio-specific alerts for only the highest-signal changes
* thesis tracking over time
* comparison of predicted relevance versus realized market moves
* a small dashboard that reads historical `market_digest-YYYY-MM-DD.md` files

---

## Bottom-Line Recommended Specification

The full system should work like this:

* You maintain one file: `config/positions.yaml`
* Each day, the system reads that file
* It resolves each ticker into richer metadata using a lookup layer
* It writes a dated expanded snapshot to `daily-positions-YYYY-MM-DD.yaml`
* It fetches relevant market information
* It ranks relevance according to the enriched daily positions
* It writes a dated digest to `market_digest-YYYY-MM-DD.md`
* It stores everything inside `output/YYYY-MM-DD-analysis/`
* It runs automatically every day
* It creates a permanent historical archive of both the analysis and the exact position interpretation used that day

That gives you a calmer, more deterministic, and more auditable way to stay informed without relying on high-stimulation visual media.
