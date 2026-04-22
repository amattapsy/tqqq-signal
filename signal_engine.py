"""
signal_engine.py — 오늘의 투자 시그널 생성기

backtest.py의 run_backtest()를 그대로 사용하여 사용자의 거래 시작일부터
오늘까지 백테스트를 실행하고, 마지막 날짜의 신호를 "오늘의 신호"로 반환합니다.

⚠️ backtest.py의 매매 로직은 절대 수정하지 않습니다 ⚠️

핵심 설계:
- 사용자 거래 시작일 = 백테스트 시작일 (initial_pos=True 상태로 시작)
- 사용자 총 투자금 = initial_capital
- 오늘의 신호 = signals_df의 마지막 행
- 액션 수량은 사용자가 실제 보유한 포지션을 기준으로 계산
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

from backtest import run_backtest, Params


POSITION_FILE = Path(__file__).parent / "my_position.json"


# ════════════════════════════════════════════════════════════
# 포지션 저장 / 불러오기
# ════════════════════════════════════════════════════════════
def load_position() -> Optional[dict]:
    """my_position.json에서 사용자 포지션 불러오기."""
    if not POSITION_FILE.exists():
        return None
    try:
        with open(POSITION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_position(position: dict) -> None:
    """사용자 포지션을 my_position.json에 저장."""
    with open(POSITION_FILE, "w", encoding="utf-8") as f:
        json.dump(position, f, ensure_ascii=False, indent=2)


def default_position() -> dict:
    """비어있는 기본 포지션 구조."""
    return {
        "trade_start_date": "",        # YYYY-MM-DD, 거래를 시작한 날짜
        "initial_investment": 0.0,     # 거래 시작일 총 투입금 (USD)
        "tqqq_shares": 0.0,            # 현재 보유 TQQQ 주수
        "tqqq_avg_cost": 0.0,          # 평균 매수단가 (USD)
        "cash_usd": 0.0,               # 현재 현금 잔액 (USD)
        "gld_shares": 0.0,             # 현재 보유 GLD 주수
        "tlt_shares": 0.0,             # 현재 보유 SHY(=TLT 슬롯) 주수
    }


# ════════════════════════════════════════════════════════════
# 오늘의 신호 계산
# ════════════════════════════════════════════════════════════
def compute_today_signal(df_full: pd.DataFrame,
                         params: Params,
                         trade_start_date: str,
                         initial_investment: float) -> dict:
    """
    사용자 거래 시작일부터 오늘까지 백테스트를 돌려
    마지막 날짜의 신호 + 상태를 반환한다.

    Parameters
    ----------
    df_full : 전체 OHLCV 데이터프레임 (data.load_data 결과)
    params  : Params (사이드바에서 조정한 현재 전략 파라미터)
    trade_start_date : "YYYY-MM-DD"
    initial_investment : 거래 시작일 총 투자금 (USD)

    Returns
    -------
    dict with keys:
        ok: bool                 - 계산 성공 여부
        error: str               - 실패 사유 (ok=False인 경우)
        today_date: str          - 마지막 데이터 날짜
        today_signal: str        - 오늘의 신호 텍스트
        action_type: str         - "BUY_ALL" | "SELL_ALL" | "SPLIT_BUY" | "RALLY_SELL"
                                   | "WAITING" | "HOLD" | "REENTRY" | "HEDGE_BUY" 등
        signal_row: dict         - signals_df의 마지막 행
        last_trades: pd.DataFrame - 최근 5건 거래
        nav: float               - 백테스트 종료 시점 NAV (시뮬레이션 기준)
        expected: dict           - 전략이 가정하는 현재 보유 상태
                                   (tqqq_shares, cash, gld_shares, tlt_shares)
        prices: dict             - 오늘 가격 (qqq, tqqq, gld, tlt, ma200, ma50, ma5)
        next_triggers: list[str] - 다음에 발생할 수 있는 이벤트 예상가
    """
    result_stub = {"ok": False, "error": "", "today_date": "", "today_signal": "",
                   "action_type": "HOLD", "signal_row": {}, "last_trades": pd.DataFrame(),
                   "nav": 0.0, "expected": {}, "prices": {}, "next_triggers": []}

    # 날짜 파싱
    try:
        start_ts = pd.Timestamp(trade_start_date)
    except Exception:
        result_stub["error"] = f"거래 시작일 형식이 잘못되었습니다: {trade_start_date}"
        return result_stub

    if initial_investment <= 0:
        result_stub["error"] = "거래 시작일 총 투자금이 0보다 커야 합니다."
        return result_stub

    # 데이터 슬라이스
    df_slice = df_full[df_full.index >= start_ts].copy()
    if len(df_slice) < 2:
        result_stub["error"] = "거래 시작일 이후 데이터가 부족합니다. (최소 2거래일 필요)"
        return result_stub

    # 백테스트 실행 (로직 변경 없음)
    try:
        res = run_backtest(df_slice, params, float(initial_investment))
    except Exception as e:
        result_stub["error"] = f"백테스트 실행 중 오류: {e}"
        return result_stub

    signals_df = res["signals"]
    trades_df  = res["trades"]
    nav_series = res["nav"]

    if len(signals_df) == 0:
        result_stub["error"] = "신호 데이터가 생성되지 않았습니다."
        return result_stub

    last_row  = signals_df.iloc[-1].to_dict()
    last_date = last_row.get("date", str(signals_df.index[-1]))
    last_signal_text = str(last_row.get("signal", ""))

    # ── 액션 타입 분류 ─────────────────────────
    action_type = _classify_action(last_signal_text, trades_df, last_date)

    # ── 전략이 가정하는 현재 보유 상태 (최종 거래 기반) ──
    # 신호 DataFrame의 마지막 행 기준
    expected = {
        "tqqq_shares": 0.0,
        "cash_usd": float(last_row.get("cash_value", 0.0) or 0.0),
        "gld_shares": 0.0,
        "tlt_shares": 0.0,
        "stock_value_usd": float(last_row.get("stock_value", 0.0) or 0.0),
        "hedge_value_usd": float(last_row.get("hedge_value", 0.0) or 0.0),
    }
    # 주수 역산
    last_tqqq_price = float(df_slice["tqqq"].iloc[-1])
    last_gld_price  = float(df_slice["gld"].iloc[-1]) if "gld" in df_slice.columns else 0.0
    last_tlt_price  = float(df_slice["tlt"].iloc[-1]) if "tlt" in df_slice.columns else 0.0
    if last_tqqq_price > 0 and expected["stock_value_usd"] > 0:
        expected["tqqq_shares"] = expected["stock_value_usd"] / last_tqqq_price
    # 헷지는 GLD/SHY 비율로 나눔 (정확하지 않지만 근사)
    if expected["hedge_value_usd"] > 0:
        gld_ratio = params.hedge_gld_ratio
        if last_gld_price > 0:
            expected["gld_shares"] = (expected["hedge_value_usd"] * gld_ratio) / last_gld_price
        if last_tlt_price > 0:
            expected["tlt_shares"] = (expected["hedge_value_usd"] * (1 - gld_ratio)) / last_tlt_price

    # ── 오늘 가격 ──
    last = df_slice.iloc[-1]
    prices = {
        "qqq":   float(last["qqq"]),
        "tqqq":  float(last["tqqq"]),
        "gld":   float(last["gld"])  if "gld"  in df_slice.columns and pd.notna(last["gld"])  else None,
        "tlt":   float(last["tlt"])  if "tlt"  in df_slice.columns and pd.notna(last["tlt"])  else None,
        "ma200": float(last["ma200"]),
        "ma50":  float(last["ma50"])  if "ma50"  in df_slice.columns else None,
        "ma5":   float(last["ma5"])   if "ma5"   in df_slice.columns else None,
    }

    # ── 다음 트리거 예상가 ──
    next_triggers = _build_next_triggers(prices, last_row, params)

    # ── 최근 거래 5건 ──
    if len(trades_df) > 0:
        last_trades = trades_df.tail(5).copy()
    else:
        last_trades = pd.DataFrame()

    return {
        "ok": True,
        "error": "",
        "today_date": last_date,
        "today_signal": last_signal_text,
        "action_type": action_type,
        "signal_row": last_row,
        "last_trades": last_trades,
        "nav": float(nav_series.iloc[-1]),
        "nav_series": nav_series,         # 전체 NAV 시리즈 (그래프용)
        "df_slice": df_slice,             # TQQQ Buy&Hold 비교 및 날짜 용
        "initial_capital": float(initial_investment),
        "expected": expected,
        "prices": prices,
        "next_triggers": next_triggers,
    }


# ════════════════════════════════════════════════════════════
# 사용자 실제 포지션 → 오늘 실제 총자산
# ════════════════════════════════════════════════════════════
def compute_user_current_total(user_position: dict, prices: dict) -> float:
    """
    사용자 입력 포지션 + 오늘 가격 → 오늘 실제 총자산 (USD)
    """
    tqqq_price = prices.get("tqqq", 0.0) or 0.0
    gld_price  = prices.get("gld")  or 0.0
    tlt_price  = prices.get("tlt")  or 0.0

    tqqq_val = float(user_position.get("tqqq_shares", 0.0)) * tqqq_price
    gld_val  = float(user_position.get("gld_shares",  0.0)) * gld_price
    tlt_val  = float(user_position.get("tlt_shares",  0.0)) * tlt_price
    cash     = float(user_position.get("cash_usd",    0.0))

    return tqqq_val + gld_val + tlt_val + cash


def _classify_action(signal_text: str, trades_df: pd.DataFrame, today_date: str) -> str:
    """
    신호 텍스트에서 액션 타입 분류.
    오늘 실제로 발생한 거래가 있는지도 함께 확인.
    """
    s = signal_text

    # 오늘 발생한 거래 확인
    today_action = ""
    if len(trades_df) > 0:
        # trades의 date 컬럼은 Timestamp 또는 str일 수 있음
        _dates = pd.to_datetime(trades_df["date"]).dt.strftime("%Y-%m-%d")
        today_trades = trades_df[_dates == today_date]
        if len(today_trades) > 0:
            today_action = str(today_trades.iloc[-1]["action"])

    if today_action:
        if today_action == "BUY_ALL":
            if "재진입" in s:
                return "REENTRY"
            return "BUY_ALL"
        if today_action in ("SELL_ALL", "SELL_ALL_MA200", "SELLOFF_CONDITIONAL_SELL"):
            return "SELL_ALL"
        if today_action.startswith("SPLIT_BUY"):
            return "SPLIT_BUY"
        if today_action.startswith("RALLY_SELL"):
            return "RALLY_SELL"
        if today_action == "HEDGE_BUY":
            return "HEDGE_BUY"
        if today_action == "HEDGE_SELL":
            return "HEDGE_SELL"

    # 텍스트 기반 분류 (거래가 없는 날)
    if "관망" in s:
        return "WAITING"
    if "분할매수" in s:
        return "SPLIT_BUY"
    if "랠리" in s:
        return "RALLY_SELL"
    if "무시" in s:
        return "HOLD"
    return "HOLD"


def _build_next_triggers(prices: dict, signal_row: dict, p: Params) -> list:
    """
    오늘 가격 + 현재 상태 기반으로 다음 주요 이벤트 예상가를 반환.
    사용자가 "언제쯤 매도/매수 신호가 나올까?"를 알 수 있게 함.
    """
    out = []
    qqq   = prices.get("qqq", 0)
    ma200 = prices.get("ma200", 0)
    ath   = float(signal_row.get("ath_qqq", 0) or 0)

    # MA200 터치 예상가
    if ma200 > 0 and p.use_ma200_sell:
        touch_price = ma200 * p.ma200_mult
        if qqq > touch_price:
            dev_pct = (touch_price / qqq - 1) * 100
            out.append(f"MA200 터치선: QQQ ${touch_price:.2f} (현재 대비 {dev_pct:+.1f}%)")

    # -3% 기준
    if qqq > 0:
        threshold_pct = p.selloff_thresh * 100
        out.append(f"-3% 전량매도 트리거: QQQ 일간 {threshold_pct:.1f}% 이상 하락 시")

    # 랠리 익절 예상가 (ATH 기반)
    if ath > 0:
        for thresh, pct_sell, label in [
            (p.rally_thresh_1, p.rally_sell_pct_1, "랠리 1단계"),
            (p.rally_thresh_2, p.rally_sell_pct_2, "랠리 2단계"),
            (p.rally_thresh_3, p.rally_sell_pct_3, "랠리 3단계"),
        ]:
            target = ath * (1 + thresh)
            if qqq < target:
                gap_pct = (target / qqq - 1) * 100
                out.append(f"{label}({pct_sell*100:.0f}% 매도): QQQ ${target:.2f} (현재 대비 {gap_pct:+.1f}%)")

    return out


# ════════════════════════════════════════════════════════════
# 사용자 실제 포지션 기준 액션 수량 계산
# ════════════════════════════════════════════════════════════
def calc_action_recommendation(signal_dict: dict, user_position: dict,
                               params: Params) -> list:
    """
    오늘의 신호 + 사용자 실제 포지션 → 구체적 행동 권장사항 리스트.

    Returns
    -------
    list of dict:
        {"asset": "TQQQ", "action": "매수"|"매도", "shares": float,
         "amount_usd": float, "price": float, "note": str}
    """
    rec = []
    action_type = signal_dict.get("action_type", "HOLD")
    prices      = signal_dict.get("prices", {})
    signal_row  = signal_dict.get("signal_row", {})
    signal_text = signal_dict.get("today_signal", "")

    tqqq_price = prices.get("tqqq", 0.0)
    gld_price  = prices.get("gld") or 0.0
    tlt_price  = prices.get("tlt") or 0.0

    u_tqqq = float(user_position.get("tqqq_shares", 0.0))
    u_cash = float(user_position.get("cash_usd", 0.0))
    u_gld  = float(user_position.get("gld_shares", 0.0))
    u_tlt  = float(user_position.get("tlt_shares", 0.0))

    if action_type == "SELL_ALL":
        if u_tqqq > 0 and tqqq_price > 0:
            rec.append({
                "asset": "TQQQ", "action": "전량매도",
                "shares": u_tqqq, "amount_usd": u_tqqq * tqqq_price,
                "price": tqqq_price, "note": "다음 거래일 시가에 전량 매도"
            })
        # 헷지 매수 (매도 대금의 hedge_alloc_pct)
        if params.use_hedge:
            proceeds = u_tqqq * tqqq_price + u_cash
            hedge_cash = proceeds * params.hedge_alloc_pct
            gld_amt = hedge_cash * params.hedge_gld_ratio
            tlt_amt = hedge_cash * (1 - params.hedge_gld_ratio)
            if gld_amt > 0 and gld_price > 0:
                rec.append({
                    "asset": "GLD", "action": "매수",
                    "shares": gld_amt / gld_price, "amount_usd": gld_amt,
                    "price": gld_price, "note": f"헷지 매수 ({params.hedge_gld_ratio*100:.0f}% 비중)"
                })
            if tlt_amt > 0 and tlt_price > 0:
                rec.append({
                    "asset": "SHY", "action": "매수",
                    "shares": tlt_amt / tlt_price, "amount_usd": tlt_amt,
                    "price": tlt_price, "note": f"헷지 매수 ({(1-params.hedge_gld_ratio)*100:.0f}% 비중)"
                })

    elif action_type in ("BUY_ALL", "REENTRY"):
        # 현재 헷지 전량매도 + TQQQ 전량매수
        if u_gld > 0 and gld_price > 0:
            rec.append({
                "asset": "GLD", "action": "전량매도",
                "shares": u_gld, "amount_usd": u_gld * gld_price,
                "price": gld_price, "note": "헷지 해제"
            })
        if u_tlt > 0 and tlt_price > 0:
            rec.append({
                "asset": "SHY", "action": "전량매도",
                "shares": u_tlt, "amount_usd": u_tlt * tlt_price,
                "price": tlt_price, "note": "헷지 해제"
            })
        total_cash = u_cash + u_gld * gld_price + u_tlt * tlt_price
        if total_cash > 0 and tqqq_price > 0:
            rec.append({
                "asset": "TQQQ", "action": "전량매수",
                "shares": total_cash / tqqq_price, "amount_usd": total_cash,
                "price": tqqq_price,
                "note": "관망 종료 → 재진입" if action_type == "REENTRY" else "전량 매수"
            })

    elif action_type == "SPLIT_BUY":
        # 신호 텍스트에서 구간(A1/A2/B1 등) 추출
        # buy_pct는 일반적으로 so_a_pct 또는 ma_a_pct
        is_ma = "MA" in signal_text or "ma200" in signal_text.lower()
        buy_pct = params.ma_a_pct if is_ma else params.so_a_pct

        # 분할매수는 "현금 + 헷지자산"의 buy_pct 만큼 TQQQ 매수
        total_liquid = u_cash + u_gld * gld_price + u_tlt * tlt_price
        buy_amount = total_liquid * buy_pct
        # 현금 부족 시 헷지 일부 매도
        if buy_amount > u_cash:
            need_from_hedge = buy_amount - u_cash
            hedge_val = u_gld * gld_price + u_tlt * tlt_price
            if hedge_val > 0:
                ratio = min(1.0, need_from_hedge / hedge_val)
                if u_gld > 0 and gld_price > 0:
                    sell_g = u_gld * ratio
                    rec.append({
                        "asset": "GLD", "action": "매도",
                        "shares": sell_g, "amount_usd": sell_g * gld_price,
                        "price": gld_price, "note": "분할매수 자금 마련"
                    })
                if u_tlt > 0 and tlt_price > 0:
                    sell_t = u_tlt * ratio
                    rec.append({
                        "asset": "SHY", "action": "매도",
                        "shares": sell_t, "amount_usd": sell_t * tlt_price,
                        "price": tlt_price, "note": "분할매수 자금 마련"
                    })
        if buy_amount > 0 and tqqq_price > 0:
            rec.append({
                "asset": "TQQQ", "action": "분할매수",
                "shares": buy_amount / tqqq_price, "amount_usd": buy_amount,
                "price": tqqq_price, "note": signal_text
            })

    elif action_type == "RALLY_SELL":
        # 랠리 단계에 따른 매도 비율
        sell_pct = params.rally_sell_pct_1
        if "2단계" in signal_text:
            sell_pct = params.rally_sell_pct_2
        elif "3단계" in signal_text:
            sell_pct = params.rally_sell_pct_3

        sell_shares = u_tqqq * sell_pct
        if sell_shares > 0 and tqqq_price > 0:
            proceeds = sell_shares * tqqq_price
            rec.append({
                "asset": "TQQQ", "action": "랠리 익절",
                "shares": sell_shares, "amount_usd": proceeds,
                "price": tqqq_price, "note": f"{sell_pct*100:.0f}% 매도"
            })
            if params.rally_sell_to_hedge and params.use_hedge:
                gld_amt = proceeds * params.hedge_gld_ratio
                tlt_amt = proceeds * (1 - params.hedge_gld_ratio)
                if gld_amt > 0 and gld_price > 0:
                    rec.append({
                        "asset": "GLD", "action": "매수",
                        "shares": gld_amt / gld_price, "amount_usd": gld_amt,
                        "price": gld_price, "note": "익절대금 헷지매수"
                    })
                if tlt_amt > 0 and tlt_price > 0:
                    rec.append({
                        "asset": "SHY", "action": "매수",
                        "shares": tlt_amt / tlt_price, "amount_usd": tlt_amt,
                        "price": tlt_price, "note": "익절대금 헷지매수"
                    })

    elif action_type in ("HEDGE_BUY", "HEDGE_SELL"):
        rec.append({
            "asset": "-", "action": "헷지 조정",
            "shares": 0, "amount_usd": 0,
            "price": 0, "note": signal_text
        })

    # HOLD, WAITING → 빈 리스트 (아무 것도 안 함)
    return rec
