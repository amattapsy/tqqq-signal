# TQQQ 매매 전략 매뉴얼 (AI 구현용 단일 정보 소스)

> 이 문서는 `backtest.py`를 작성하거나 수정할 때 유일한 기준이 됩니다.
> 코드와 이 문서가 충돌하면 **이 문서가 우선**입니다.
> 설계 의도가 명시된 경우 코드를 이 문서에 맞게 수정해야 합니다.

---

## 0. 핵심 설계 원칙

### 포트폴리오 이진 구조
이 전략은 **TQQQ(공격 모드)** vs **헷지자산(방어 모드)** 두 상태만 존재한다.

| 모드 | 보유 자산 | 전환 시점 |
|------|----------|----------|
| **공격** | TQQQ 100% | 재진입 시 |
| **방어** | 현금 + GLD + TLT | -3% 전량매도 또는 MA200 전량매도 시 |

- 헷지자산 = 현금 + GLD + TLT (기본 비율: 현금 30% / GLD 35% / TLT 35%)
- 공격 → 방어 전환: TQQQ 전량매도 → 매도 대금을 헷지자산으로 분배
- 방어 → 공격 전환: 헷지자산 전량 현금화 → TQQQ 전량매수

### 신호와 매매
- **신호 자산**: QQQ (매매 신호 판단에만 사용)
- **매매 자산**: TQQQ (실제 매수/매도)
- **체결 가격**: 당일 종가(`qqq`)로 신호 판단 → **다음날 시가**(`tqqq_exec`)로 거래 체결
  - `tqqq_exec = tqqq_open.shift(-1)` — 당일 신호를 보고 다음 거래일 시가에 주문 실행
- **하루에 하나의 주요 이벤트만 처리**: 이벤트 우선순위에 따라 처음 조건을 만족한 이벤트만 실행

---

## 1. 이벤트 처리 우선순위 (매일 아래 순서로 체크)

```
① -3% 이벤트          (최우선 — 항상 먼저 체크)
② MA200 터치 이벤트    (①이 발동되지 않은 날만)
③ 관망 종료 재진입      (①②가 발동되지 않은 날만, waiting == True 조건)
④ 분할매수             (①②③ 결과와 무관하게, waiting == True이면 항상 체크)
⑤ 랠리 익절            (①②가 발동되지 않은 날만, tqqq_shares > 0 조건)
```

④ 분할매수는 이벤트 플래그와 무관하게 항상 실행되지만,
① 매도가 발동된 날에는 split_buy_base가 0(1~3차) 또는 당일 현재가(4차+)로 설정되므로
당일 분할매수 조건(`q <= split_buy_base * (1 - drop_pct)`)은 절대 충족되지 않는다.
→ **전량매도와 분할매수가 같은 날 동시에 실행되는 일은 없다.**

---

## 2. 상태 변수 (State)

| 변수 | 타입 | 설명 |
|------|------|------|
| `cash` | float | 현금 |
| `tqqq_shares` | float | 보유 TQQQ 주수 |
| `gld_shares` | float | 보유 GLD 주수 (헷지) |
| `tlt_shares` | float | 보유 TLT 주수 (헷지) |
| `ath_qqq` | float | 랠리 익절 계산용 동결 전고점. 사용자의 자산 총액이 가장 높았던 시기의 QQQ 가격 |
| `running_max_qqq` | float | 현재 보유 사이클 중 QQQ 최고값 (다음 ATH 후보) |
| `rally_level` | int | 0=없음, 1~3=해당 레벨까지 익절 완료 |
| `sell_off_count` | int | 누적 -3% 이벤트 횟수 (실제 매도 + 무시 + 관망 중 재발 모두 포함) |
| `initial_pos` | bool | True=첫 -3% 이전 최초 포지션 상태 |
| `waiting` | bool | True=관망 중 (방어 모드) |
| `trigger_type` | str | ""=정상보유, "selloff"=-3% 전량매도 관망 중, "ma200"=MA200 전량매도 관망 중 |
| `split_buy_base` | float | 분할매수 기준가 (QQQ). 0이면 분할매수 비활성 |
| `split_bought_a` | list | 이미 체결된 A구간 레벨 목록 (중복 방지) |
| `split_bought_b` | list | 이미 체결된 B구간 레벨 목록 (중복 방지) |
| `wait_end_idx` | int | 21일 관망 종료 인덱스 (-1=비활성) |
| `timer_42_start_idx` | int | 42일 타이머 시작 인덱스 (-1=비활성) |
| `last_selloff_idx` | int | 마지막 매도 이벤트 인덱스 |
| `ma5_above_ma200_streak` | int | MA5 > MA200 연속 일수 (MA200 재진입 조건용) |


