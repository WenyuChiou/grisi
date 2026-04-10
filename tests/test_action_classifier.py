from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pandas as pd

from src.action_classifier import classify_action, compute_action_thresholds, write_thresholds_json


def _write_history_csv(path: Path, scores: list[float | None], market: str = "tw") -> None:
    df = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=len(scores), freq="D"),
            "us_score": [None] * len(scores),
            "tw_score": [None] * len(scores),
            "divergence": [None] * len(scores),
        }
    )
    df[f"{market}_score"] = scores
    df.to_csv(path, index=False)


def _make_workspace_dir() -> Path:
    path = Path("tests") / f"_tmp_action_classifier_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_compute_action_thresholds_happy_path() -> None:
    temp_dir = _make_workspace_dir()
    try:
        csv_path = temp_dir / "historical_scores.csv"
        scores = [round(i * 100 / 251, 6) for i in range(252)]
        _write_history_csv(csv_path, scores, market="tw")

        thresholds = compute_action_thresholds("tw", history_csv=str(csv_path))

        assert thresholds["fallback"] is False
        assert thresholds["sample_size"] == 252
        assert thresholds["p20"] == 20.0
        assert thresholds["p40"] == 40.0
        assert thresholds["p60"] == 60.0
        assert thresholds["p80"] == 80.0
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_compute_action_thresholds_cold_start_fallback() -> None:
    temp_dir = _make_workspace_dir()
    try:
        csv_path = temp_dir / "historical_scores.csv"
        _write_history_csv(csv_path, list(range(30)), market="tw")

        thresholds = compute_action_thresholds("tw", history_csv=str(csv_path))

        assert thresholds["fallback"] is True
        assert thresholds["sample_size"] == 30
        assert thresholds["p20"] == 25.0
        assert thresholds["p40"] == 40.0
        assert thresholds["p60"] == 55.0
        assert thresholds["p80"] == 75.0
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_compute_action_thresholds_filters_null_rows() -> None:
    temp_dir = _make_workspace_dir()
    try:
        csv_path = temp_dir / "historical_scores.csv"
        scores = [10.0, None, 20.0, None, 30.0, 40.0, None, 50.0] * 32
        _write_history_csv(csv_path, scores[:252], market="tw")

        thresholds = compute_action_thresholds("tw", history_csv=str(csv_path))

        assert thresholds["sample_size"] == 157
        assert thresholds["fallback"] is False
        assert thresholds["p20"] == 12.0
        assert thresholds["p40"] == 20.0
        assert thresholds["p60"] == 30.0
        assert thresholds["p80"] == 40.0
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_classify_action_boundaries() -> None:
    thresholds = {"p20": 20.0, "p40": 40.0, "p60": 60.0, "p80": 80.0}

    assert classify_action(20.0, thresholds) == "積極加碼"
    assert classify_action(20.1, thresholds) == "逢低布局"
    assert classify_action(None, thresholds) == "觀望持有"


def test_distribution_sanity_real_tw_history() -> None:
    thresholds = compute_action_thresholds("tw")
    df = pd.read_csv("data/historical_scores.csv")
    scores = pd.to_numeric(df["tw_score"], errors="coerce").dropna().tail(252)

    counts: dict[str, int] = {}
    for score in scores:
        action = classify_action(float(score), thresholds)
        counts[action] = counts.get(action, 0) + 1

    assert max(counts.values()) / sum(counts.values()) < 0.6


def test_write_thresholds_json_atomic_shape() -> None:
    temp_dir = _make_workspace_dir()
    try:
        out_path = temp_dir / "action_thresholds.json"
        thresholds = {"tw": compute_action_thresholds("tw")}

        write_thresholds_json(thresholds, out_path=str(out_path))

        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert "generated_at" in payload
        assert payload["tw"]["market"] == "tw"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
