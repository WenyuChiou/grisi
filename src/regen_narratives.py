#!/usr/bin/env python3
"""Regenerate tw_agent / us_agent narratives via Claude API.

Designed to run inside GitHub Actions after daily_update.py + rebuild_dashboard
have written fresh scores. Reads data/, calls the Anthropic API, parses JSON,
writes narrative fields back to data/phase2_agent_results.json.

Replaces the cowork-trigger approach which was silent and unobservable.

Required env: ANTHROPIC_API_KEY
"""
import csv
import json
import os
import re
import sys
from pathlib import Path

from anthropic import Anthropic

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
MODEL = "claude-opus-4-6"


def load_context() -> dict:
    with open(DATA_DIR / "phase2_agent_results.json", encoding="utf-8") as f:
        p2 = json.load(f)
    with open(DATA_DIR / "snapshot_latest.json", encoding="utf-8") as f:
        snap = json.load(f)
    with open(DATA_DIR / "action_thresholds.json", encoding="utf-8") as f:
        thresholds = json.load(f)
    with open(DATA_DIR / "historical_scores.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    hist_recent = rows[-15:]
    return {
        "p2": p2,
        "snap": snap,
        "thresholds": thresholds,
        "hist": hist_recent,
    }


def build_prompt(ctx: dict) -> str:
    p2 = ctx["p2"]
    snap = ctx["snap"]
    hist = ctx["hist"]

    us_score = p2.get("us_base_score")
    tw_score = p2.get("tw_base_score")
    tw_thresh = p2.get("tw_agent", {}).get("action_thresholds", {})
    us_thresh = p2.get("us_agent", {}).get("action_thresholds", {})
    us_market = snap.get("us_market", {})
    tw_market = snap.get("tw_market", {})
    tw_retail = snap.get("tw_retail_indicators", {})
    global_ctx = snap.get("global_context", {})

    yesterday_us = (
        p2.get("us_agent", {}).get("forward_outlook", "")[:200]
    )
    yesterday_tw = (
        p2.get("tw_agent", {}).get("forward_outlook", "")[:200]
    )

    return f"""You are regenerating MoodRing daily Chinese narratives for two market agents (TW + US). Today's scores and indicators are below. Output a single JSON object — no prose, no code fence, no preamble.

# Current state
- date: {snap.get('date')}
- us_base_score: {us_score}
- tw_base_score: {tw_score}
- us_action_thresholds: {us_thresh}
- tw_action_thresholds: {tw_thresh}

# US market data
{json.dumps(us_market, ensure_ascii=False, indent=2)}

# TW market data
{json.dumps(tw_market, ensure_ascii=False, indent=2)}

# TW retail indicators
{json.dumps(tw_retail, ensure_ascii=False, indent=2)}

# Global context
{json.dumps(global_ctx, ensure_ascii=False, indent=2)}

# Last 15 days of scores (for delta_5d)
{json.dumps(hist, ensure_ascii=False, indent=2)}

# Yesterday's narratives (DO NOT copy — vary phrasing)
us_agent.forward_outlook: {yesterday_us}
tw_agent.forward_outlook: {yesterday_tw}

# Output schema
Respond with ONLY a valid JSON object matching this schema:
{{
  "tw_agent": {{
    "narrative_tw": "100-180 字, 第一人稱散戶口吻, must contain 'Moodring {tw_score}' and cite >=2 real indicators",
    "watch_for_tw": "50-100 字, 第三人稱觀察, specific + falsifiable",
    "forward_outlook": "80-150 字, 前瞻口吻, MUST contain '{tw_score}' string, cite P80={tw_thresh.get('p80')}",
    "key_factors_tw": ["bullet 1 with real indicator", "bullet 2", "bullet 3"],
    "what_quant_misses": "1-3 sentences naming a non-quant signal the model misses",
    "cross_market_summary": "one line referencing current score and top 2 indicators"
  }},
  "us_agent": {{
    "narrative_tw": "100-180 字, 第一人稱散戶口吻, must contain 'Moodring {us_score}' and cite >=2 real indicators (US voice may use WSB / Robinhood / put-call / diamond hands flavoring when level warrants)",
    "watch_for_tw": "50-100 字, 第三人稱觀察, specific + falsifiable",
    "forward_outlook": "80-150 字, 前瞻口吻, MUST contain '{us_score}' string, cite P80={us_thresh.get('p80')}",
    "key_factors_tw": ["bullet 1 with real indicator", "bullet 2", "bullet 3"],
    "what_quant_misses": "1-3 sentences naming a non-quant signal the model misses",
    "cross_market_summary": "one line referencing current score and top 2 indicators"
  }}
}}

# Tone bucket (use action_thresholds to decide)
- score <= P20: 恐慌懷疑, "又崩了..."
- P20-P40: 觀望不安, "盤整不敢動..."
- P40-P60: 中性理性, "看戲為主..."
- P60-P80: FOMO 加熱, "熱度上來了..."
- score > P80: 亢奮但保留警覺, "Moodring 衝到 X.X，P80 才 Y.Y，知道要冷靜但..."

# Banned phrases — rewrite any sentence containing these
- AI 腔: 值得注意的是 / 發揮關鍵作用 / 綜上所述 / 在當前市場環境下 / 不容忽視 / 密切關注 / 持續觀察後續發展
- GPT 直譯: 深入探討 / 利用 / 關鍵 / 穩健 / 全面 / 投資人應保持謹慎
- Vague hedges without numbers: 顯著 / 明顯 / 大幅 (unless followed by a real number)

# Delta-5d rule
If abs(today_score - score 5 trading days ago) > 3, narrative_tw MUST explicitly name the direction and magnitude.

# Critical
- forward_outlook MUST literally contain the score number formatted as one decimal (e.g. '81.8'), or the audit will fail.
- narrative_tw MUST contain the literal string 'Moodring X.X' where X.X is today's score.
- Output ONLY the JSON object. No markdown fence, no commentary.
"""


def call_claude(prompt: str) -> dict:
    client = Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    # Strip optional ```json fences if Claude added them despite instructions
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    # Find first { and last } as a fallback
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def apply_narratives(p2: dict, gen: dict) -> dict:
    for agent_key in ("tw_agent", "us_agent"):
        agent = p2.setdefault(agent_key, {})
        fields = gen.get(agent_key, {}) or {}
        for field, value in fields.items():
            agent[field] = value
        # Mirror narrative_tw to narrative (dashboard fallback)
        if "narrative_tw" in fields:
            agent["narrative"] = fields["narrative_tw"]
        # Mirror key_factors_tw to key_factors
        if "key_factors_tw" in fields:
            agent["key_factors"] = fields["key_factors_tw"]
    return p2


def validate(p2: dict) -> None:
    """Hard checks that mirror the audit_narrative + Step 6e gate."""
    errors = []
    for agent_key in ("tw_agent", "us_agent"):
        score_key = f"{agent_key[:2]}_base_score"
        score = p2.get(score_key)
        if score is None:
            continue
        score_str = f"{float(score):.1f}"
        agent = p2.get(agent_key, {}) or {}
        fo = agent.get("forward_outlook", "") or ""
        narr = agent.get("narrative_tw", "") or ""
        if score_str not in fo:
            errors.append(
                f"{agent_key}.forward_outlook missing literal score {score_str!r}: {fo[:80]!r}"
            )
        if f"Moodring {score_str}" not in narr:
            errors.append(
                f"{agent_key}.narrative_tw missing literal 'Moodring {score_str}': {narr[:80]!r}"
            )
    if errors:
        raise SystemExit(
            "[NARRATIVE] validation failed:\n  " + "\n  ".join(errors)
        )


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[NARRATIVE] ANTHROPIC_API_KEY not set — skipping", file=sys.stderr)
        return 0
    ctx = load_context()
    prompt = build_prompt(ctx)
    print(f"[NARRATIVE] calling {MODEL}, prompt={len(prompt)} chars")
    gen = call_claude(prompt)
    p2 = apply_narratives(ctx["p2"], gen)
    validate(p2)
    out_path = DATA_DIR / "phase2_agent_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(p2, f, ensure_ascii=False, indent=2)
    print(
        f"[NARRATIVE] regenerated for us={p2.get('us_base_score')} "
        f"tw={p2.get('tw_base_score')} -> {out_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