---

## 3. ATH(전고점) 추적 규칙

### 변수 역할
- `ath_qqq`: 사용자의 자산 총액이 가장 높았던 시기의 QQQ 가격. 랠리 익절 계산 기준.
  -3% 또는 MA200 이벤트 발생 시점에 확정·동결.
- `running_max_qqq`: 현재 보유 사이클 중 QQQ 최고값. 다음 이벤트 때 ATH 후보로 사용.

### 규칙 (상황별)

**① 최초 포지션 기간 (initial_pos == True, 첫 -3% 발생 전)**
- 매일: `running_max_qqq = max(running_max_qqq, qqq_today)`
- 매일: `ath_qqq = running_max_qqq` (같이 올라감)

**② -3% 이벤트 또는 MA200 이벤트 발생 시 (triggered 또는 MA200 조건 충족)**
- `ath_qqq = max(ath_qqq, running_max_qqq)` → 높은 값으로 확정. ATH는 절대 내려가지 않음.
- 이후 `ath_qqq`는 다음 이벤트 전까지 절대 변경 안 함.

**③ 재진입 후 보유 기간 (trigger_type == "")**
- 매일: `running_max_qqq = max(running_max_qqq, qqq_today)` 갱신
- `ath_qqq`: 변경 없음 (이전 이벤트 때 확정된 값 유지. QQQ가 ATH를 초과해도 갱신 안 함)

**④ 다음 -3% 또는 MA200 이벤트 발생 시**
- `ath_qqq = max(ath_qqq, running_max_qqq)` → 더 높은 값이면 업데이트 (절대 낮아지지 않음)

**⑤ 재진입 시**
- `running_max_qqq = qqq_재진입일` (리셋)
- `ath_qqq`: 변경 없음

### 검증 예시
```
사이클 1:
  2010-02-11 진입, QQQ 오르며 running_max 매일 갱신
  2010-04-23: QQQ=50.52 → running_max=50.52
  2010-05-04: -3% 발동 → ath_qqq = max(이전, 50.52) = 50.52 확정·동결

사이클 2 (재진입 후):
  ath_qqq = 50.52 고정 (QQQ가 51, 52, 55 올라도 불변)
  running_max는 재진입 당일 QQQ부터 다시 갱신
  QQQ = 55.07 (= 50.52 × 1.09) → 랠리 1단계 발동 ✓
  2011-08-04 직전 최고 = 59.63 → 이 날 -3% 발동 시 ath_qqq = 59.63 교체

사이클 3 (재진입 후):
  ath_qqq = 59.63 고정
  QQQ = 65.0 (= 59.63 × 1.09) → 랠리 1단계 발동 ✓
```

---

## 4. 이벤트 ① — -3% 이벤트 (하락 대응)

### 발동 조건
`qqq_daily_ret <= selloff_thresh (기본값 -0.03)`

### sell_off_count 카운팅 규칙
**모든 -3% 이벤트를 카운트한다** — 실제 매도가 발생했는지, 무시됐는지 여부와 무관하게,
당일 -3% 조건을 충족하면 항상 sell_off_count를 1 증가시킨다.
(실제 매도 시에도, "랠리 이전 1차 무시" 시에도, 관망 중 재발로 카운트만 할 때도 동일하게 증가)

### 케이스별 처리 (우선순위 순)

#### 케이스 A: initial_pos == True (최초 포지션, 아직 한 번도 매도 없음)
```
동작:
  ath_qqq = max(ath_qqq, running_max_qqq)  ← ATH 확정
  SELL_ALL (전량매도)
  sell_off_count += 1  → 1
  initial_pos = False
  trigger_type = "selloff"
  waiting = True
  → 1차 매도이므로 sell_off_count(1) < heavy_sell_count(4): 21일 관망
  wait_end_idx = today + wait_short(21)
  split_buy_base = 0.0  ← 1~3차는 분할매수 없음
  split_bought_a/b = []
  rally_level = 0
  헷지 매수 (use_hedge=True이면)
```

#### 케이스 B: tqqq_shares > 0, trigger_type == "" (정상 보유 중, 재진입 후 상태)

