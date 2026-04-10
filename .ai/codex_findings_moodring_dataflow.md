# Moodring Data-Flow Audit — Codex Findings

## Critical Issues
### C1: Scheduled runs are using the wrong trading-day boundary, so US data is stale/misdated by design
- File: `.github/workflows/daily-update.yml:6`, `.github/workflows/daily-update.yml:7`, `src/daily_update.py:391`, `src/daily_update.py:433`, `src/daily_update.py:436`, `src/daily_update.py:821`, `src/daily_update.py:2589`
- Severity: Critical
- Description: The workflow claims the `22:00 Taipei` run is "after US close", but the job actually runs at `14:00 UTC`, which is before the US cash close; both scheduled runs then use `datetime.now().strftime('%Y-%m-%d')` as `today`, so US fetch/open detection compares against the runner's current date instead of the US market session date and can append a carry-forward US score under the wrong day.
- Root-cause hypothesis: Scheduling assumptions were written in Taipei terms, but the code uses naive server-local dates instead of market-local trading dates and close cutoffs.

### C2: `docs/data` is not a source of truth, so docs-only hotfixes are guaranteed to be overwritten by the next pipeline run
- File: `src/fix_flatline_20260410.py:19`, `src/fix_flatline_20260410.py:20`, `src/fix_flatline_20260410.py:27`, `src/fix_flatline_20260410.py:67`, `src/daily_update.py:2695`, `src/daily_update.py:2708`, `src/rebuild_dashboard_daily.py:176`, `src/rebuild_dashboard_daily.py:194`, `.github/workflows/daily-update.yml:32`, `.github/workflows/daily-update.yml:35`
- Severity: Critical
- Description: The ad-hoc repair script edits `docs/data/*` directly, but the daily pipeline republishes from `data/*` into `docs/data/*` in both Python scripts and again in the workflow, so any targeted fix applied only to the published mirror will disappear on the next run.
- Root-cause hypothesis: The repo maintains two writable copies of the same datasets and the repair path edits the mirror instead of the canonical upstream file.

## High Issues
### H1: Selective `--us` / `--tw` runs corrupt shared US/TW files
- File: `src/daily_update.py:811`, `src/daily_update.py:852`, `src/daily_update.py:855`, `src/daily_update.py:909`, `src/daily_update.py:960`, `src/daily_update.py:963`, `src/daily_update.py:965`, `src/daily_update.py:2553`, `src/daily_update.py:2558`
- Severity: High
- Description: The CLI advertises per-market runs, but `append_scores_to_csv()` backfills the non-requested US/TW column from the previous row or `50`, and `update_overlay_json()` appends the shared `dates` array only when `us_score` is present, so `--tw` and `--us` can silently create fake carry-forwards or `dates`/`tw_score` length mismatches.
- Root-cause hypothesis: The writer logic assumes US and TW are updated together even though the public CLI exposes independent market modes.

### H2: `rebuild_dashboard_daily.py` is the last writer for `dashboard_data.json` and drops TW-only dates when US is blank
- File: `src/daily_update.py:875`, `src/daily_update.py:903`, `src/rebuild_dashboard_daily.py:84`, `src/rebuild_dashboard_daily.py:86`, `src/rebuild_dashboard_daily.py:88`, `src/rebuild_dashboard_daily.py:125`, `src/rebuild_dashboard_daily.py:152`, `.github/workflows/daily-update.yml:29`, `.github/workflows/daily-update.yml:30`
- Severity: High
- Description: The daily workflow rewrites `dashboard_data.json` twice, and the second writer rebuilds US/TW arrays from `historical_scores.csv` after dropping every row with `us_score` missing, which means legitimate TW-only dates are removed even if `daily_update.py` just produced a fresher dashboard snapshot.
- Root-cause hypothesis: The rebuild step treats US availability as the gating date universe and reconstructs the whole file instead of preserving the shared chronology written earlier in the run.

