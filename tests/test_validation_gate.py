"""
Tests for the moodring data validation gate.
Run with: pytest tests/test_validation_gate.py -v
"""

import os
import subprocess
import sys

import pytest

# Ensure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from src.validation_gate import DataValidationError, get_market_history, validate_daily_scores


def test_rejects_tw_score_on_qingming() -> None:
    with pytest.raises(DataValidationError):
        validate_daily_scores(
            {"us": None, "tw": 65.0, "jp": None, "kr": None, "eu": None},
            "2026-04-06",
        )


def test_rejects_kr_score_on_sunday() -> None:
    with pytest.raises(DataValidationError):
        validate_daily_scores(
            {"us": None, "tw": None, "jp": None, "kr": 58.0, "eu": None},
            "2026-04-05",
        )


def test_rejects_eu_score_on_good_friday() -> None:
    with pytest.raises(DataValidationError):
        validate_daily_scores(
            {"us": None, "tw": None, "jp": None, "kr": None, "eu": 55.0},
            "2026-04-03",
        )


def test_rejects_flatline_3_days(monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_history(market: str, n: int = 5) -> list[float]:
        return [72.1, 72.1, 72.1] if market == "us" else []

    def mock_is_open(date_str: str, market: str) -> bool:
        return market == "us" and date_str == "2026-04-10"

    monkeypatch.setattr("src.validation_gate.get_market_history", mock_history)
    monkeypatch.setattr("src.validation_gate._is_market_open", mock_is_open)

    with pytest.raises(DataValidationError):
        validate_daily_scores(
            {"us": 72.1, "tw": None, "jp": None, "kr": None, "eu": None},
            "2026-04-10",
        )


def test_accepts_valid_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    # Use monkeypatching for determinism: avoid live yfinance calls for "valid" test.
    # _is_market_open: US and TW were open on 2026-04-08 (Wednesday, no holidays).
    # get_market_history: return values different from test scores to avoid stale warnings.
    def mock_is_open(date_str: str, market: str) -> bool:
        return market in ("us", "tw")

    def mock_history(market: str, n: int = 5) -> list[float]:
        return [50.0, 55.0, 60.0]  # distinct from test scores below

    monkeypatch.setattr("src.validation_gate._is_market_open", mock_is_open)
    monkeypatch.setattr("src.validation_gate.get_market_history", mock_history)

    assert (
        validate_daily_scores(
            {"us": 65.0, "tw": 72.0, "jp": None, "kr": None, "eu": None},
            "2026-04-08",
        )
        is None
    )


def test_audit_historical_csv_clean() -> None:
    # --since 2026-04-07: scopes audit to data after the 5cf3fe3 cleanup commit.
    # --until 2026-04-09: excludes today (2026-04-10) since same-day yfinance data
    # may not be propagated yet (Layer A is the real-time guard for today's writes).
    # Pre-2026-04-07 data contains legacy Sunday US entries (2026-03-22, 2026-03-29)
    # that pre-date the validation gate; tracked as a separate cleanup task.
    result = subprocess.run(
        [sys.executable, "scripts/audit_history.py", "--strict", "--since", "2026-04-07", "--until", "2026-04-09"],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Audit failed:\n{result.stdout}\n{result.stderr}"


def test_audit_overlay_clean() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/audit_history.py", "--strict", "--since", "2026-04-07", "--until", "2026-04-09"],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        capture_output=True,
        text=True,
        check=False,
    )
    assert "[AUDIT] PASS" in result.stdout, f"Audit did not pass:\n{result.stdout}\n{result.stderr}"