이 케이스는 rally_level과 sell_off_count에 따라 **3가지 하위 케이스**로 분기한다.

##### 케이스 B-1: rally_level >= 1 (랠리 익절이 1단계 이상 발동된 상태)
```
동작: 전량매도 (케이스 A와 동일한 매도 + 관망 시작)
  ath_qqq = max(ath_qqq, running_max_qqq)
  SELL_ALL
  sell_off_count += 1
  → sell_off_count에 따라 21일 또는 42일 관망
```

##### 케이스 B-2: rally_level == 0, sell_off_count >= 1 (랠리 이전, 2차 이상 -3%)
```
동작: 전량매도 (케이스 A와 동일한 매도 + 관망 시작)
  ath_qqq = max(ath_qqq, running_max_qqq)
  SELL_ALL
  sell_off_count += 1
  → sell_off_count에 따라 21일 또는 42일 관망
```

##### 케이스 B-3: rally_level == 0, sell_off_count == 0 (랠리 이전 1차 -3%)
```
먼저 "조건부 무시 필터"를 체크한다 (use_ignore_filter == True일 때만):
  qqq_ret_10d = (qqq_today - qqq[today - ignore_filter_lookback]) / qqq[today - ignore_filter_lookback]

  if use_ignore_filter AND qqq_ret_10d <= ignore_filter_thresh:
      # 10일 누적 하락이 임계값(-5%) 이하 → 하락 추세 진행 중이므로 전량매도
      동작: 전량매도 (케이스 B-2와 동일하게 처리)
        ath_qqq = max(ath_qqq, running_max_qqq)
        SELL_ALL
        sell_off_count += 1
        → sell_off_count에 따라 21일 또는 42일 관망
        로그: SELLOFF_CONDITIONAL_SELL (10d누적 {qqq_ret_10d*100:.1f}%)
        event_handled = True

  else:
      # 필터 비활성 또는 10일 누적이 임계값 초과 → 기존대로 무시
      동작: 무시. 전량매도하지 않는다.
        sell_off_count += 1  → 1 (카운트는 증가)
        last_selloff_idx = today
        로그: SELLOFF_IGNORED
        event_handled = True

      이후 하락감시 기간 (wait_short 거래일, 기본 24일):
        - TQQQ 보유 유지, 매도하지 않음
        - 24일 이내 -3% 재발 → 케이스 B-2 (2차 전량매도) 발동
        - 24일 경과 무사 → sell_off_count = 0 리셋, 정상 "보유"로 복귀

파라미터 (Params):
  use_ignore_filter: bool = False     # 기본 OFF (토글 스위치)
  ignore_filter_lookback: int = 10    # 누적수익률 계산 기간 (거래일)
  ignore_filter_thresh: float = -0.05 # 이 값 이하이면 전량매도 (-5%)

설계 의도:
  재진입 직후 첫 -3%를 무조건 무시하면, 이미 하락 추세 진행 중인 경우
  추가 손실을 입고 결국 매도하게 되는 비율이 높다 (백테스트 기준 62%).
  10일 누적 수익률 < -5%이면 하락 추세를 판별하여 즉시 전량매도한다.
  필터 OFF 시 기존 동작(무조건 무시)과 100% 동일하게 작동한다.
```

#### 케이스 C: tqqq_shares > 0, trigger_type == "ma200" (MA200 관망 중 분할매수 포지션 보유)
```
상황: MA200 터치로 전량매도 → 관망 중 분할매수 체결 → tqqq_shares > 0
      이 상태에서 -3% 발동

동작:
  ath_qqq = max(ath_qqq, running_max_qqq)  ← ATH 확정
  SELL_ALL (분할매수 포지션 포함 전량 청산)
  sell_off_count += 1
  trigger_type = "selloff"  ← MA200 관망에서 -3% 전량매도 관망으로 전환
  waiting = True
  → sell_off_count에 따라 21일 또는 42일 관망 적용
  split_buy_base = 0.0 (1~3차) 또는 q (4차+)
  split_bought_a/b = []

이유: -3%는 최우선 이벤트. trigger_type과 무관하게 항상 전량매도 후 -3% 전량매도 관망 시작.
```