### H3: The workflow has no concurrency guard, but the scripts perform whole-file read/modify/write cycles on canonical data
- File: `.github/workflows/daily-update.yml:13`, `src/daily_update.py:825`, `src/daily_update.py:869`, `src/daily_update.py:879`, `src/daily_update.py:903`, `src/daily_update.py:927`, `src/daily_update.py:1008`, `src/rebuild_dashboard_daily.py:49`, `src/rebuild_dashboard_daily.py:152`
- Severity: High
- Description: There is no `concurrency` mutex in GitHub Actions, and the pipeline rewrites `historical_scores.csv`, `dashboard_data.json`, `overlay_data.json`, and `forward_outlook.json` by loading the whole file, mutating it in memory, then overwriting the same path, so overlapping scheduled/manual runs can lose updates or publish whichever stale snapshot finishes last.
- Root-cause hypothesis: The pipeline relies on serial execution but neither the workflow nor the file writes enforce that assumption.

### H4: The final docs publish step is redundant and can clobber earlier sync results with a different file set
- File: `src/daily_update.py:2695`, `src/daily_update.py:2713`, `src/rebuild_dashboard_daily.py:176`, `src/rebuild_dashboard_daily.py:194`, `.github/workflows/daily-update.yml:32`, `.github/workflows/daily-update.yml:35`
- Severity: High
- Description: `daily_update.py` already syncs JSON, CSV, and snapshots to `docs/data`, `rebuild_dashboard_daily.py` syncs a similar subset again, and the workflow then runs `cp data/*.json docs/data/`, which re-overwrites JSON only; the last published state therefore comes from mixed writers and can wipe docs-only JSON fixes while leaving CSVs/snapshots on a different write path.
- Root-cause hypothesis: Publishing responsibility is split across three independent sync mechanisms that do not share the same file list or ownership model.

## Medium Issues
### M1: The 09:30 Taipei run has no safeguard against partial same-day Asia daily bars
- File: `.github/workflows/daily-update.yml:7`, `src/daily_update.py:558`, `src/daily_update.py:561`, `src/daily_update.py:603`, `src/daily_update.py:606`, `src/daily_update.py:656`, `src/daily_update.py:659`, `src/daily_update.py:709`, `src/daily_update.py:712`
- Severity: Medium
- Description: For TW/JP/KR/EU, the code considers the market "open" whenever today's date appears in the downloaded daily series and then uses the last row as a final daily close, but the workflow explicitly runs during the Asian/European trading day rather than after those markets have settled.
- Root-cause hypothesis: The code conflates "today exists in the daily feed" with "today's daily candle is finalized."

### M2: Taiwan retail data can fail partially and still be written without any hard failure or data-quality flag
- File: `src/daily_update.py:487`, `src/daily_update.py:555`, `src/daily_update.py:556`, `src/daily_update.py:2660`, `src/daily_update.py:2667`
- Severity: Medium
- Description: All FinMind collection for Taiwan is wrapped in one broad `try/except`, so rate-limit/schema/API failures only log `[TW] FinMind partial error` and the pipeline continues to publish snapshots, narratives, and agent outputs with missing retail inputs.
- Root-cause hypothesis: The TW retail fetch path was designed for availability-first behavior and never promotes incomplete external data to a write-blocking condition.

### M3: Empty/stale yfinance handling is inconsistent, and the shared validator is not used by the main fetch path
- File: `src/daily_update.py:102`, `src/daily_update.py:149`, `src/daily_update.py:412`, `src/daily_update.py:417`, `src/daily_update.py:468`, `src/daily_update.py:472`, `src/daily_update.py:592`, `src/daily_update.py:595`, `src/daily_update.py:633`, `src/daily_update.py:645`, `src/daily_update.py:698`, `src/daily_update.py:701`
- Severity: Medium
- Description: `validate_market_open()` has more defensive stale-date logic but is never used by the scheduled pipeline, while US/TW/JP/EU fetchers immediately dereference `iloc[-1]` on downloaded series and only KR has a market-specific fallback, so provider glitches are handled inconsistently across markets.
- Root-cause hypothesis: Reliability fixes were added piecemeal per market instead of via one shared validation path.

