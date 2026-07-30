"""
Tests for src/rebuild_dashboard_daily.py.

Covers the regression this module previously had: historical_scores.csv rows
were dropped via `df.dropna(subset=['us_score'], how='any')`, which discarded
valid TW-only rows on US-only market holidays (e.g. Juneteenth, July 4th —
days TWSE trades but NYSE is closed). The fix only drops a row when ALL
`*_score` columns are NaN.

NOTE: rebuild_dashboard_daily.py reassigns sys.stdout at import time
(`sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`), which corrupts
pytest's global capture buffer if the module is imported in-process. To
avoid that, this test drives the module in a subprocess (same isolation
pattern test_validation_gate.py uses for scripts/audit_history.py) and
exchanges data via temp files instead of importing the module directly.

Run with: pytest tests/test_rebuild_dashboard.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
_DRIVER = """
import sys, os, json
sys.path.insert(0, {repo_root!r})
sys.path.insert(0, os.path.join({repo_root!r}, "src"))

import pandas as pd
import yfinance


def _fake_download(ticker, start=None, end=None, progress=False, auto_adjust=True):
    idx = pd.date_range("2026-06-01", periods=5, freq="D")
    return pd.DataFrame({{"Close": [100.0, 101.0, 102.0, 103.0, 104.0]}}, index=idx)


yfinance.download = _fake_download

from src import rebuild_dashboard_daily as rdd

rdd.DATA_DIR = {data_dir!r}
rdd.main()

with open(os.path.join({data_dir!r}, "dashboard_data.json"), encoding="utf-8") as f:
    d = json.load(f)

with open({result_path!r}, "w", encoding="utf-8") as f:
    json.dump({{"dates": d["dates"], "tw_score": d["tw_score"], "us_score": d["us_score"]}}, f)
"""

def _run_rebuild(tmp_path, csv_body: str) -> dict:
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    (data_dir / "historical_scores.csv").write_text(csv_body, encoding="utf-8")
    (data_dir / "dashboard_data.json").write_text(json.dumps({"snapshot": {}}), encoding="utf-8")

    result_path = tmp_path / "result.json"
    driver = _DRIVER.format(repo_root=os.path.abspath(REPO_ROOT), data_dir=str(data_dir), result_path=str(result_path))

    proc = subprocess.run(
        [sys.executable, "-c", driver],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"rebuild_dashboard_daily.main() failed:\n{proc.stdout}\n{proc.stderr}"

    return json.loads(result_path.read_text(encoding="utf-8"))

def test_tw_only_row_not_dropped_on_us_holiday(tmp_path) -> None:
    """A row where us_score is blank (US market closed, e.g. Juneteenth) but
    tw_score has a value (TW market open) must survive the CSV load/filter
    step and appear in the rebuilt dashboard_data.json."""
    result = _run_rebuild(
        tmp_path,
        "date,us_score,tw_score,divergence\n"
        "2026-06-17,60.0,55.0,5.0\n"
        "2026-06-18,61.0,56.0,5.0\n"
        "2026-06-19,,57.0,\n"
        "2026-06-22,62.0,58.0,4.0\n",
    )

    assert "2026-06-19" in result["dates"], (
        "TW-only row (US holiday) was dropped from dashboard_data.json — "
        "this is the exact regression the dropna(subset=score_cols, how='all') "
        "fix addresses."
    )
    idx = result["dates"].index("2026-06-19")
    assert result["tw_score"][idx] == 57.0
    assert result["us_score"][idx] is None


def test_row_dropped_only_when_all_scores_nan(tmp_path) -> None:
    """A row where BOTH us_score and tw_score are blank (e.g. a shared
    holiday, or a data gap) should still be dropped."""
    result = _run_rebuild(
        tmp_path,
        "date,us_score,tw_score,divergence\n"
        "2026-06-17,60.0,55.0,5.0\n"
        "2026-06-18,,,\n"
        "2026-06-19,62.0,58.0,4.0\n",
    )

    assert "2026-06-18" not in result["dates"]
    assert "2026-06-17" in result["dates"]
    assert "2026-06-19" in result["dates"]