#### 케이스 D: tqqq_shares > 0, trigger_type == "selloff" (-3% 전량매도 관망 중 분할매수 포지션 보유)
```
상황: -3% 전량매도 → 관망 중 분할매수 체결 → tqqq_shares > 0
      이 상태에서 -3% 재발동

동작: SELL_ALL 하지 않음. 분할매수 포지션 유지.
  sell_off_count += 1
  last_selloff_idx = today
  if sell_off_count < heavy_sell_count:        ← 1~3차
      wait_end_idx = today + wait_short  ← 21일 타이머 리셋
  elif sell_off_count == heavy_sell_count:      ← 4차 정확히
      wait_end_idx = -1
      timer_42_start_idx = today  ← 42일 타이머 시작
      (base=0 유지 → 5차 -3% 대기)
  else:                                         ← 5차+
      timer_42_start_idx = today  ← 타이머 리셋
      if split_buy_base == 0.0:   ← 최초 1회만 (5차)
          split_buy_base = q      ← 기준가 고정 (이후 갱신 없음)
          split_bought_a = []
          split_bought_b = []
      (6차+: base 갱신 없음, split_bought 초기화 없음)
  로그: "SELLOFF_WAIT"
  event_handled = True

이유: 이미 -3% 전량매도 관망 중이므로 분할매수 포지션은 보유 유지.
     -3%는 카운트/타이머 업데이트만 하고 SELL_ALL은 없음.
     기준가는 5차에서 1회 설정 후 고정 — A1 즉시 실행, A2~A5는 기준가 대비 추가 하락 시.
```

#### 케이스 E: tqqq_shares == 0, trigger_type == "selloff" (-3% 전량매도 관망 중 포지션 없음)
```
동작: 케이스 D와 동일한 3분기 로직 (< heavy_sell_count / == / >)
      단, 이미 tqqq=0이므로 분할매수 유지 관련 처리 없음
```

#### 케이스 F: tqqq_shares == 0, trigger_type == "ma200" (MA200 관망 중 포지션 없음)
```
동작:
  timer_42_start_idx = today  (타이머 갱신만)
  sell_off_count 증가 없음
  split_buy_base/split_bought 변경 없음
```

### 매도 후 공통 처리 (케이스 A/B-1/B-2/C에서 SELL_ALL 발동 시)
```
매도 후 sell_off_count에 따라 관망 모드 결정:

if sell_off_count < heavy_sell_count (1~3차):
    waiting = True
    wait_end_idx = today + wait_short  (기본 21일)
    timer_42_start_idx = -1
    split_buy_base = 0.0      ← 1~3차는 분할매수 없음
    split_bought_a = []
    split_bought_b = []

else (4차 이상, sell_off_count >= heavy_sell_count):
    waiting = True
    wait_end_idx = -1
    timer_42_start_idx = today   ← 42일 타이머 시작
    split_buy_base = 0.0         ← 기준가 없음, 다음 -3%(5차) 대기
    split_bought_a = []
    split_bought_b = []

공통:
    rally_level = 0
    trigger_type = "selloff"
    last_selloff_idx = today
    ma200_touch_price = 0.0
    헷지 매수: use_hedge=True이면 cash * hedge_alloc_pct를 GLD/TLT로 매수
```

---

## 5. 이벤트 ② — MA200 터치 이벤트

### 발동 조건
```
not event_handled  (①이 발동되지 않은 날)
AND use_ma200_sell == True
AND rally_level > 0  (랠리 익절이 1단계 이상 발동된 상태 — 랠리 중에만 작동)
AND tqqq_shares > 0
AND qqq <= ma200 * ma200_mult (기본 1.01)
```

### 동작
```
ath_qqq = max(ath_qqq, running_max_qqq)  ← ATH 확정
SELL_ALL
sell_off_count += 1
trigger_type = "ma200"
waiting = True
wait_end_idx = -1
timer_42_start_idx = -1
split_buy_base = q       ← MA200 관망은 항상 분할매수 기준가 설정
split_bought_a = []
split_bought_b = []
ma5_above_ma200_streak = 0  ← 재진입 조건 카운트 새로 시작
rally_level = 0
last_selloff_idx = today
헷지 매수: use_hedge=True이면 cash * hedge_alloc_pct를 GLD/TLT로 매수
```

---

## 6. 이벤트 ③ — 관망 종료 재진입

### 발동 조건
```
not event_handled
AND waiting == True
(분할매수로 tqqq_shares > 0이어도 재진입 가능 — 기존 포지션 유지 + 남은 현금으로 추가 매수)
```

