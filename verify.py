# -*- coding: utf-8 -*-
"""
verify.py - 전략 규칙 자동 검증 스크립트
실행: python verify.py
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import numpy as np
import pandas as pd
from backtest import run_backtest, Params

results = []

def make_df(qqq_prices, ma200_val=70.0):
    """합성 가격 시리즈로 테스트용 DataFrame 생성"""
    n = len(qqq_prices)
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    qqq   = np.array(qqq_prices, dtype=float)
    tqqq  = qqq * 0.5                      # TQQQ = QQQ 절반 (단순화)
    ma200 = np.full(n, ma200_val)           # MA200은 QQQ 훨씬 아래 고정
    ma5   = np.full(n, ma200_val + 5.0)    # MA5 > MA200 (MA200 터치 방지)
    qqq_ret = np.zeros(n)
    qqq_ret[1:] = (qqq[1:] - qqq[:-1]) / qqq[:-1]
    return pd.DataFrame({
        "qqq": qqq, "tqqq": tqqq,
        "ma200": ma200, "ma5": ma5,
        "qqq_daily_ret": qqq_ret,
    }, index=dates)

def check(name, condition, detail=""):
    results.append((name, condition))
    status = "✅ PASS" if condition else "❌ FAIL"
    suffix = "" if condition else "  ⬅ 버그!"
    print(f"  {status}  {name}{suffix}")
    if detail:
        print(f"         → {detail}")

p = Params()  # 기본 파라미터

# ─────────────────────────────────────────────────────────────────────
# 공통 시나리오 (테스트 1·2·3·5 공유)
#
#  Index  QQQ      이벤트
#  -----  -------  ----------------------------------------
#   0     100      초기 매수 (initial_pos=True)
#   1     105      상승, ATH=105
#   2     110      상승, ATH=110  ← 최고점
#   3     107      하락, ATH=110 유지
#   4     103.62   -3.16% → -3% 트리거! ATH=110 확정, wait_end=25
#   5~24  104      관망 (20일)
#   25    104      21거래일째 → 재진입 (sell_off_count 리셋)
#   26    120      gain=(120-110)/110=9.09% → 랠리 1단계 발동 기대
# ─────────────────────────────────────────────────────────────────────
prices_main = [100, 105, 110, 107, 103.62] + [104] * 21 + [120]
df_main = make_df(prices_main)
r_main  = run_backtest(df_main, p)
trades_main  = r_main["trades"]
signals_main = r_main["signals"]

sells_main = trades_main[trades_main["action"].isin(["SELL_ALL", "SELL_ALL_MA200"])]
buys_main  = trades_main[trades_main["action"] == "BUY_ALL"]


print()
print("=" * 58)
print("  TQQQ 전략 규칙 자동 검증")
print("=" * 58)

# ══════════════════════════════════════════════════════════════
print("\n[규칙 1]  ATH 동결 - -3% 발생 시 직전 최고점(110)으로 확정")
# ══════════════════════════════════════════════════════════════

ath_at_sell = sells_main["ath_qqq"].iloc[0] if len(sells_main) else None
check("ATH = 110.00 으로 동결",
      ath_at_sell is not None and abs(ath_at_sell - 110) < 0.01,
      f"실제 ATH = {ath_at_sell}")

# ══════════════════════════════════════════════════════════════
print("\n[규칙 2]  ATH 유지 - 재진입 후 QQQ가 올라도 ATH 불변")
# ══════════════════════════════════════════════════════════════

reentry = buys_main.iloc[1] if len(buys_main) > 1 else None  # 초기진입 제외
if reentry is not None:
    after_sig  = signals_main[signals_main["date"] > str(reentry["date"])].head(3)
    ath_after  = after_sig["ath_qqq"].values
    check("재진입 후 ATH = 110.00 유지 (갱신 안 함)",
          len(ath_after) > 0 and all(abs(v - 110) < 0.01 for v in ath_after),
          f"재진입 후 ATH 값: {ath_after}")
else:
    check("재진입 후 ATH 유지", False, "재진입이 발생하지 않음")

# ══════════════════════════════════════════════════════════════
print("\n[규칙 3]  랠리 익절 - ATH 대비 +9% 초과 시 1단계 발동")
# ══════════════════════════════════════════════════════════════

rally1 = trades_main[trades_main["action"] == "RALLY_SELL_1"]
check("RALLY_SELL_1 발동됨",
      len(rally1) > 0,
      f"발동 횟수: {len(rally1)}")

if len(rally1):
    rq   = rally1["qqq_price"].iloc[0]
    ath  = rally1["ath_qqq"].iloc[0]
    gain = (rq - ath) / ath
    check(f"발동 시 gain={gain*100:.1f}% ≥ 9%",
          gain >= 0.09,
          f"QQQ={rq:.2f}, ATH={ath:.2f}")

# ══════════════════════════════════════════════════════════════
print("\n[규칙 4]  sell_off_count 리셋 - 재진입 후 다음 -3%는 1차")
# ══════════════════════════════════════════════════════════════
#
#  Index  QQQ     이벤트
#  -----  ------  ----------------------------------------
#   0     100     초기 매수
#   1     96.9    -3.1% → 1차 매도 (count=1), wait_end=22
#   2~22  97      관망 (21일)
#   22    97      재진입 → sell_off_count 리셋(=0)
#   23    93.97   -3.12% → 다시 1차여야 함 (count=0→1)
#   24~28 94      (패딩)
# ──────────────────────────────────────────────────────────────

prices_reset = [100, 96.9] + [97] * 21 + [93.97] + [94] * 5
df_reset = make_df(prices_reset)
r_reset  = run_backtest(df_reset, p)
sells_r  = r_reset["trades"][r_reset["trades"]["action"] == "SELL_ALL"]

if len(sells_r) >= 2:
    reason1 = sells_r["reason"].iloc[0]
    reason2 = sells_r["reason"].iloc[1]
    check("2번째 -3%도 '1차'로 카운트 (2차 아님)",
          "1차" in reason2,
          f"1번째 매도: {reason1}\n         2번째 매도: {reason2}")
else:
    check("2번째 -3% 1차 확인", False,
          f"SELL 횟수={len(sells_r)} (2회 필요, 데이터 부족)")

# ══════════════════════════════════════════════════════════════
print("\n[규칙 5]  관망 기간 - -3% 후 정확히 21거래일째 재진입")
# ══════════════════════════════════════════════════════════════

if reentry is not None:
    sell_date    = pd.Timestamp(sells_main.iloc[0]["date"])
    reentry_date = pd.Timestamp(reentry["date"])
    sell_idx    = df_main.index.get_loc(sell_date)
    reentry_idx = df_main.index.get_loc(reentry_date)
    gap = reentry_idx - sell_idx
    check(f"재진입이 SELL 후 21거래일째 (실제: {gap}일)",
          gap == 21,
          f"SELL 날짜: {sell_date.date()}, 재진입 날짜: {reentry_date.date()}")
else:
    check("21거래일 관망 확인", False, "재진입이 발생하지 않음")

# ══════════════════════════════════════════════════════════════
# 결과 요약
# ══════════════════════════════════════════════════════════════
passed = sum(1 for _, ok in results if ok)
total  = len(results)

print()
print("=" * 58)
print(f"  결과: {passed} / {total} 통과")
if passed == total:
    print("  ✅ 모든 규칙 정상 동작!")
else:
    failed_names = [name for name, ok in results if not ok]
    print(f"  ❌ 문제 항목:")
    for name in failed_names:
        print(f"       - {name}")
print("=" * 58)
print()

sys.exit(0 if passed == total else 1)