### M4: `forward_outlook.json` has two writers in the daily workflow and the last writer only patches part of the file
- File: `src/daily_update.py:1982`, `src/daily_update.py:2072`, `src/rebuild_dashboard_daily.py:62`, `src/rebuild_dashboard_daily.py:74`, `.github/workflows/daily-update.yml:27`, `.github/workflows/daily-update.yml:30`
- Severity: Medium
- Description: `daily_update.py` writes `forward_outlook.json` from live compute/agent state, then `rebuild_dashboard_daily.py` reopens the existing file and patches only US/TW current scores from `phase2_agent_results.json`, making the final artifact dependent on cross-file sequencing instead of one authoritative builder.
- Root-cause hypothesis: The rebuild step was retrofitted to "fix up" a downstream file rather than regenerate it from a complete in-memory model.

## Low / Observations
### L1: `clean_holiday_anomalies()` is currently ordered safely, but it remains another writer to the same canonical files
- File: `src/daily_update.py:203`, `src/daily_update.py:306`, `src/daily_update.py:370`, `src/daily_update.py:2576`, `src/daily_update.py:2584`
- Severity: Low
- Description: In the current `main()` flow, `--clean` runs before any append/update writers and therefore does not presently overwrite freshly appended data, but it still shares ownership of `overlay_data.json` and `historical_scores.csv` with normal daily writers.
- Root-cause hypothesis: The cleanup path is now guarded by call order rather than by isolated write targets or transactional updates.

### L2: Multiple-writer file map shows several datasets lack a single authoritative builder
- File: `src/daily_update.py:811`, `src/daily_update.py:875`, `src/daily_update.py:909`, `src/daily_update.py:1786`, `src/daily_update.py:1982`, `src/daily_update.py:2077`, `src/daily_update.py:2316`, `src/rebuild_dashboard_daily.py:39`, `src/fix_flatline_20260410.py:26`, `src/recalibrate.py:450`
- Severity: Low
- Description: More-than-one-writer files are: `data/historical_scores.csv` (`clean_holiday_anomalies`, `append_scores_to_csv`), `data/dashboard_data.json` (`update_dashboard_json`, `rebuild_dashboard_daily.py`), `data/overlay_data.json` (`clean_holiday_anomalies`, `update_overlay_json`), `data/forward_outlook.json` (`update_forward_outlook`, `rebuild_dashboard_daily.py`), `data/self_improve.json` (`generate_self_improve`, `recalibrate.py`), plus the mirrored `docs/data/*` copies that are written again by both Python sync code and the workflow.
- Root-cause hypothesis: The repo evolved by layering repair/rebuild/sync scripts on top of the original pipeline instead of centralizing ownership per artifact.

## Summary Table
| ID | Title | Severity | File:Line |
|----|-------|----------|-----------|
| C1 | Scheduled runs use the wrong trading-day boundary | Critical | `.github/workflows/daily-update.yml:6` |
| C2 | `docs/data` hotfixes are overwritten by the next pipeline run | Critical | `src/fix_flatline_20260410.py:19` |
| H1 | Selective `--us` / `--tw` runs corrupt shared US/TW files | High | `src/daily_update.py:811` |
| H2 | Rebuild drops TW-only dates and overwrites fresh dashboard output | High | `src/rebuild_dashboard_daily.py:84` |
| H3 | No workflow concurrency guard around whole-file rewrites | High | `.github/workflows/daily-update.yml:13` |
| H4 | Redundant docs sync clobbers earlier publish results | High | `.github/workflows/daily-update.yml:32` |
| M1 | 09:30 Taipei run can use partial same-day Asia daily bars | Medium | `.github/workflows/daily-update.yml:7` |
| M2 | FinMind failures are swallowed and partial TW data is still published | Medium | `src/daily_update.py:487` |
| M3 | yfinance empty/stale handling is inconsistent across markets | Medium | `src/daily_update.py:102` |
| M4 | `forward_outlook.json` has two writers in one workflow | Medium | `src/daily_update.py:1982` |
| L1 | `clean_holiday_anomalies()` is ordered safely today but remains a shared writer | Low | `src/daily_update.py:2576` |
| L2 | Writer map shows several artifacts still lack one authoritative builder | Low | `src/daily_update.py:811` |