### 재진입 조건 (trigger_type별)

#### trigger_type == "selloff" (-3% 전량매도 관망)
```
if sell_off_count < heavy_sell_count:  (1~3차)
    if today >= wait_end_idx:
        reenter = True  ← 재진입

else:  (4차 이상)
    if timer_42_start_idx >= 0 AND (today - timer_42_start_idx) >= wait_long(24):
        reenter = True  ← 재진입
```

#### trigger_type == "ma200" (MA200 전량매도 관망)
```
if ma200_reentry_streak == 0:
    reenter = True  ← 즉시 재진입
elif ma5_above_ma200_streak >= ma200_reentry_streak (기본 3일):
    reenter = True  ← MA5 > MA200 5일 연속 충족 시 재진입
```

### 추세 필터 (use_trend_filter == True인 경우 추가 조건)
```
재진입 조건이 충족됐을 때 추가로 확인:
  above_ma50 = (qqq > ma50) if trend_require_above_ma50 else True
  ma200_rise = (ma200_today > ma200[today - ma200_slope_lookback]) if trend_require_ma200_rising else True

  if trend_filter_mode == "AND": trend_ok = above_ma50 AND ma200_rise
  if trend_filter_mode == "OR":  trend_ok = above_ma50 OR ma200_rise

  trend_ok == False이면 재진입 보류
```

### 재진입 실행
```
헷지 전량 매도 (use_hedge=True이면) → 현금화
BUY_ALL (남은 현금 전액으로 TQQQ 매수) → 공격 모드 전환
  (분할매수 포지션이 있으면 그대로 유지하고, 남은 현금으로 추가 매수)
running_max_qqq = qqq_today  ← 리셋 (ath_qqq는 변경 없음)
waiting = False
trigger_type = ""
split_buy_base = 0.0
split_bought_a = []
split_bought_b = []
timer_42_start_idx = -1
sell_off_count = 0   ← 항상 0으로 리셋. 다음 -3%는 1차부터 시작.
last_selloff_idx = -1
```

**중요:** `sell_off_count`는 재진입 시 항상 0으로 리셋된다.
재진입 후 첫 -3%는 케이스 B-3(조건부 무시 필터 ON이면 10일 누적 체크, OFF면 무시)로 처리된다.
과거 매도 이력(몇 차였는지)은 재진입 시 초기화되며 누적되지 않는다.

---

## 7. 이벤트 ④ — 분할매수

### 발동 조건
```
waiting == True  (관망 중이면 항상 체크 — event_handled 플래그와 무관)
AND split_buy_base > 0  (기준가 설정된 경우만)
```

### 분할매수 레벨 계산

#### trigger_type == "selloff" (-3% 전량매도 관망) 일 때
```
A구간 (기준가 대비 하락률 = so_a_step * (레벨번호-1)):
  A1: split_buy_base (즉시매수)     → 기준가 설정과 동시에 실행 (0% 하락)
  A2: split_buy_base × (1 - 0.03)  → 기준가 대비 -3%
  A3: split_buy_base × (1 - 0.06)  → 기준가 대비 -6%
  A4: split_buy_base × (1 - 0.09)  → 기준가 대비 -9%
  A5: split_buy_base × (1 - 0.12)  → 기준가 대비 -12%

B구간 (so_b_enabled == True일 때만 활성, 기본 OFF):
  B1: split_buy_base × (1 - 0.20)  → 기준가 대비 -20%
  B2: split_buy_base × (1 - 0.25)  → 기준가 대비 -25%
  B3: split_buy_base × (1 - 0.30)  → 기준가 대비 -30%
  B4: split_buy_base × (1 - 0.35)  → 기준가 대비 -35%
  B5: split_buy_base × (1 - 0.40)  → 기준가 대비 -40%
```

#### trigger_type == "ma200" (MA200 전량매도 관망) 일 때
```
A구간:
  A1: split_buy_base × (1 - 0.03)  → 기준가 대비 -3%
  A2: split_buy_base × (1 - 0.05)  → 기준가 대비 -5%
  A3: split_buy_base × (1 - 0.07)  → 기준가 대비 -7%
  A4: split_buy_base × (1 - 0.09)  → 기준가 대비 -9%
  A5: split_buy_base × (1 - 0.11)  → 기준가 대비 -11%

B구간 (ma_b_enabled == True일 때만 활성, 기본 OFF):
  B1: split_buy_base × (1 - 0.20)  → 기준가 대비 -20%
  B2: split_buy_base × (1 - 0.23)  → 기준가 대비 -23%
  B3: split_buy_base × (1 - 0.26)  → 기준가 대비 -26%
  B4: split_buy_base × (1 - 0.29)  → 기준가 대비 -29%
  B5: split_buy_base × (1 - 0.32)  → 기준가 대비 -32%
```

