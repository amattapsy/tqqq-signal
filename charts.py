"""
charts.py — Plotly 인터랙티브 차트 생성
"""

import pandas as pd
import numpy as np
import plotly.graph_objects as go

# ── 알려진 하락 이슈 (날짜 범위 → 이름 매핑) ─────────────
_KNOWN_EVENTS = [
    ("1987-10-01", "1987-12-31", "블랙 먼데이"),
    ("1990-07-01", "1990-12-31", "걸프전 경기침체"),
    ("2000-03-01", "2002-10-31", "닷컴버블 붕괴"),
    ("2007-10-01", "2009-03-31", "금융위기 (서브프라임·리먼)"),
    ("2011-05-01", "2011-12-31", "유럽 재정위기"),
    ("2015-06-01", "2016-03-31", "중국 경제 둔화"),
    ("2018-10-01", "2019-01-31", "미중 무역분쟁·금리 인상"),
    ("2020-02-01", "2020-04-30", "코로나19 팬데믹"),
    ("2022-01-01", "2022-12-31", "인플레이션·금리 급등"),
]


def _match_issue(start, end) -> str:
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    best = "기타 하락"
    best_overlap = pd.Timedelta(0)
    for ks, ke, name in _KNOWN_EVENTS:
        ks, ke = pd.Timestamp(ks), pd.Timestamp(ke)
        overlap_start = max(s, ks)
        overlap_end   = min(e, ke)
        if overlap_end > overlap_start:
            overlap = overlap_end - overlap_start
            if overlap > best_overlap:
                best_overlap = overlap
                best = name
    return best


def find_bear_periods(nav: pd.Series, threshold: float = -0.35) -> list[dict]:
    """MDD가 threshold 이하인 구간 탐지"""
    rolling_max = nav.cummax()
    dd = (nav - rolling_max) / rolling_max

    periods = []
    start = None
    for date, is_bear in (dd <= threshold).items():
        if is_bear and start is None:
            start = date
        elif not is_bear and start is not None:
            seg = dd[start:date]
            periods.append({
                "start":       start,
                "end":         date,
                "bottom":      seg.idxmin(),
                "mdd":         seg.min(),
                "issue":       _match_issue(start, date),
            })
            start = None
    if start is not None:
        seg = dd[start:]
        periods.append({
            "start":   start,
            "end":     nav.index[-1],
            "bottom":  seg.idxmin(),
            "mdd":     seg.min(),
            "issue":   _match_issue(start, nav.index[-1]),
        })
    return periods


def chart_nav(nav: pd.Series, bnh_nav: pd.Series, trades: pd.DataFrame,
              bear_periods: list = None, log_scale: bool = True) -> go.Figure:
    """NAV vs B&H + 매수/매도 마커 + 하락장 음영"""
    fig = go.Figure()

    # 하락장 음영
    if bear_periods:
        for bp in bear_periods:
            fig.add_vrect(
                x0=str(bp["start"])[:10], x1=str(bp["end"])[:10],
                fillcolor="rgba(255,0,0,0.08)", layer="below", line_width=0,
                annotation_text=bp["issue"],
                annotation_position="top left",
                annotation=dict(font_size=10, font_color="#cc0000"),
            )

    fig.add_trace(go.Scatter(
        x=nav.index, y=nav.values,
        name="전략 NAV",
        line=dict(color="#2196F3", width=2),
    ))
    fig.add_trace(go.Scatter(
        x=bnh_nav.index, y=bnh_nav.values,
        name="TQQQ Buy & Hold",
        line=dict(color="#FF9800", width=1.5, dash="dot"),
    ))

    if trades is not None and len(trades) > 0:
        nav_dict = nav.to_dict()
        buys   = trades[trades["action"] == "BUY_ALL"]
        sells  = trades[trades["action"] == "SELL_ALL"]
        ma200_sells = trades[trades["action"] == "SELL_ALL_MA200"]
        cond_sells = trades[trades["action"] == "SELLOFF_CONDITIONAL_SELL"]
        splits = trades[trades["action"].str.startswith("SPLIT_BUY")]
        rallys = trades[trades["action"].str.startswith("RALLY_SELL")]

        def marker_y(rows):
            return [nav_dict.get(d, None) for d in rows["date"]]

        if len(buys):
            fig.add_trace(go.Scatter(x=buys["date"], y=marker_y(buys), mode="markers",
                name="전량매수", marker=dict(symbol="triangle-up", size=11, color="#4CAF50"),
                hovertemplate="%{x}<br>전량매수<extra></extra>"))
        if len(sells):
            fig.add_trace(go.Scatter(x=sells["date"], y=marker_y(sells), mode="markers",
                name="-3% 전량매도", marker=dict(symbol="triangle-down", size=11, color="#F44336"),
                hovertemplate="%{x}<br>-3% 전량매도<extra></extra>"))
        if len(ma200_sells):
            fig.add_trace(go.Scatter(x=ma200_sells["date"], y=marker_y(ma200_sells), mode="markers",
                name="MA200 전량매도", marker=dict(symbol="diamond", size=11, color="#3E2723"),
                hovertemplate="%{x}<br>MA200 전량매도<extra></extra>"))
        if len(splits):
            fig.add_trace(go.Scatter(x=splits["date"], y=marker_y(splits), mode="markers",
                name="분할매수", marker=dict(symbol="circle", size=7, color="#9C27B0", opacity=0.7),
                hovertemplate="%{x}<br>분할매수<extra></extra>"))
        if len(rallys):
            fig.add_trace(go.Scatter(x=rallys["date"], y=marker_y(rallys), mode="markers",
                name="랠리 익절", marker=dict(symbol="star", size=9, color="#FF9800"),
                hovertemplate="%{x}<br>랠리 익절<extra></extra>"))
        if len(cond_sells):
            fig.add_trace(go.Scatter(x=cond_sells["date"], y=marker_y(cond_sells), mode="markers",
                name="조건부매도", marker=dict(symbol="triangle-down", size=11, color="#E91E63"),
                hovertemplate="%{x}<br>조건부매도<extra></extra>"))

    fig.update_layout(
        title="포트폴리오 NAV vs TQQQ Buy & Hold",
        xaxis_title="날짜",
        yaxis_title="NAV (로그 스케일)" if log_scale else "NAV",
        yaxis_type="log" if log_scale else "linear",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=520,
    )
    return fig


