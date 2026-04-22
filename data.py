"""
data.py — Yahoo Finance 데이터 다운로드 + 합성 QQQ/TQQQ/VIX 생성
"""

import numpy as np
import pandas as pd
import yfinance as yf
import streamlit as st

TQQQ_EXPENSE_RATIO = 0.0095
TQQQ_LAUNCH_DATE = "2010-02-11"
QQQ_LAUNCH_DATE  = "1999-03-10"
GLD_EXPENSE_RATIO  = 0.0040   # 0.40%/year
GLD_LAUNCH_DATE    = "2004-11-18"
SHY_EXPENSE_RATIO  = 0.0015   # 0.15%/year
SHY_LAUNCH_DATE    = "2002-07-22"
SHY_DURATION       = 1.8      # 근사 프록시 — SHY 실제 듀레이션(~1.8년)과 ^IRX(3개월) 만기 괴리 있음


@st.cache_data(show_spinner="Yahoo Finance에서 데이터 다운로드 중...")
def load_data(start_year: int = 1985) -> pd.DataFrame:
    """
    QQQ 신호용 + TQQQ + VIX 데이터 반환 (합성 포함).

    반환 컬럼:
        qqq, tqqq, ma200, ma5, qqq_daily_ret, vix
    """
    start_str = f"{start_year}-01-01"

    raw_ndx  = yf.download("^NDX",  start=start_str,        auto_adjust=True, progress=False)
    raw_qqq  = yf.download("QQQ",   start=QQQ_LAUNCH_DATE,  auto_adjust=True, progress=False)
    raw_tqqq = yf.download("TQQQ",  start=TQQQ_LAUNCH_DATE, auto_adjust=True, progress=False)
    raw_vix  = yf.download("^VIX",  start=start_str,        auto_adjust=True, progress=False)
    raw_gld  = yf.download("GLD",   start=GLD_LAUNCH_DATE,   auto_adjust=True, progress=False)
    raw_shy  = yf.download("SHY",   start=SHY_LAUNCH_DATE,  auto_adjust=True, progress=False)

    def extract_close(raw):
        if isinstance(raw.columns, pd.MultiIndex):
            return raw["Close"].iloc[:, 0]
        return raw["Close"]

    def extract_open(raw):
        if isinstance(raw.columns, pd.MultiIndex):
            return raw["Open"].iloc[:, 0]
        return raw["Open"]

    # 벤치마크: GLD/SHY 합성용 (extract_close 정의 후 호출)
    try:
        raw_gold = yf.download("GC=F", start=start_str, auto_adjust=True, progress=False)
        gold_close = extract_close(raw_gold).rename("gold_futures")
    except Exception:
        gold_close = pd.Series(dtype=float, name="gold_futures")
    try:
        # ^IRX: 3개월 T-Bill 연율 수익률 (% 단위, e.g. 5.23 = 5.23%)
        # SHY 실효 듀레이션 ~1.8년 대비 만기가 짧은 근사 프록시
        raw_irx = yf.download("^IRX", start=start_str, auto_adjust=True, progress=False)
        irx_close = extract_close(raw_irx).rename("irx")
    except Exception:
        irx_close = pd.Series(dtype=float, name="irx")

    ndx_close  = extract_close(raw_ndx).rename("ndx")
    qqq_close  = extract_close(raw_qqq).rename("qqq_real")
    tqqq_close = extract_close(raw_tqqq).rename("tqqq_real")
    tqqq_open  = extract_open(raw_tqqq).rename("tqqq_open_real")
    vix_close  = extract_close(raw_vix).rename("vix_real")
    gld_close  = extract_close(raw_gld).rename("gld_real")
    gld_open   = extract_open(raw_gld).rename("gld_open_real")
    shy_close  = extract_close(raw_shy).rename("shy_real")
    shy_open   = extract_open(raw_shy).rename("shy_open_real")

    all_dates = ndx_close.index.union(qqq_close.index).union(tqqq_close.index)
    all_dates = all_dates[all_dates >= pd.Timestamp(f"{start_year}-01-01")]

    df = pd.DataFrame(index=all_dates)
    df.index.name = "date"
    df["ndx"]            = ndx_close.reindex(all_dates)
    df["qqq_real"]       = qqq_close.reindex(all_dates)
    df["tqqq_real"]      = tqqq_close.reindex(all_dates)
    df["tqqq_open_real"] = tqqq_open.reindex(all_dates)

    # ── QQQ 합성 ────────────────────────────────────────
    df["ndx_ret"] = df["ndx"].pct_change()
    qqq_start       = df["qqq_real"].first_valid_index()
    qqq_start_price = df.loc[qqq_start, "qqq_real"]

    df["qqq"] = df["qqq_real"].copy()
    dates_before_qqq = df.index[df.index < qqq_start]
    if len(dates_before_qqq) > 0:
        prices = [qqq_start_price]
        sorted_before = sorted(dates_before_qqq, reverse=True)
        date_list = [qqq_start] + sorted_before
        for i in range(1, len(date_list)):
            ret = df.loc[date_list[i - 1], "ndx_ret"]
            ret = ret if pd.notna(ret) else 0.0
            p = prices[-1] / (1 + ret) if (1 + ret) != 0 else prices[-1]
            prices.append(p)
        for d, p in zip(sorted_before, prices[1:]):
            df.loc[d, "qqq"] = p
    df["qqq"] = df["qqq"].ffill().bfill()

    # ── TQQQ 합성 ────────────────────────────────────────
    df["qqq_ret"] = df["qqq"].pct_change()
    tqqq_start       = df["tqqq_real"].first_valid_index()
    tqqq_start_price = df.loc[tqqq_start, "tqqq_real"]

    df["tqqq"] = df["tqqq_real"].copy()
    dates_before_tqqq = df.index[df.index < tqqq_start]
    if len(dates_before_tqqq) > 0:
        daily_expense = TQQQ_EXPENSE_RATIO / 252
        prices = [tqqq_start_price]
        sorted_before = sorted(dates_before_tqqq, reverse=True)
        date_list = [tqqq_start] + sorted_before
        for i in range(1, len(date_list)):
            qqq_ret = df.loc[date_list[i - 1], "qqq_ret"]
            qqq_ret = qqq_ret if pd.notna(qqq_ret) else 0.0
            lev_ret = 3 * qqq_ret - daily_expense
            p = prices[-1] / (1 + lev_ret) if (1 + lev_ret) != 0 else prices[-1]
            prices.append(p)
        for d, p in zip(sorted_before, prices[1:]):
            df.loc[d, "tqqq"] = p
    df["tqqq"] = df["tqqq"].ffill().bfill()

    # ── GLD 합성 (금 선물 GC=F 기반 reverse-walk) ────────────
    df["gld"] = gld_close.reindex(all_dates)
    df["gld_open"] = gld_open.reindex(all_dates)
    gld_start = df["gld"].first_valid_index()
    if gld_start is not None and len(gold_close) > 0:
        _gold = gold_close.reindex(all_dates).ffill()
        _gold_ret = _gold.pct_change()
        gld_start_price = df.loc[gld_start, "gld"]
        dates_before_gld = df.index[df.index < gld_start]
        if len(dates_before_gld) > 0:
            daily_exp_gld = GLD_EXPENSE_RATIO / 252
            prices = [gld_start_price]
            sorted_before = sorted(dates_before_gld, reverse=True)
            date_list = [gld_start] + sorted_before
            for i in range(1, len(date_list)):
                g_ret = _gold_ret.get(date_list[i - 1], 0.0)
                g_ret = g_ret if pd.notna(g_ret) else 0.0
                synth_ret = g_ret - daily_exp_gld
                p = prices[-1] / (1 + synth_ret) if (1 + synth_ret) != 0 else prices[-1]
                prices.append(p)
            for d, p in zip(sorted_before, prices[1:]):
                df.loc[d, "gld"] = p
                df.loc[d, "gld_open"] = p  # 합성 구간: 종가를 시가 대용
    df["gld"] = df["gld"].ffill().bfill()
    df["gld_open"] = df["gld_open"].ffill().bfill()

    # ── SHY 합성 (3개월 T-Bill ^IRX 기반 단기채 듀레이션 모델 + reverse-walk) ──
    # 컬럼명은 "tlt"/"tlt_open"/"tlt_exec" 유지 (Option 1: 내부 슬롯 재사용)
    df["tlt"] = shy_close.reindex(all_dates)
    df["tlt_open"] = shy_open.reindex(all_dates)
    shy_start = df["tlt"].first_valid_index()
    if shy_start is not None and len(irx_close) > 0:
        _irx = irx_close.reindex(all_dates).ffill()
        _y_dec = _irx / 100                              # % → 소수 (e.g. 5.23 → 0.0523)
        _yield_chg = _irx.diff()                         # 일간 변화 (% 단위, e.g. 0.05 = 5bp)
        # 단기채 고정 듀레이션 모델 (SHY ~1.8년, 근사 프록시: ^IRX 3개월)
        # 채권 일간 수익률 = -duration * (yield_change/100) + yield_accrual
        _bond_ret = -SHY_DURATION * (_yield_chg / 100) + _y_dec / 252
        _bond_ret = pd.Series(_bond_ret, index=all_dates)

        shy_start_price = df.loc[shy_start, "tlt"]
        dates_before_shy = df.index[df.index < shy_start]
        if len(dates_before_shy) > 0:
            daily_exp_shy = SHY_EXPENSE_RATIO / 252
            prices = [shy_start_price]
            sorted_before = sorted(dates_before_shy, reverse=True)
            date_list = [shy_start] + sorted_before
            for i in range(1, len(date_list)):
                b_ret = _bond_ret.get(date_list[i - 1], 0.0)
                b_ret = b_ret if pd.notna(b_ret) else 0.0
                synth_ret = b_ret - daily_exp_shy
                p = prices[-1] / (1 + synth_ret) if (1 + synth_ret) != 0 else prices[-1]
                prices.append(p)
            for d, p in zip(sorted_before, prices[1:]):
                df.loc[d, "tlt"] = p
                df.loc[d, "tlt_open"] = p  # 합성 구간: 종가를 시가 대용
    df["tlt"] = df["tlt"].ffill().bfill()
    df["tlt_open"] = df["tlt_open"].ffill().bfill()

    # ── TQQQ 실행가 (다음날 시가) ──────────────────────────
    # 한국 투자자: 당일 종가 보고 → 다음날 미국장 시가에 주문 실행
    # 합성 구간(2010년 이전)은 실제 시가 없으므로 종가를 대용으로 사용
    df["tqqq_open"] = df["tqqq_open_real"].copy()
    dates_before_tqqq_open = df.index[df.index < tqqq_start]
    for d in dates_before_tqqq_open:
        df.loc[d, "tqqq_open"] = df.loc[d, "tqqq"]  # 합성 구간: 종가 대용
    df["tqqq_open"] = df["tqqq_open"].ffill().bfill()
    # tqqq_exec[i] = i+1일의 시가 (당일 신호 → 다음날 실행)
    df["tqqq_exec"] = df["tqqq_open"].shift(-1)
    df["tqqq_exec"] = df["tqqq_exec"].fillna(df["tqqq"])  # 마지막 날: 종가로 대체

    # ── GLD/TLT 실행가 (다음날 시가) ─────────────────────
    df["gld_exec"] = df["gld_open"].shift(-1)
    df["gld_exec"] = df["gld_exec"].fillna(df["gld"])
    df["tlt_exec"] = df["tlt_open"].shift(-1)
    df["tlt_exec"] = df["tlt_exec"].fillna(df["tlt"])

    # ── 이동평균 ─────────────────────────────────────────
    df["ma200"] = df["qqq"].rolling(200, min_periods=1).mean()
    df["ma50"]  = df["qqq"].rolling(50,  min_periods=1).mean()
    df["ma5"]   = df["qqq"].rolling(5,   min_periods=1).mean()
    df["qqq_daily_ret"] = df["qqq"].pct_change()

    # ── VIX ─────────────────────────────────────────────
    # VIX는 1990년 이전 데이터 없음 → NaN 유지 (backtest에서 0으로 처리)
    df["vix"] = vix_close.reindex(all_dates)
    df["vix"] = df["vix"].ffill()  # 공휴일 등 결측 채우기

    df = df.dropna(subset=["qqq", "tqqq"])
    return df[["qqq", "tqqq", "tqqq_exec", "gld", "gld_exec", "tlt", "tlt_exec",
               "ma200", "ma50", "ma5", "qqq_daily_ret", "vix"]].copy()
