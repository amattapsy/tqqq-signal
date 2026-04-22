"""
metrics.py — 백테스트 성과 지표 계산
"""

import numpy as np
import pandas as pd


def calc_metrics(nav: pd.Series, initial_capital: float, risk_free_rate: float = 0.02) -> dict:
    """
    주요 성과 지표 계산.

    Parameters
    ----------
    nav : 날짜별 NAV 시리즈
    initial_capital : 초기 자본
    risk_free_rate  : 무위험 수익률 (연간, 샤프 계산용)

    Returns
    -------
    dict of metrics
    """
    nav = nav.dropna()
    if len(nav) < 2:
        return {}

    final = nav.iloc[-1]
    total_return = (final - initial_capital) / initial_capital

    # CAGR
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = (final / initial_capital) ** (1 / years) - 1 if years > 0 else 0.0

    # MDD (최대낙폭)
    rolling_max = nav.cummax()
    drawdown = (nav - rolling_max) / rolling_max
    mdd = drawdown.min()

    # 일간 수익률
    daily_ret = nav.pct_change().dropna()

    # Sharpe Ratio (연율화)
    excess = daily_ret - risk_free_rate / 252
    sharpe = (excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else 0.0

    # 연도별 수익률
    annual = nav.resample("YE").last().pct_change().dropna()
    annual.index = annual.index.year

    # 승률 (양의 수익률 연도 비율)
    win_rate = (annual > 0).mean() if len(annual) > 0 else 0.0

    # 최악의 연도
    worst_year = annual.idxmin() if len(annual) > 0 else None
    worst_year_ret = annual.min() if len(annual) > 0 else 0.0

    # 최고의 연도
    best_year = annual.idxmax() if len(annual) > 0 else None
    best_year_ret = annual.max() if len(annual) > 0 else 0.0

    return {
        "최종 잔고": final,
        "총 수익률": total_return,
        "CAGR": cagr,
        "MDD": mdd,
        "Sharpe Ratio": sharpe,
        "백테스트 기간 (년)": years,
        "연도별 수익률": annual,
        "승률 (연도별)": win_rate,
        "최악의 연도": worst_year,
        "최악의 연도 수익률": worst_year_ret,
        "최고의 연도": best_year,
        "최고의 연도 수익률": best_year_ret,
    }


def calc_bnh_metrics(df: pd.DataFrame, initial_capital: float) -> dict:
    """TQQQ Buy & Hold 성과 (비교용)"""
    tqqq = df["tqqq"].dropna()
    if len(tqqq) < 2:
        return {}
    shares = initial_capital / tqqq.iloc[0]
    nav = tqqq * shares
    return calc_metrics(nav, initial_capital)
