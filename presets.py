"""
presets.py — 전략 파라미터 프리셋 저장/불러오기
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import streamlit as st

PRESETS_DIR = Path(__file__).parent / "saves" / "presets"

# ── 전략 파라미터 키 목록 (날짜/자본금 제외) ──────────────────────
PARAM_KEYS: list[str] = [
    "rally_thresh_1", "rally_thresh_2", "rally_thresh_3",
    "rally_sell_pct_1", "rally_sell_pct_2", "rally_sell_pct_3",
    "rally_sell_to_hedge",
    "selloff_thresh", "use_ma200_sell", "ma200_mult",
    "use_ignore_filter", "ignore_filter_lookback", "ignore_filter_thresh",
    "wait_short", "wait_long", "heavy_sell_count",
    "so_a_step", "so_a_pct", "so_a_cnt",
    "so_b_enabled", "so_b_start", "so_b_step", "so_b_pct", "so_b_cnt",
    "ma_a_step", "ma_a_pct", "ma_a_cnt",
    "ma_b_enabled", "ma_b_start", "ma_b_step", "ma_b_pct", "ma_b_cnt",
    "split_buy_max_exposure",
    "use_staged_loss_cap", "staged_loss_cap_pct",
    "use_trend_filter", "trend_require_ma5_above_ma200", "ma200_reentry_streak",
    "trend_require_above_ma50", "trend_require_ma200_rising",
    "ma200_slope_lookback", "trend_filter_mode",
    "use_hedge", "hedge_cash_pct", "hedge_gld_ratio",
    "use_tax", "tax_rate", "tax_deduction_usd",
]

# ── 기본값 (위젯 raw 값 기준 — /100 적용 전 정수) ─────────────────
DEFAULT_PARAMS: dict = {
    "rally_thresh_1": 15,
    "rally_thresh_2": 21,
    "rally_thresh_3": 25,
    "rally_sell_pct_1": 10,
    "rally_sell_pct_2": 20,
    "rally_sell_pct_3": 30,
    "rally_sell_to_hedge": True,
    "selloff_thresh": -3,
    "use_ma200_sell": True,
    "ma200_mult": 1.01,
    "use_ignore_filter": True,
    "ignore_filter_lookback": 15,
    "ignore_filter_thresh": -5,         # raw: -5 → app.py에서 /100 → -0.05
    "wait_short": 24,
    "wait_long": 24,
    "heavy_sell_count": 4,
    "so_a_step": 3,
    "so_a_pct": 10,
    "so_a_cnt": 5,
    "so_b_enabled": False,
    "so_b_start": 20,
    "so_b_step": 5,
    "so_b_pct": 10,
    "so_b_cnt": 5,
    "ma_a_step": 2,
    "ma_a_pct": 10,
    "ma_a_cnt": 5,
    "ma_b_enabled": False,
    "ma_b_start": 20,
    "ma_b_step": 3,
    "ma_b_pct": 10,
    "ma_b_cnt": 5,
    "split_buy_max_exposure": 40,
    "use_staged_loss_cap": False,
    "staged_loss_cap_pct": 20,
    "use_trend_filter": True,
    "trend_require_ma5_above_ma200": True,
    "ma200_reentry_streak": 3,
    "trend_require_above_ma50": True,
    "trend_require_ma200_rising": True,
    "ma200_slope_lookback": 20,
    "trend_filter_mode": "OR",

    "use_hedge": True,
    "hedge_cash_pct": 20,               # 20% 현금 → hedge_alloc_pct = 0.80
    "hedge_gld_ratio": 75,
    "use_tax": True,
    "tax_rate": 22,
    "tax_deduction_usd": 1800,
}

# ── 내장 프리셋 ────────────────────────────────────────────────────
_MA200_PARAMS: dict = {
    **DEFAULT_PARAMS,
    "use_ma200_sell": True,             # MA200 전량매도 명시적 ON
    "use_trend_filter": True,
    "trend_require_above_ma50": False,
    "trend_require_ma200_rising": True,
}

BUILTIN_PRESETS: list[dict] = [
    {
        "preset_format_version": 1,
        "name": "기본 설정",
        "description": "모든 파라미터 기본값",
        "_source": "builtin",
        "_filename": None,
        "params": dict(DEFAULT_PARAMS),
    },
    {
        "preset_format_version": 1,
        "name": "MA200 추세 필터 ★",
        "description": "CAGR 32.94% / MDD -59.17% / Sharpe 0.827",
        "_source": "builtin",
        "_filename": None,
        "params": dict(_MA200_PARAMS),
    },
]


def _ensure_dir() -> None:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)


def load_user_presets() -> list[dict]:
    """saves/presets/*.json 파일을 최신순으로 읽어서 반환."""
    _ensure_dir()
    presets = []
    for f in sorted(PRESETS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_source"] = "user"
            data["_filename"] = f.name
            presets.append(data)
        except Exception:
            pass
    return presets


def get_all_presets() -> list[dict]:
    """내장 프리셋 + 사용자 프리셋 목록 반환 (내장 먼저)."""
    return BUILTIN_PRESETS + load_user_presets()


def save_user_preset(name: str, raw_params: dict) -> str:
    """현재 raw 파라미터를 saves/presets/에 JSON으로 저장. 파일명 반환."""
    _ensure_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c for c in name if c.isalnum() or c in "._- ").strip()
    fname = f"{ts}_{safe}.json"
    data = {
        "preset_format_version": 1,
        "name": name,
        "description": "",
        "created_at": datetime.now().isoformat(),
        "params": {k: raw_params[k] for k in PARAM_KEYS if k in raw_params},
    }
    (PRESETS_DIR / fname).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return fname


def delete_user_preset(filename: str) -> None:
    """saves/presets/filename 삭제."""
    path = PRESETS_DIR / filename
    if path.exists():
        path.unlink()


def collect_raw_params() -> dict:
    """session_state에서 현재 위젯 raw 값을 읽어 반환."""
    return {k: st.session_state.get(k, DEFAULT_PARAMS[k]) for k in PARAM_KEYS}
