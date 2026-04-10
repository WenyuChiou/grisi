# Task: Moodring Data-Flow Update Path Audit

## Goal
Trace the full data-flow update path in the moodring repo and produce a ranked list of bugs, risks, and hidden issues. This is a **diagnostic-only** audit — do NOT fix anything. Report findings with file:line references and severity (Critical / High / Medium / Low).

## Background
This repo runs a daily pipeline that fetches market data, computes sentiment scores, and publishes JSON files to GitHub Pages (docs/data/). We just fixed one write-ordering bug (overlay_data.json missing 04-10 due to a merge conflict between a hotfix branch and the daily-run branch). We want to know if there are other hidden issues.

## Files to Audit
Read these files in full:
- `src/daily_update.py` — main pipeline (2700+ lines)
- `src/rebuild_dashboard_daily.py` — secondary pipeline
- `.github/workflows/daily-update.yml` — orchestration/ordering
- `src/fix_flatline_20260410.py` — ad-hoc repair script (study this as an example of what goes wrong)

Also read (for data shape reference, don't analyze deeply):
- `data/overlay_data.json` — check key arrays and their last few entries
- `data/historical_scores.csv` — check last 10 rows

## Audit Checklist

For each item, provide: description, file:line, severity, and a 1-sentence root-cause hypothesis.

### A. Write-ordering bugs
- Are there multiple code paths that write to the same file without coordination?
- Could `rebuild_dashboard_daily.py` overwrite data that `daily_update.py` just wrote?
- Does the `cp data/*.json docs/data/` step in the workflow clobber any targeted fixes?
- Is `clean_holiday_anomalies()` ever called in a position where it could overwrite freshly-appended data?
- Are there any other functions that write then re-read the same file in a non-atomic way?

### B. In-memory vs on-disk divergence
- Are there functions that load a file into memory at the top, do work, then call another function that also loads the same file — potentially with different data?
- Is there any global mutable state (module-level dict or list) that accumulates between calls and could become stale?

### C. Silent failure paths (bad data still written)
- For each fetcher (fetch_us_data, fetch_tw_data, fetch_jp_data, fetch_kr_data, fetch_eu_data): what happens if the yfinance download returns empty/None/stale data? Does the pipeline still write? Does it write None, carry-forward, or skip?
- Does `market_open=False` detection work correctly for all 5 markets, or does it use the same pattern as the pre-fix TW/JP code (iloc[-1] on a potentially empty DataFrame)?
- Are there any try/except blocks that swallow exceptions silently and let the pipeline continue with corrupted state?

### D. Data files with multiple writers / no single source of truth
- List every file in data/ and docs/data/ that is written by MORE THAN ONE script.
- For each such file, identify which script is the "last writer" in the workflow and whether that last writer has access to all the data it needs.

### E. Timezone / date-boundary bugs
- The pipeline runs at 22:00 Taipei (UTC+8) and also at 09:30 Taipei. Could a run near midnight produce a wrong `today` date?
- For Asian markets (TW, JP, KR), is `today` computed correctly relative to the market's local timezone?
- Could a run at 09:30 Taipei (before US close) produce a wrong/stale US score? What safeguards exist?

### F. Git push / deploy risks
- The GH Actions workflow does `git add data/ docs/data/ src/` — could this accidentally include partially-written or corrupt files?
- Is there a race condition where two workflow runs execute simultaneously (GitHub Actions has no mutex by default)?
- If `rebuild_dashboard_daily.py` fails (network error, yfinance down), does the workflow still push partial data?

## Output Format
Save your findings to `.ai/codex_findings_moodring_dataflow.md` with this structure:

```
# Moodring Data-Flow Audit — Codex Findings

## Critical Issues
### C1: [title]
- File: src/daily_update.py:LINE
- Severity: Critical
- Description: ...
- Root-cause hypothesis: ...

## High Issues
### H1: [title]
...

## Medium Issues
...

## Low / Observations
...

## Summary Table
| ID | Title | Severity | File:Line |
|----|-------|----------|-----------|
...
```

## Instructions
1. Read all files listed above.
2. Work through checklist A through F systematically.
3. Write findings to `.ai/codex_findings_moodring_dataflow.md`.
4. Do NOT modify any source files — this is read-only audit.
5. Focus on facts from the code. Do not invent issues that aren't there.