### 분할매수 금액 계산 (핵심)
```
각 레벨 발동 시, 그 시점의 "남은 헷지자산(현금 + GLD가치 + TLT가치)"을 재계산한다.
매수 금액 = 남은 헷지자산 × buy_pct (기본 10%)

예시 (헷지자산 총 1000만원, 분할매수 10%):
  A1 발동: 1000만 × 10% = 100만원 매수 → 남은 헷지자산 900만
  A2 발동: 900만 × 10% = 90만원 매수 → 남은 헷지자산 810만
  A3 발동: 810만 × 10% = 81만원 매수 → 남은 헷지자산 729만
  ... (복리식으로 점점 줄어듦)
```

**주의:** `total_liquid`는 루프 밖에서 한 번 계산하는 것이 아니라,
**매 레벨 발동 직전에 재계산**해야 한다. 이전 레벨에서 매수한 금액이 빠져야 하기 때문이다.

### 분할매수 실행 조건
```
for 각 레벨 (zone, level_idx, drop_pct, buy_pct):
    if (zone, level_idx) in split_bought_a/b:  → skip (이미 매수)
    if split_buy_base > 0 and qqq <= split_buy_base * (1 - drop_pct):
        # ★ 매 레벨 발동 직전 잔여 헷지 자산 재계산
        hedge_val = gld_shares * gld_price + tlt_shares * tlt_price
        total_liquid = cash + hedge_val
        if total_liquid <= 0: continue
        amount = total_liquid * buy_pct
        현금 부족 시 헷지 일부 매도하여 충당
        TQQQ 매수
        split_bought_a/b에 (zone, level_idx) 추가
```

### 분할매수 최대 노출 캡 (split_buy_max_exposure)

각 분할매수 레벨 실행 직전, TQQQ 평가액이 전체 자산(NAV) 대비 설정 비율 이상이면 해당 레벨을 건너뛴다.

```
for 각 레벨:
    ... (중복·VIX 체크) ...
    # ★ 노출 캡 체크 (split_buy_max_exposure < 1.0일 때만)
    current_tqqq_value = tqqq_shares × tqqq_exec_price
    current_nav = current_tqqq_value + cash + gld_shares × gld_price + tlt_shares × tlt_price
    if current_tqqq_value ≥ current_nav × split_buy_max_exposure:
        skip (추가 매수 없음)
    ... (기존 가격 조건·매수 로직) ...
```

- **기본값: 100% (제한 없음 = 기존 동작)**
- 슬라이더 범위: 10% ~ 100%, 5% 단위
- A/B 구간에 동일 적용. 시장이 B구간까지 하락하면 3× 레버리지로 기존 TQQQ 가치가 감소하여 캡 아래로 자연 복귀 → B구간 매수 허용 (자기 보정)
- **재진입 시에는 적용되지 않음** (전량 재진입은 항상 실행)

### 분할매수 후 처리 (헷지 재투자)
```
waiting == True AND cash > 0 AND use_hedge == True이면:
    목표 현금 비중 = (1 - hedge_alloc_pct)
    초과 현금을 GLD/TLT로 이동
```

### 분할매수가 없는 구간
- **1~3차 매도 관망 중**: `split_buy_base = 0.0` → 분할매수 없음. 21일 후 전량 재진입만 함.
- **4차+ 매도 관망 중**: `split_buy_base = q` → 분할매수 활성화. 42일 후 재진입.

---

## 8. 이벤트 ⑤ — 랠리 익절

### 발동 조건
```
not event_handled
AND tqqq_shares > 0
AND trigger_type == ""  (정상 보유 상태에서만. 관망 중에는 발동 안 함)
```

### 발동 기준
```
gain = (qqq_today - ath_qqq) / ath_qqq

레벨 1: rally_level < 1 AND gain >= rally_thresh_1 (기본 +15%)
  → TQQQ의 rally_sell_pct_1 (기본 10%) 매도
  → rally_level = 1

레벨 2: rally_level < 2 AND gain >= rally_thresh_2 (기본 +21%)
  → TQQQ의 rally_sell_pct_2 (기본 20%) 매도
  → rally_level = 2

레벨 3: rally_level < 3 AND gain >= rally_thresh_3 (기본 +25%)
  → TQQQ의 rally_sell_pct_3 (기본 30%) 매도
  → rally_level = 3
```

