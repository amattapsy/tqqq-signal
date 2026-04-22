"""
backtest.py — QQQ 신호 기반 TQQQ 매매 전략 백테스트 엔진

[ATH 규칙 — 확정]
  ath_qqq : 랠리 익절 계산에 사용하는 "동결된 전고점"
  running_max_qqq : 현재 보유 기간 중 QQQ 누적 최고값 (ATH 후보)

  ① 초기 포지션(initial_pos=True) 기간:
       매일 running_max_qqq 갱신 + ath_qqq도 같이 갱신
       → -3% 첫 발생 전까지는 ath_qqq = 그날그날 최고값

  ② -3% 이벤트 또는 MA200 이벤트 발생 시:
       ath_qqq = running_max_qqq  (그 순간 최고값으로 확정·동결)
       이후 ath_qqq는 절대 변경 안 함

  ③ 재진입 후 보유 기간:
       running_max_qqq만 매일 갱신 (다음 이벤트 때 쓸 ATH 후보)
       ath_qqq는 이전 이벤트 때 확정된 값 그대로 유지

  ④ 랠리 익절 체크:
       gain = (오늘 QQQ - ath_qqq) / ath_qqq
       QQQ가 ATH 대비 +9%/+21%/+28% 초과 시 각 레벨 발동

  엑셀 검증 예시:
    2010-04-23: QQQ=50.52 → ATH 확정 (2010-05-04 -3% 이벤트 시)
    재진입 후 QQQ=55.07 (50.52×1.09) → 랠리 1단계 ✓
    2011-08-04 -3% 이벤트: ATH → 59.63 (직전 최고)
    재진입 후 QQQ=65.0 (59.63×1.09) → 랠리 1단계 ✓
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field


@dataclass
class Params:
    # ── 랠리 익절 ────────────────────────────────────────
    rally_thresh_1: float = 0.15
    rally_thresh_2: float = 0.21
    rally_thresh_3: float = 0.25
    rally_sell_pct_1: float = 0.10
    rally_sell_pct_2: float = 0.20
    rally_sell_pct_3: float = 0.30
    rally_sell_to_hedge: bool = True   # ON: 익절 대금 즉시 헷지매수 / OFF: 현금 보유

    # ── 하락 트리거 ──────────────────────────────────────
    selloff_thresh: float = -0.03
    use_ma200_sell: bool = True    # MA200 전량매도 기능 ON/OFF
    ma200_mult: float = 1.01

    # ── 재진입 대기 ──────────────────────────────────────
    wait_short: int = 24
    wait_long: int = 24
    heavy_sell_count: int = 4
    ma200_wait_days: int = 10   # MA200 전량매도 후 최소 관망 거래일 (추세필터와 AND 게이트)


    # ── 분할매수 (-3% 트리거) ────────────────────────────
    so_a_step: float = 0.03
    so_a_pct:  float = 0.10
    so_a_cnt:  int   = 5
    so_b_enabled: bool = False
    so_b_start: float = 0.20
    so_b_step: float = 0.05
    so_b_pct:  float = 0.10
    so_b_cnt:  int   = 5

    # ── 분할매수 (MA200 트리거) ──────────────────────────
    ma_a_step: float = 0.02
    ma_a_pct:  float = 0.10
    ma_a_cnt:  int   = 5
    ma_b_enabled: bool = False
    ma_b_start: float = 0.20
    ma_b_step: float = 0.03
    ma_b_pct:  float = 0.10
    ma_b_cnt:  int   = 5

    # ── 분할매수 노출 캡 ─────────────────────────────────
    split_buy_max_exposure: float = 0.40  # 0.40 = 40%

    # ── 투입 손실 캡 (Staged-Buy Loss Cap) ─────────────────
    use_staged_loss_cap: bool = False           # 기본 OFF (토글)
    staged_loss_cap_pct: float = 0.20           # 사이클 피크 대비 하락률 임계값 (0.20 = 20%)

    # ── 개선 D: 추세 확인 재진입 ─────────────────────────
    use_trend_filter: bool = True
    trend_require_ma5_above_ma200: bool = True
    trend_require_above_ma50: bool = True
    trend_require_ma200_rising: bool = True
    ma200_slope_lookback: int = 20
    trend_filter_mode: str = "OR"


    # ── 관망 구간 헷지 ──────────────────────────────────────
    use_hedge: bool = True
    hedge_alloc_pct: float = 0.80   # 1.0 - cash_pct (app.py에서 변환) → 기본: 현금20%/GLD60%/SHY20%
    hedge_gld_ratio: float = 0.75   # 헷지 내 GLD 비율 (나머지 = SHY)

    # ── MA200 재진입 조건 ───────────────────────────────────
    ma200_reentry_streak: int = 3   # MA5>MA200 연속 N일 (0=즉시 재진입)

    # ── 조건부 무시 필터 (케이스 B-3) ────────────────────────
    use_ignore_filter: bool   = True    # 기본 ON (토글)
    ignore_filter_lookback: int  = 15   # 15거래일 누적수익률 기간
    ignore_filter_thresh: float  = -0.05  # -5% 이하이면 무시 안 하고 매도

    # ── 세금 (한국 거주자 해외주식) ─────────────────────────
    use_tax: bool = True            # 세금 반영 토글
    tax_rate: float = 0.22          # 양도세 20% + 지방소득세 2%
    tax_deduction_usd: float = 1800.0  # 연 공제액 USD (한국 250만원 ≈ $1,800)


@dataclass
class State:
    cash: float = 0.0
    tqqq_shares: float = 0.0

    # ── ATH 추적 (핵심) ───────────────────────────────────
    ath_qqq: float = 0.0          # 동결된 전고점 (랠리 익절 계산 기준)
    running_max_qqq: float = 0.0  # 현재 보유 사이클 누적 최고 QQQ

    rally_level: int = 0           # 0=없음, 1~3=해당 레벨까지 익절 완료

    sell_off_count: int = 0
    initial_pos: bool = True       # 첫 -3% 이벤트 전 최초 포지션

    waiting: bool = False
    wait_end_idx: int = -1
    timer_42_start_idx: int = -1


    trigger_type: str = ""
    split_buy_base: float = 0.0
    split_bought_a: list = field(default_factory=list)
    split_bought_b: list = field(default_factory=list)

    # ── 투입 손실 캡: 사이클 내 TQQQ 평가액 running max ──
    staged_cycle_peak_tqqq: float = 0.0

    ma200_touch_price: float = 0.0
    ma5_above_ma200_streak: int = 0
    last_selloff_idx: int = -1

    # ── 헷지 자산 ──
    gld_shares: float = 0.0
    tlt_shares: float = 0.0

    # ── 세금 추적 (이동평균법) ─────────────────────────────
    tqqq_avg_cost: float = 0.0      # TQQQ 평균 취득단가
    gld_avg_cost: float = 0.0       # GLD 평균 취득단가
    tlt_avg_cost: float = 0.0       # TLT 평균 취득단가
    ytd_realized_pnl: float = 0.0   # 올해 누적 실현손익
    total_tax_paid: float = 0.0     # 누적 납부 세금
    current_year: int = 0           # 연도 변경 감지용



def _nav(s: State, tqqq_price: float,
         gld_price: float = 0.0, tlt_price: float = 0.0) -> float:
    gld_val = s.gld_shares * gld_price if np.isfinite(gld_price) else 0.0
    tlt_val = s.tlt_shares * tlt_price if np.isfinite(tlt_price) else 0.0
    return s.cash + s.tqqq_shares * tqqq_price + gld_val + tlt_val


def _buy_all(s: State, tqqq_price: float) -> float:
    if tqqq_price <= 0 or s.cash <= 0:
        return 0.0
    amount = s.cash
    shares = amount / tqqq_price
    # 평균단가 갱신 (이동평균법)
    _total_cost = s.tqqq_shares * s.tqqq_avg_cost + amount
    s.tqqq_shares += shares
    s.tqqq_avg_cost = _total_cost / s.tqqq_shares if s.tqqq_shares > 0 else 0.0
    s.cash = 0.0
    return shares


def _sell_pct(s: State, pct: float, tqqq_price: float) -> float:
    shares_to_sell = s.tqqq_shares * pct
    proceeds = shares_to_sell * tqqq_price
    # 실현손익 누적 (평균단가는 유지)
    if shares_to_sell > 0:
        s.ytd_realized_pnl += (tqqq_price - s.tqqq_avg_cost) * shares_to_sell
    s.tqqq_shares -= shares_to_sell
    s.cash += proceeds
    return proceeds


def _sell_all(s: State, tqqq_price: float) -> float:
    if s.tqqq_shares > 0:
        s.ytd_realized_pnl += (tqqq_price - s.tqqq_avg_cost) * s.tqqq_shares
    proceeds = s.tqqq_shares * tqqq_price
    s.cash += proceeds
    s.tqqq_shares = 0.0
    return proceeds


def _buy_hedge(s: State, cash_for_hedge: float,
               gld_price: float, tlt_price: float, gld_ratio: float):
    """현금을 GLD/SHY로 분배 매수. NaN 가격이면 해당 비중은 현금 유지."""
    gld_amount = cash_for_hedge * gld_ratio
    tlt_amount = cash_for_hedge * (1 - gld_ratio)
    if np.isfinite(gld_price) and gld_price > 0 and gld_amount > 0:
        _new_sh = gld_amount / gld_price
        _tc = s.gld_shares * s.gld_avg_cost + gld_amount
        s.gld_shares += _new_sh
        s.gld_avg_cost = _tc / s.gld_shares if s.gld_shares > 0 else 0.0
        s.cash -= gld_amount
    if np.isfinite(tlt_price) and tlt_price > 0 and tlt_amount > 0:
        _new_sh = tlt_amount / tlt_price
        _tc = s.tlt_shares * s.tlt_avg_cost + tlt_amount
        s.tlt_shares += _new_sh
        s.tlt_avg_cost = _tc / s.tlt_shares if s.tlt_shares > 0 else 0.0
        s.cash -= tlt_amount


def _sell_hedge(s: State, gld_price: float, tlt_price: float):
    """GLD/SHY 전량 매도 → 현금화."""
    if s.gld_shares > 0 and np.isfinite(gld_price) and gld_price > 0:
        s.ytd_realized_pnl += (gld_price - s.gld_avg_cost) * s.gld_shares
        s.cash += s.gld_shares * gld_price
        s.gld_shares = 0.0
    if s.tlt_shares > 0 and np.isfinite(tlt_price) and tlt_price > 0:
        s.ytd_realized_pnl += (tlt_price - s.tlt_avg_cost) * s.tlt_shares
        s.cash += s.tlt_shares * tlt_price
        s.tlt_shares = 0.0


def _sell_hedge_partial(s: State, cash_needed: float,
                        gld_price: float, tlt_price: float):
    """헷지 자산 일부 매도하여 현금 확보 (GLD/SHY 비율 유지)."""
    gld_val = s.gld_shares * gld_price if (np.isfinite(gld_price) and gld_price > 0) else 0.0
    tlt_val = s.tlt_shares * tlt_price if (np.isfinite(tlt_price) and tlt_price > 0) else 0.0
    total_hedge = gld_val + tlt_val
    if total_hedge <= 0:
        return
    ratio = min(1.0, cash_needed / total_hedge)
    if gld_val > 0 and np.isfinite(gld_price) and gld_price > 0:
        sell_sh = s.gld_shares * ratio
        s.ytd_realized_pnl += (gld_price - s.gld_avg_cost) * sell_sh
        s.cash       += sell_sh * gld_price
        s.gld_shares -= sell_sh
    if tlt_val > 0 and np.isfinite(tlt_price) and tlt_price > 0:
        sell_sh = s.tlt_shares * ratio
        s.ytd_realized_pnl += (tlt_price - s.tlt_avg_cost) * sell_sh
        s.cash       += sell_sh * tlt_price
        s.tlt_shares -= sell_sh


def _split_buy_levels(p: Params, trigger: str):
    levels = []
    if trigger == "selloff":
        for i in range(p.so_a_cnt):
            levels.append(("A", i, p.so_a_step * i, p.so_a_pct))
        if p.so_b_enabled:
            for i in range(p.so_b_cnt):
                levels.append(("B", i, p.so_b_start + p.so_b_step * i, p.so_b_pct))
    elif trigger == "ma200":
        for i in range(p.ma_a_cnt):
            levels.append(("A", i, 0.03 + p.ma_a_step * i, p.ma_a_pct))
        if p.ma_b_enabled:
            for i in range(p.ma_b_cnt):
                levels.append(("B", i, p.ma_b_start + p.ma_b_step * i, p.ma_b_pct))
    return levels


def run_backtest(df: pd.DataFrame, p: Params, initial_capital: float = 100_000.0) -> dict:
    dates     = df.index.tolist()
    qqq       = df["qqq"].values
    tqqq      = df["tqqq"].values
    tqqq_exec = df["tqqq_exec"].values if "tqqq_exec" in df.columns else tqqq
    ma200     = df["ma200"].values
    ma50      = df["ma50"].values if "ma50" in df.columns else ma200.copy()
    ma5       = df["ma5"].values
    qqq_ret   = df["qqq_daily_ret"].values
    vix_arr   = df["vix"].values if "vix" in df.columns else np.zeros(len(dates))

    n = len(dates)
    gld      = df["gld"].values if "gld" in df.columns else np.full(n, np.nan)
    gld_exec = df["gld_exec"].values if "gld_exec" in df.columns else np.full(n, np.nan)
    tlt      = df["tlt"].values if "tlt" in df.columns else np.full(n, np.nan)
    tlt_exec = df["tlt_exec"].values if "tlt_exec" in df.columns else np.full(n, np.nan)

    s = State()
    s.cash           = initial_capital
    s.ath_qqq        = qqq[0]
    s.running_max_qqq = qqq[0]
    s.current_year   = pd.Timestamp(dates[0]).year
    nav_arr = np.zeros(n)
    trades  = []
    signals = []

    def log_trade(i, action, shares, price, reason, gld_val=0.0, tlt_val=0.0):
        trades.append({
            "date":       dates[i],
            "action":     action,
            "shares":     round(shares, 4),
            "tqqq_price": round(price, 4),
            "qqq_price":  round(qqq[i], 4),
            "ath_qqq":    round(s.ath_qqq, 4),
            "gld_val":    round(gld_val, 0),
            "tlt_val":    round(tlt_val, 0),
            "reason":     reason,
        })

    # 첫날 전량 매수
    _buy_all(s, tqqq[0])
    log_trade(0, "BUY_ALL", s.tqqq_shares, tqqq[0], "초기 진입")
    nav_arr[0] = _nav(s, tqqq[0], gld[0], tlt[0])
    signals.append(_make_signal_row(dates[0], "초기 진입", nav_arr[0], s, qqq[0], 0.0, ma200[0], tqqq[0],
                                    gld_price=gld[0], tlt_price=tlt[0],
                                    ytd_realized_pnl=s.ytd_realized_pnl,
                                    total_tax_paid=s.total_tax_paid))

    for i in range(1, n):
        q      = qqq[i]
        t      = tqqq[i]          # 종가: NAV 평가·신호 판단용
        _te    = tqqq_exec[i]
        t_exec = _te if not np.isnan(_te) else t  # 다음날 시가: 실제 매매 실행가
        ret    = qqq_ret[i] if not np.isnan(qqq_ret[i]) else 0.0
        vix    = vix_arr[i] if not np.isnan(vix_arr[i]) else 0.0

        # ── 연도 변경 감지 + 세금 정산 (매매 전 가장 먼저) ────
        _this_year = pd.Timestamp(dates[i]).year
        if p.use_tax and _this_year != s.current_year:
            _prev_year = s.current_year
            _ytd_snapshot = s.ytd_realized_pnl  # 납부 매도 전 스냅샷
            _taxable = _ytd_snapshot - p.tax_deduction_usd

            if _taxable > 0:
                _tax = _taxable * p.tax_rate
                _tax_total = _tax

                # 1순위: 현금에서 차감
                _from_cash = min(s.cash, _tax)
                s.cash -= _from_cash
                _remaining = _tax - _from_cash

                # 2순위: 헷지 자산 일부 매도
                if _remaining > 0 and (s.gld_shares > 0 or s.tlt_shares > 0):
                    _sell_hedge_partial(s, _remaining, gld_exec[i], tlt_exec[i])
                    _from_hedge = min(s.cash, _remaining)
                    s.cash -= _from_hedge
                    _remaining -= _from_hedge

                # 3순위: TQQQ 일부 매도
                if _remaining > 0 and s.tqqq_shares > 0 and t_exec > 0:
                    _need_shares = _remaining / t_exec
                    if _need_shares <= s.tqqq_shares:
                        _sell_pct(s, _need_shares / s.tqqq_shares, t_exec)
                    else:
                        _sell_all(s, t_exec)
                    _from_tqqq = min(s.cash, _remaining)
                    s.cash -= _from_tqqq
                    _remaining -= _from_tqqq

                s.total_tax_paid += _tax_total
                log_trade(i, "TAX_PAID", 0, 0,
                          f"{_prev_year}년 세금: 실현손익 ${_ytd_snapshot:,.0f} → 세금 ${_tax_total:,.0f}")

                if _remaining > 0:
                    log_trade(i, "TAX_SHORTFALL", 0, 0,
                              f"세금 부족분 ${_remaining:,.0f}")

            # 스냅샷만큼만 차감 (납부 매도의 손익은 새 연도에 남김)
            s.ytd_realized_pnl -= _ytd_snapshot
            s.current_year = _this_year

        # ── MA5/MA200 연속 카운트 ──────────────────────────
        s.ma5_above_ma200_streak = (s.ma5_above_ma200_streak + 1) if ma5[i] > ma200[i] else 0

        # ── running_max 갱신 (보유 중에만) ──────────────────
        # ath_qqq는 이벤트 발생 시에만 업데이트 (여기선 건드리지 않음)
        if s.tqqq_shares > 0:
            if q > s.running_max_qqq:
                s.running_max_qqq = q
            # 초기 포지션 기간에는 ath_qqq도 running_max와 같이 올라감
            if s.initial_pos:
                s.ath_qqq = s.running_max_qqq

        # ── 하락감시 타임아웃: -3% 무시 후 24거래일 경과 시 카운트 리셋 ──
        if (s.sell_off_count >= 1 and not s.waiting
                and s.tqqq_shares > 0 and s.trigger_type == ""
                and s.last_selloff_idx >= 0
                and i - s.last_selloff_idx >= p.wait_short):
            s.sell_off_count = 0
            s.last_selloff_idx = -1

        # ── 이벤트 처리 (우선순위 순) ─────────────────────
        event_handled = False
        day_signal    = ""

        # ①-1  -3% 이벤트
        if ret <= p.selloff_thresh:
            triggered = False
            _conditional_sell = False

            if s.initial_pos:
                # 최초 포지션: 1차도 즉시 전량 매도
                triggered = True
                s.initial_pos = False
            elif s.tqqq_shares > 0:
                if s.trigger_type == "ma200":
                    triggered = True
                elif s.trigger_type == "":
                    if s.rally_level >= 1:
                        # 랠리 상태 → 전량매도
                        triggered = True
                    elif s.sell_off_count >= 1:
                        # 랠리 이전 2차 이상 → 전량매도
                        triggered = True
                    else:
                        # 랠리 이전 1차: 조건부 무시 필터 체크
                        _qqq_10d_prev = qqq[max(0, i - p.ignore_filter_lookback)]
                        _qqq_ret_10d = (q / _qqq_10d_prev - 1) if _qqq_10d_prev > 0 else 0.0

                        if p.use_ignore_filter and _qqq_ret_10d <= p.ignore_filter_thresh:
                            # 10일 누적 하락이 임계값 이하 → 하락 추세이므로 전량매도
                            triggered = True
                            _conditional_sell = True
                        else:
                            # 기존대로 무시
                            s.sell_off_count += 1
                            s.last_selloff_idx = i
                            day_signal = f"-3% 무시 (랠리 이전 1차) | count={s.sell_off_count}"
                            if p.use_ignore_filter:
                                day_signal += f" | 10d={_qqq_ret_10d*100:.1f}%"
                            log_trade(i, "SELLOFF_IGNORED", 0, 0, day_signal)
                            event_handled = True
                elif s.trigger_type == "selloff":
                    # 관망 중 분할매수로 쌓인 포지션은 유지, 카운트/타이머만 업데이트
                    s.sell_off_count += 1
                    s.last_selloff_idx = i

                    if s.sell_off_count < p.heavy_sell_count:
                        s.wait_end_idx = i + p.wait_short

                    elif s.sell_off_count == p.heavy_sell_count:
                        # 4차 정확히: 42일 타이머 시작, 분할매수 기준가는 아직 없음
                        s.wait_end_idx = -1
                        s.timer_42_start_idx = i
                    else:
                        # 5차+: 타이머 리셋, 기준가는 최초 1회만 설정
                        s.timer_42_start_idx = i
                        if s.split_buy_base == 0.0:
                            s.split_buy_base = q
                            s.split_bought_a = []
                            s.split_bought_b = []

                    day_signal = f"-3% 추가 하락 ({s.sell_off_count}차, 분할매수 포지션 유지)"
                    log_trade(i, "SELLOFF_WAIT", 0, 0, day_signal)
                    event_handled = True

            else:
                # 관망 중 -3% (tqqq=0): sell_off_count 누적 + 상태 전환
                if s.trigger_type == "selloff":
                    s.sell_off_count += 1
                    s.last_selloff_idx = i

                    if s.sell_off_count < p.heavy_sell_count:
                        # 1~3차: 대기일 리셋 (21일 다시 시작)
                        s.wait_end_idx = i + p.wait_short

                    elif s.sell_off_count == p.heavy_sell_count:
                        # 4차 정확히: 42일 타이머 시작, 분할매수 기준가는 아직 없음
                        s.wait_end_idx = -1
                        s.timer_42_start_idx = i
                        # base=0 유지 → 5차 -3% 대기
                    else:
                        # 5차+: 타이머 리셋, 기준가는 최초 1회만 설정
                        s.timer_42_start_idx = i
                        if s.split_buy_base == 0.0:
                            s.split_buy_base = q
                            s.split_bought_a = []
                            s.split_bought_b = []

                    day_signal = f"-3% 추가 하락 ({s.sell_off_count}차)"
                    log_trade(i, "SELLOFF_WAIT", 0, 0, day_signal)
                    event_handled = True

                elif s.trigger_type == "ma200":
                    s.timer_42_start_idx = i

            if triggered:
                # ★ ATH 확정: 이벤트 발생 순간 running_max와 비교해 높은 값 유지
                # ATH는 절대 내려가지 않음 (하락장 반등 시 낮은 값으로 교체 방지)
                s.ath_qqq = max(s.ath_qqq, s.running_max_qqq)
                s.sell_off_count += 1
                s.initial_pos = False
                _sell_all(s, t_exec)

                if _conditional_sell:
                    day_signal = f"-3% 조건부매도 (10d누적 {_qqq_ret_10d*100:.1f}%) | count={s.sell_off_count}"
                    _action_label = "SELLOFF_CONDITIONAL_SELL"
                else:
                    day_signal = f"-3% 전량매도 ({s.sell_off_count}차) | ATH={s.ath_qqq:.2f}"
                    _action_label = "SELL_ALL"
                log_trade(i, _action_label, 0, t_exec, day_signal)
                s.rally_level    = 0
                s.trigger_type   = "selloff"

                if s.sell_off_count < p.heavy_sell_count:
                    s.waiting        = True
                    s.wait_end_idx   = i + p.wait_short
                    s.timer_42_start_idx = -1
                    s.split_buy_base = 0.0  # 이전 cycle 기준가 초기화 (동일 날 분할매수 방지)
                    s.split_bought_a = []
                    s.split_bought_b = []
                else:
                    s.waiting      = True
                    s.wait_end_idx = -1
                    s.timer_42_start_idx = i
                    s.split_buy_base = 0.0  # 4차+: 기준가 없음, 다음 -3%(5차) 대기
                    s.split_bought_a = []
                    s.split_bought_b = []

                # ── 헷지 매수 (매도 직후 동일 시가) ──
                if p.use_hedge and s.cash > 0:
                    _hedge_cash = s.cash * p.hedge_alloc_pct
                    _gld_a = _hedge_cash * p.hedge_gld_ratio
                    _tlt_a = _hedge_cash * (1 - p.hedge_gld_ratio)
                    _buy_hedge(s, _hedge_cash, gld_exec[i], tlt_exec[i], p.hedge_gld_ratio)
                    day_signal += f" | 헷지매수 GLD ${_gld_a:,.0f}+SHY ${_tlt_a:,.0f}"
                    log_trade(i, "HEDGE_BUY", 0, 0,
                              f"헷지 매수: GLD ${_gld_a:,.0f} + SHY ${_tlt_a:,.0f}",
                              gld_val=_gld_a, tlt_val=_tlt_a)

                s.last_selloff_idx    = i
                s.ma200_touch_price   = 0.0
                event_handled         = True

        # ①-2  MA200 터치 (랠리 상태에서만, use_ma200_sell=True 일 때만)
        if not event_handled and p.use_ma200_sell and s.rally_level > 0 and s.tqqq_shares > 0:
            if q <= ma200[i] * p.ma200_mult:
                # ★ ATH 확정 (절대 내려가지 않음)
                s.ath_qqq = max(s.ath_qqq, s.running_max_qqq)
                s.sell_off_count    += 1
                s.ma200_touch_price  = q
                _sell_all(s, t_exec)
                day_signal = f"MA200 전량매도 (QQQ={q:.2f}) | ATH={s.ath_qqq:.2f}"
                log_trade(i, "SELL_ALL_MA200", 0, t_exec, day_signal)
                s.rally_level              = 0
                s.trigger_type             = "ma200"
                s.waiting                  = True
                s.wait_end_idx             = i + p.ma200_wait_days   # MA200 매도 후 최소 관망
                s.split_buy_base           = q
                s.split_bought_a           = []
                s.ma5_above_ma200_streak   = 0  # 매도 시점의 streak 초기화 (재진입 조건 새로 카운트)
                s.split_bought_b  = []
                s.timer_42_start_idx = -1
                s.last_selloff_idx   = i   # MA200 매도 기준일 (경과일 계산용)
                # ── 헷지 매수 (매도 직후 동일 시가) ──
                if p.use_hedge and s.cash > 0:
                    _hedge_cash = s.cash * p.hedge_alloc_pct
                    _gld_a = _hedge_cash * p.hedge_gld_ratio
                    _tlt_a = _hedge_cash * (1 - p.hedge_gld_ratio)
                    _buy_hedge(s, _hedge_cash, gld_exec[i], tlt_exec[i], p.hedge_gld_ratio)
                    day_signal += f" | 헷지매수 GLD ${_gld_a:,.0f}+SHY ${_tlt_a:,.0f}"
                    log_trade(i, "HEDGE_BUY", 0, 0,
                              f"헷지 매수: GLD ${_gld_a:,.0f} + SHY ${_tlt_a:,.0f}",
                              gld_val=_gld_a, tlt_val=_tlt_a)
                event_handled        = True

        # ②  관망 종료 재진입
        #    분할매수로 tqqq_shares > 0이어도 재진입 가능
        #    (분할매수 포지션 유지 + 남은 현금/헷지로 추가 TQQQ 매수)
        if not event_handled and s.waiting:
            reenter = False
            reason  = ""

            if s.trigger_type == "selloff":
                if s.sell_off_count < p.heavy_sell_count:
                    if i >= s.wait_end_idx:
                        reenter = True
                        reason  = f"{p.wait_short}거래일 관망 종료 → 전량 재진입"
                else:
                    if s.timer_42_start_idx >= 0 and (i - s.timer_42_start_idx) >= p.wait_long:
                        reenter = True
                        reason  = "24일 타이머 종료 → 전량 재진입"
            elif s.trigger_type == "ma200":
                if i >= s.wait_end_idx:
                    reenter = True
                    reason  = f"MA200 {p.ma200_wait_days}거래일 관망 + 추세 필터 통과 시 재진입"

            # ── 개선 D: 추세 필터 게이트 ──────────────────
            if reenter and p.use_trend_filter:
                ma5_ok      = (s.ma5_above_ma200_streak >= p.ma200_reentry_streak
                               if p.trend_require_ma5_above_ma200 else False)
                above_ma50  = q > ma50[i] if p.trend_require_above_ma50 else False
                ma200_rise  = (ma200[i] > ma200[max(0, i - p.ma200_slope_lookback)]
                               if p.trend_require_ma200_rising else False)

                if p.trend_filter_mode == "AND":
                    trend_ok = (ma5_ok if p.trend_require_ma5_above_ma200 else True) and \
                               (above_ma50 if p.trend_require_above_ma50 else True) and \
                               (ma200_rise if p.trend_require_ma200_rising else True)
                else:  # OR
                    checks = []
                    if p.trend_require_ma5_above_ma200:
                        checks.append(ma5_ok)
                    if p.trend_require_above_ma50:
                        checks.append(above_ma50)
                    if p.trend_require_ma200_rising:
                        checks.append(ma200_rise)
                    trend_ok = any(checks) if checks else True

                if not trend_ok:
                    reenter = False
                    reason  = ""

            if reenter:
                # ── 헷지 매도 (재진입 전 동일 시가) ──
                if p.use_hedge:
                    _gld_p = round(s.gld_shares * gld_exec[i], 0) if s.gld_shares > 0 else 0.0
                    _tlt_p = round(s.tlt_shares * tlt_exec[i], 0) if s.tlt_shares > 0 else 0.0
                    _sell_hedge(s, gld_exec[i], tlt_exec[i])
                    if _gld_p + _tlt_p > 0:
                        log_trade(i, "HEDGE_SELL", 0, 0,
                                  f"헷지 매도: GLD ${_gld_p:,.0f} + SHY ${_tlt_p:,.0f} → 현금화",
                                  gld_val=_gld_p, tlt_val=_tlt_p)
                _buy_all(s, t_exec)
                # ★ 재진입 시 running_max를 재진입 당일 QQQ로 리셋
                #    ath_qqq는 이전 이벤트 때 확정된 값 그대로 유지
                s.running_max_qqq = q
                _reentry_tqqq_val = round(s.tqqq_shares * t_exec, 0)
                _reentry_reason = reason + f" | TQQQ ${_reentry_tqqq_val:,.0f} 매수"
                log_trade(i, "BUY_ALL", s.tqqq_shares, t_exec, _reentry_reason)
                day_signal = _reentry_reason
                s.waiting            = False
                s.trigger_type       = ""
                s.split_buy_base     = 0.0
                s.split_bought_a     = []
                s.split_bought_b     = []
                s.staged_cycle_peak_tqqq = 0.0
                s.timer_42_start_idx = -1
                s.sell_off_count     = 0
                s.last_selloff_idx   = -1
                event_handled        = True

        # ④  분할매수
        if s.waiting:
            # ── 투입 손실 캡: 사이클 피크 업데이트 ──
            if p.use_staged_loss_cap:
                _cur_tqqq_val = s.tqqq_shares * t
                if _cur_tqqq_val > s.staged_cycle_peak_tqqq:
                    s.staged_cycle_peak_tqqq = _cur_tqqq_val
            levels = _split_buy_levels(p, s.trigger_type)
            for zone, level_idx, drop_pct, buy_pct in levels:
                key     = (zone, level_idx)
                already = (key in s.split_bought_a) if zone == "A" else (key in s.split_bought_b)
                if already:
                    continue
                # ── 분할매수 노출 캡: TQQQ 비중이 한도 이상이면 skip ──
                # 신호 판단은 종가(t) 기준, 실제 매수 실행은 다음날 시가(t_exec)
                if p.split_buy_max_exposure < 1.0:
                    _cap_tqqq = s.tqqq_shares * t
                    _cap_nav  = _cap_tqqq + s.cash + \
                                ((s.gld_shares * gld[i]) if (np.isfinite(gld[i]) and gld[i] > 0) else 0.0) + \
                                ((s.tlt_shares * tlt[i]) if (np.isfinite(tlt[i]) and tlt[i] > 0) else 0.0)
                    if _cap_nav > 0 and _cap_tqqq >= _cap_nav * p.split_buy_max_exposure:
                        continue
                # ── 투입 손실 캡: 사이클 피크 대비 하락률이 임계값 초과 시 차단 ──
                if p.use_staged_loss_cap and s.staged_cycle_peak_tqqq > 0:
                    _cur_tqqq_val = s.tqqq_shares * t
                    _dd_from_peak = (s.staged_cycle_peak_tqqq - _cur_tqqq_val) / s.staged_cycle_peak_tqqq
                    if _dd_from_peak >= p.staged_loss_cap_pct:
                        continue
                if s.split_buy_base > 0 and q <= s.split_buy_base * (1 - drop_pct):
                    # 매 레벨 발동 직전 잔여 헷지 자산 재계산 (남은 자산의 10% 올바르게 적용)
                    hedge_val    = ((s.gld_shares * gld[i]) if (np.isfinite(gld[i]) and gld[i] > 0) else 0.0) + \
                                   ((s.tlt_shares * tlt[i]) if (np.isfinite(tlt[i]) and tlt[i] > 0) else 0.0)
                    total_liquid = s.cash + hedge_val
                    if total_liquid <= 0:
                        continue
                    amount = total_liquid * buy_pct
                    if amount <= 0:
                        continue
                    # 현금 부족 시 헷지 일부 매도하여 충당
                    if amount > s.cash:
                        _sell_hedge_partial(s, amount - s.cash,
                                            gld_exec[i], tlt_exec[i])
                    amount = min(amount, s.cash)  # 안전 상한
                    if amount <= 0:
                        continue
                    shares_bought = amount / t_exec
                    # 평균단가 갱신 (이동평균법) — _buy_all 미경유
                    _tc = s.tqqq_shares * s.tqqq_avg_cost + amount
                    s.cash        -= amount
                    s.tqqq_shares += shares_bought
                    s.tqqq_avg_cost = _tc / s.tqqq_shares if s.tqqq_shares > 0 else 0.0
                    (s.split_bought_a if zone == "A" else s.split_bought_b).append(key)
                    sig = f"분할매수 {zone}{level_idx+1} (즉시매수)" if drop_pct == 0 else f"분할매수 {zone}{level_idx+1} (기준가 -{drop_pct*100:.0f}%)"
                    log_trade(i, f"SPLIT_BUY_{zone}{level_idx+1}", shares_bought, t_exec, sig)
                    if not day_signal:
                        day_signal = sig
                    else:
                        day_signal = day_signal + " + " + sig

        # ④-후처리  관망 중 잔여 현금 → 헷지 재투자
        # 현금 비중(1-hedge_alloc_pct) 목표를 초과하는 현금만 헷지로 이동
        if s.waiting and s.cash > 0 and p.use_hedge and p.hedge_alloc_pct > 0:
            _hv = ((s.gld_shares * gld[i]) if (np.isfinite(gld[i]) and gld[i] > 0) else 0.0) + \
                  ((s.tlt_shares * tlt[i]) if (np.isfinite(tlt[i]) and tlt[i] > 0) else 0.0)
            _tl = s.cash + _hv
            _target_cash = _tl * (1 - p.hedge_alloc_pct)
            _excess = s.cash - _target_cash
            if _excess > 0:
                _buy_hedge(s, _excess, gld_exec[i], tlt_exec[i], p.hedge_gld_ratio)

        # ③  랠리 익절 체크
        #    gain = (오늘 QQQ - ath_qqq) / ath_qqq
        #    ath_qqq는 가장 최근 -3%/MA200 이벤트 때 확정된 전고점
        if not event_handled and s.tqqq_shares > 0 and s.trigger_type == "":
            ath  = s.ath_qqq
            gain = (q - ath) / ath if ath > 0 else 0.0

            if s.rally_level < 1 and gain >= p.rally_thresh_1:
                proceeds = _sell_pct(s, p.rally_sell_pct_1, t_exec)
                if p.rally_sell_to_hedge and proceeds > 0:
                    _buy_hedge(s, proceeds, gld_exec[i], tlt_exec[i], p.hedge_gld_ratio)
                s.rally_level = 1
                sig = f"랠리 1단계 (ATH {ath:.2f} 대비 +{gain*100:.1f}%, {p.rally_sell_pct_1*100:.0f}% 매도)"
                log_trade(i, "RALLY_SELL_1", 0, t_exec, sig)
                if not day_signal:
                    day_signal = sig
            elif s.rally_level < 2 and gain >= p.rally_thresh_2:
                proceeds = _sell_pct(s, p.rally_sell_pct_2, t_exec)
                if p.rally_sell_to_hedge and proceeds > 0:
                    _buy_hedge(s, proceeds, gld_exec[i], tlt_exec[i], p.hedge_gld_ratio)
                s.rally_level = 2
                sig = f"랠리 2단계 (ATH {ath:.2f} 대비 +{gain*100:.1f}%, {p.rally_sell_pct_2*100:.0f}% 매도)"
                log_trade(i, "RALLY_SELL_2", 0, t_exec, sig)
                if not day_signal:
                    day_signal = sig
            elif s.rally_level < 3 and gain >= p.rally_thresh_3:
                proceeds = _sell_pct(s, p.rally_sell_pct_3, t_exec)
                if p.rally_sell_to_hedge and proceeds > 0:
                    _buy_hedge(s, proceeds, gld_exec[i], tlt_exec[i], p.hedge_gld_ratio)
                s.rally_level = 3
                sig = f"랠리 3단계 (ATH {ath:.2f} 대비 +{gain*100:.1f}%, {p.rally_sell_pct_3*100:.0f}% 매도)"
                log_trade(i, "RALLY_SELL_3", 0, t_exec, sig)
                if not day_signal:
                    day_signal = sig


        # ── 시그널 텍스트 ─────────────────────────────────
        if not day_signal:
            if s.waiting and s.tqqq_shares == 0:
                elapsed = i - max(
                    s.last_selloff_idx,
                    s.timer_42_start_idx if s.timer_42_start_idx >= 0 else s.last_selloff_idx
                )
                extra_msgs = []
                if p.use_trend_filter:
                    # 추세 필터 미충족 상태 표시 (어떤 조건이 실패했는지 구체적으로)
                    _ma5ok = (s.ma5_above_ma200_streak >= p.ma200_reentry_streak
                              ) if p.trend_require_ma5_above_ma200 else True
                    _above = (q > ma50[i]) if p.trend_require_above_ma50 else True
                    _rise  = (ma200[i] > ma200[max(0, i - p.ma200_slope_lookback)]
                              ) if p.trend_require_ma200_rising else True
                    if p.trend_filter_mode == "AND":
                        _tok = _ma5ok and _above and _rise
                    else:
                        _chk = []
                        if p.trend_require_ma5_above_ma200: _chk.append(_ma5ok)
                        if p.trend_require_above_ma50:  _chk.append(_above)
                        if p.trend_require_ma200_rising: _chk.append(_rise)
                        _tok = any(_chk) if _chk else True
                    if not _tok:
                        _fail = []
                        if p.trend_require_ma5_above_ma200 and not _ma5ok: _fail.append(f"MA5<MA200({s.ma5_above_ma200_streak}일)")
                        if p.trend_require_above_ma50  and not _above: _fail.append("QQQ<MA50")
                        if p.trend_require_ma200_rising and not _rise:  _fail.append("MA200하락중")
                        _fail_str = ",".join(_fail) if _fail else "조건미충족"
                        extra_msgs.append(f"재진입보류({_fail_str})")
                suffix = (" | " + " | ".join(extra_msgs)) if extra_msgs else ""
                day_signal = f"관망 ({max(1, elapsed)}일째){suffix}"
            elif s.waiting and s.tqqq_shares > 0:
                day_signal = "분할매수 보유 중"
            elif s.sell_off_count >= 1 and not s.waiting and s.tqqq_shares > 0:
                _watch_days = i - s.last_selloff_idx
                day_signal = f"보유 (하락감시 {_watch_days}일째, count={s.sell_off_count})"
            else:
                day_signal = "보유"

        nav_arr[i] = _nav(s, t, gld[i], tlt[i])
        signals.append(_make_signal_row(dates[i], day_signal, nav_arr[i], s, q, ret, ma200[i], t,
                                        vix if vix > 0 else np.nan,
                                        gld_price=gld[i], tlt_price=tlt[i],
                                        ytd_realized_pnl=s.ytd_realized_pnl,
                                        total_tax_paid=s.total_tax_paid))

    # ── 마지막 연도 세금 정산 ──────────────────────────────
    if p.use_tax and s.ytd_realized_pnl > 0:
        _final_taxable = s.ytd_realized_pnl - p.tax_deduction_usd
        if _final_taxable > 0:
            _final_tax = _final_taxable * p.tax_rate
            _last_i = n - 1
            _last_t = tqqq[_last_i]
            _last_g = gld[_last_i]
            _last_l = tlt[_last_i]
            _ytd_snap = s.ytd_realized_pnl

            # 1순위: 현금
            _from_cash = min(s.cash, _final_tax)
            s.cash -= _from_cash
            _remaining = _final_tax - _from_cash

            # 2순위: 헷지
            if _remaining > 0 and (s.gld_shares > 0 or s.tlt_shares > 0):
                _sell_hedge_partial(s, _remaining, _last_g, _last_l)
                _from_hedge = min(s.cash, _remaining)
                s.cash -= _from_hedge
                _remaining -= _from_hedge

            # 3순위: TQQQ
            if _remaining > 0 and s.tqqq_shares > 0 and _last_t > 0:
                _need = _remaining / _last_t
                if _need <= s.tqqq_shares:
                    _sell_pct(s, _need / s.tqqq_shares, _last_t)
                else:
                    _sell_all(s, _last_t)
                _from_tqqq = min(s.cash, _remaining)
                s.cash -= _from_tqqq

            s.total_tax_paid += _final_tax
            s.ytd_realized_pnl -= _ytd_snap
            # 마지막 NAV 재계산
            nav_arr[-1] = _nav(s, _last_t, _last_g, _last_l)

    nav_series = pd.Series(nav_arr, index=dates, name="nav")
    trades_df  = pd.DataFrame(trades)
    signals_df = pd.DataFrame(signals)

    rolling_max = nav_series.cummax()
    signals_df["drawdown(%)"] = ((nav_series - rolling_max) / rolling_max * 100).values

    return {
        "nav":             nav_series,
        "trades":          trades_df,
        "signals":         signals_df,
        "final_nav":       nav_arr[-1],
        "initial_capital": initial_capital,
        "total_tax_paid":  s.total_tax_paid,
    }


def _make_signal_row(date, signal, nav, s: State, qqq_price, qqq_ret,
                     ma200_val, tqqq_price, vix=np.nan,
                     gld_price=0.0, tlt_price=0.0,
                     ytd_realized_pnl=0.0, total_tax_paid=0.0):
    stock_val = s.tqqq_shares * tqqq_price
    gld_val   = s.gld_shares * gld_price if np.isfinite(gld_price) else 0.0
    tlt_val   = s.tlt_shares * tlt_price if np.isfinite(tlt_price) else 0.0
    hedge_val = gld_val + tlt_val
    total     = stock_val + hedge_val + s.cash
    stock_wt  = (stock_val / total * 100) if total > 0 else 0.0
    ma200_dev = ((qqq_price - ma200_val) / ma200_val * 100) if ma200_val > 0 else 0.0
    gain_vs_ath = ((qqq_price - s.ath_qqq) / s.ath_qqq * 100) if s.ath_qqq > 0 else 0.0

    return {
        "date":             pd.Timestamp(date).strftime("%Y-%m-%d"),
        "signal":           signal,
        "nav":              round(nav, 0),
        "stock_weight(%)":  round(stock_wt, 1),
        "drawdown(%)":      0.0,
        "ath_qqq":          round(s.ath_qqq, 2),
        "qqq_vs_ath(%)":    round(gain_vs_ath, 1),
        "ma200":            round(ma200_val, 2),
        "ma200_dev(%)":     round(ma200_dev, 1),
        "qqq_change(%)":    round(qqq_ret * 100, 2),
        "qqq":              round(qqq_price, 2),
        "stock_value":      round(stock_val, 0),
        "hedge_value":      round(hedge_val, 0),
        "cash_value":       round(s.cash, 0),
        "vix":              round(vix, 1) if not np.isnan(vix) else None,
        "ytd_realized_pnl": round(ytd_realized_pnl, 0),
        "total_tax_paid":   round(total_tax_paid, 0),
    }