def chart_drawdown(nav: pd.Series, bnh_nav: pd.Series, bear_periods: list = None) -> go.Figure:
    def calc_dd(s):
        return (s - s.cummax()) / s.cummax() * 100

    fig = go.Figure()

    if bear_periods:
        for bp in bear_periods:
            fig.add_vrect(
                x0=str(bp["start"])[:10], x1=str(bp["end"])[:10],
                fillcolor="rgba(255,0,0,0.08)", layer="below", line_width=0,
            )

    fig.add_trace(go.Scatter(x=nav.index, y=calc_dd(nav),
        name="전략", fill="tozeroy", line=dict(color="#2196F3")))
    fig.add_trace(go.Scatter(x=bnh_nav.index, y=calc_dd(bnh_nav),
        name="TQQQ B&H", line=dict(color="#FF9800", dash="dot")))
    fig.add_hline(y=-35, line_dash="dash", line_color="red",
                  annotation_text="-35% (하락장 기준)", annotation_position="right")

    fig.update_layout(title="드로다운 (%)", xaxis_title="날짜", yaxis_title="드로다운 (%)",
                      hovermode="x unified", height=350)
    return fig


def chart_annual_returns(annual: pd.Series, bnh_annual: pd.Series) -> go.Figure:
    years = sorted(set(annual.index) | set(bnh_annual.index))
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[str(y) for y in years],
        y=[annual.get(y, 0) * 100 for y in years],
        name="전략",
        marker_color=["#4CAF50" if annual.get(y, 0) >= 0 else "#F44336" for y in years],
    ))
    fig.add_trace(go.Bar(
        x=[str(y) for y in years],
        y=[bnh_annual.get(y, 0) * 100 for y in years],
        name="TQQQ B&H", marker_color="rgba(255,152,0,0.5)",
    ))
    fig.update_layout(title="연도별 수익률 (%)", xaxis_title="연도", yaxis_title="수익률 (%)",
                      barmode="group", height=350)
    return fig


def chart_cash_ratio(nav: pd.Series, signals: pd.DataFrame) -> go.Figure:
    """헷지 자산 보유 비율 차트. signals의 실제 hedge_value를 사용."""
    if signals is None or len(signals) == 0:
        return go.Figure()

    # signals의 날짜를 DatetimeIndex로 변환하고 nav와 정렬
    sig = signals.copy()
    sig.index = pd.to_datetime(sig["date"])
    sig = sig.reindex(nav.index, method="ffill")

    hedge_val = sig["hedge_value"].fillna(0.0) if "hedge_value" in sig.columns else pd.Series(0.0, index=nav.index)
    cash_val  = sig["cash_value"].fillna(0.0)  if "cash_value"  in sig.columns else pd.Series(0.0, index=nav.index)

    nav_aligned = nav.reindex(sig.index).ffill().fillna(1.0)
    hedge_ratio = (hedge_val / nav_aligned * 100).clip(0, 100)
    cash_ratio  = (cash_val  / nav_aligned * 100).clip(0, 100)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=nav.index, y=hedge_ratio.values,
        name="헷지 자산 (GLD+TLT)", fill="tozeroy",
        line=dict(color="#9C27B0"), stackgroup="one",
    ))
    fig.add_trace(go.Scatter(
        x=nav.index, y=cash_ratio.values,
        name="현금", fill="tonexty",
        line=dict(color="#78909C"), stackgroup="one",
    ))
    fig.update_layout(
        title="헷지 자산 보유 비율 (관망 기간)",
        xaxis_title="날짜",
        yaxis_title="비율 (%)",
        yaxis=dict(range=[0, 105]),
        hovermode="x unified",
        height=260,
    )
    return fig