- 한 레벨은 한 번만 발동. 이미 발동된 레벨은 다시 발동 안 됨.
- 당일 하나의 레벨만 처리 (elif 구조).
- 랠리 익절 후 MA200 이벤트 발동 가능성 생김 (`rally_level > 0` 이므로).

---

## 10. 헷지 (GLD/TLT)

### 기본 구조
```
use_hedge == True일 때만 활성화.

헷지자산 비율 (기본값):
  현금 30% (= 1 - hedge_alloc_pct = 1 - 0.70)
  GLD  35% (= hedge_alloc_pct * hedge_gld_ratio = 0.70 * 0.50)
  TLT  35% (= hedge_alloc_pct * (1 - hedge_gld_ratio) = 0.70 * 0.50)

app.py에서 사용자 입력:
  hedge_cash_pct 슬라이더 (기본 30%) → hedge_alloc_pct = 1.0 - hedge_cash_pct/100
  hedge_gld_ratio 슬라이더 (기본 50%) → GLD 비율
```

### 운용 시점
```
헷지 매수 시점:
  - 전량매도(-3% 또는 MA200) 직후 동일 시가: cash * hedge_alloc_pct를 GLD/TLT로 매수
  - 관망 중 잔여 현금이 목표 비중 초과 시 초과분을 헷지로 이동

헷지 매도 시점:
  - 재진입(BUY_ALL) 직전 동일 시가: GLD/TLT 전량 현금화

분할매수 시 현금 부족:
  - 헷지 일부 비례 매도하여 현금 확보 후 TQQQ 매수
```

---

## 11. 파라미터 목록 (Params 클래스)

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `rally_thresh_1` | 0.15 | 랠리 1단계 발동 임계값 (ATH 대비 +15%) |
| `rally_thresh_2` | 0.21 | 랠리 2단계 (+21%) |
| `rally_thresh_3` | 0.25 | 랠리 3단계 (+25%) |
| `rally_sell_pct_1` | 0.10 | 랠리 1단계 매도 비율 (보유 TQQQ의 10%) |
| `rally_sell_pct_2` | 0.20 | 랠리 2단계 매도 비율 (20%) |
| `rally_sell_pct_3` | 0.30 | 랠리 3단계 매도 비율 (30%) |
| `selloff_thresh` | -0.03 | -3% 트리거 임계값 |
| `use_ma200_sell` | True | MA200 전량매도 기능 ON/OFF |
| `ma200_mult` | 1.01 | MA200 터치 판정 배수 (QQQ <= MA200 x 1.01) |
| `wait_short` | 21 | 1~3차 매도 후 관망 거래일수 |
| `wait_long` | 24 | 4차+ 매도 후 관망 거래일수 |
| `heavy_sell_count` | 4 | 24일 관망으로 전환되는 매도 횟수 기준 |

| `so_a_step` | 0.03 | -3% 관망 A구간 간격 |
| `so_a_pct` | 0.10 | -3% 관망 A구간 매수 비율 |
| `so_a_cnt` | 5 | -3% 관망 A구간 단계 수 |
| `so_b_enabled` | False | -3% 관망 B구간 사용 여부 (기본 OFF) |
| `so_b_start` | 0.20 | -3% 관망 B구간 시작 하락률 |
| `so_b_step` | 0.05 | -3% 관망 B구간 간격 |
| `so_b_pct` | 0.10 | -3% 관망 B구간 매수 비율 |
| `so_b_cnt` | 5 | -3% 관망 B구간 단계 수 |
| `ma_a_step` | 0.02 | MA200 관망 A구간 간격 |
| `ma_a_pct` | 0.10 | MA200 관망 A구간 매수 비율 |
| `ma_a_cnt` | 5 | MA200 관망 A구간 단계 수 |
| `ma_b_enabled` | False | MA200 관망 B구간 사용 여부 (기본 OFF, MA200 매도 OFF 시 함께 비활성) |
| `ma_b_start` | 0.20 | MA200 관망 B구간 시작 하락률 (-20%) |
| `ma_b_step` | 0.03 | MA200 관망 B구간 간격 |
| `ma_b_pct` | 0.10 | MA200 관망 B구간 매수 비율 |
| `ma_b_cnt` | 5 | MA200 관망 B구간 단계 수 |
| `use_trend_filter` | False | 추세 확인 재진입 필터 사용 여부 |
| `trend_require_above_ma50` | True | 재진입 조건: QQQ > MA50 요구 |
| `trend_require_ma200_rising` | False | 재진입 조건: MA200 상승 요구 |
| `ma200_slope_lookback` | 20 | MA200 기울기 계산 기간 (거래일) |
| `trend_filter_mode` | "OR" | 필터 모드: "OR" 또는 "AND" |

| `use_hedge` | True | GLD/TLT 헷지 사용 여부 |
| `hedge_alloc_pct` | 0.70 | 현금 중 헷지로 이동할 비율 (현금 30% 유보, 70% 헷지) |
| `hedge_gld_ratio` | 0.50 | 헷지 내 GLD 비율 (나머지 = TLT) |
| `ma200_reentry_streak` | 3 | MA200 관망 재진입 조건: MA5>MA200 연속 3일 (0=즉시) |

---

## 12. 코드 파일 구조

```
backtest for TQQQ/
├── backtest.py   ← 전략 엔진 (State, Params, run_backtest)
├── data.py       ← Yahoo Finance 데이터 다운로드 + 합성
├── app.py        ← Streamlit UI
├── metrics.py    ← CAGR, MDD, Sharpe 계산
├── charts.py     ← Plotly 차트 생성
├── presets.py    ← 전략 파라미터 프리셋 관리
├── storage.py    ← 백테스트 결과 저장/불러오기
└── strategy.md   ← 이 파일 (전략 정의 — 단일 정보 소스)
```

---

## 13. 자주 실수하기 쉬운 구현 포인트

1. **분할매수와 매도 동시 발생 방지**
   - 1~3차 매도 시 `split_buy_base = 0.0` 으로 설정해야 한다.
   - 4차+ 매도 시 `split_buy_base = q (현재가)` 로 설정하면 같은 날 `q <= q*(1-drop)` 는 절대 True가 안 됨.

2. **sell_off_count 카운팅 위치**
   - 실제 SELL_ALL 실행 시 → `sell_off_count += 1` (triggered 블록 안)
   - 랠리 이전 1차 무시 시 → `sell_off_count += 1` (SELLOFF_IGNORED)
   - 관망 중 tqqq=0에서 -3% 재발 시 → `sell_off_count += 1` (else 블록 안)
   - 관망 중 tqqq>0 (분할매수), trigger_type=="selloff"에서 -3% 재발 시 → `sell_off_count += 1` (포지션 유지)
   - MA200 관망 중 tqqq=0에서 -3% 재발 시 → sell_off_count 증가 **없음** (timer_42만 갱신)

3. **재진입은 분할매수 포지션이 있어도 가능**
   - 분할매수로 tqqq_shares > 0이어도 재진입 체크 실행
   - 재진입 시 기존 분할매수 포지션은 유지하고, 남은 현금(+헷지 현금화)으로 추가 TQQQ 매수

4. **split_bought_a/b는 (zone, level_idx) 튜플 목록**
   - trigger_type이 "ma200"에서 "selloff"로 바뀌어도 레벨 인덱스 구조는 동일 (A0~A4, B0~B4).
   - 단, trigger_type 변경 시 split_bought_a/b를 초기화하므로 중복 문제 없음.

5. **랠리 익절은 trigger_type == "" 조건**
   - 관망 중(waiting=True)에는 trigger_type이 "selloff" 또는 "ma200"이므로 랠리 익절 발동 불가.

6. **랠리 이전 1차 -3% 무시는 재진입 후에만 적용**
   - initial_pos=True 상태(최초 포지션)에서는 무시 없이 즉시 전량매도 (케이스 A).
   - 재진입 후(trigger_type="", sell_off_count=0, rally_level=0) 첫 -3%는 케이스 B-3 (조건부 무시 필터 체크).

7. **분할매수 금액은 매 레벨마다 재계산**
   - `total_liquid = cash + hedge_val`을 for 루프 **안에서** 매 레벨 발동 직전에 계산해야 한다.
   - 루프 밖에서 한 번만 계산하면 이전 레벨에서 빠진 금액이 반영되지 않는다.
