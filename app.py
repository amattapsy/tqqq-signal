"""
app.py — Streamlit 메인 앱
실행: streamlit run app.py
"""

import io
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from data import load_data
from backtest import run_backtest, Params
from metrics import calc_metrics, calc_bnh_metrics
from charts import (chart_nav, chart_drawdown, chart_annual_returns,
                    chart_cash_ratio, find_bear_periods)
from storage import save_result, load_all_saves, delete_save, get_comparison_df
from presets import (get_all_presets, save_user_preset, delete_user_preset,
                     collect_raw_params, DEFAULT_PARAMS)
from signal_engine import (compute_today_signal, calc_action_recommendation,
                           load_position, save_position, default_position,
                           compute_user_current_total)

# ══════════════════════════════════════════════════════════
# 숫자 포맷 헬퍼
# ══════════════════════════════════════════════════════════
def fmt_money(v) -> str:
    """돈: 소수점 없이 1,000 단위 콤마  예) $1,234,567"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "N/A"
    return f"${v:,.0f}"

def fmt_pct(v, sign=True) -> str:
    """수익률: 소수점 1자리 + 천단위 콤마  예) +12.3%  또는  -5.4%  또는  +28,735,575.6%"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "N/A"
    if sign:
        return f"{v*100:+,.1f}%"
    return f"{v*100:,.1f}%"

def fmt_ratio(v) -> str:
    """비율(0~100): 소수점 1자리"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "N/A"
    return f"{v:.2f}"


# ══════════════════════════════════════════════════════════
# 인쇄용 HTML 생성 헬퍼
# ══════════════════════════════════════════════════════════
def _md_to_html(md: str) -> str:
    """간단한 Markdown → HTML 변환 (strategy.md 용)."""
    import re, html as _html

    def inline(t):
        t = _html.escape(t)
        t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
        t = re.sub(r'\*(.+?)\*',     r'<em>\1</em>',         t)
        t = re.sub(r'`([^`]+)`',     r'<code>\1</code>',     t)
        return t

    lines     = md.split('\n')
    out       = []
    in_code   = False
    in_ul     = False
    in_ol     = False
    in_table  = False
    table_head_done = False
    buf_p     = []

    def flush_p():
        if buf_p:
            out.append(f'<p>{"<br>".join(buf_p)}</p>')
            buf_p.clear()

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:   out.append('</ul>');   in_ul = False
        if in_ol:   out.append('</ol>');   in_ol = False

    def close_table():
        nonlocal in_table, table_head_done
        if in_table:
            out.append('</tbody></table>')
            in_table = False
            table_head_done = False

    for line in lines:
        # ── 코드 블록 ──
        if line.strip().startswith('```'):
            flush_p(); close_lists(); close_table()
            if not in_code:
                out.append('<pre>')
                in_code = True
            else:
                out.append('</pre>')
                in_code = False
            continue
        if in_code:
            out.append(_html.escape(line))
            continue

        # ── 테이블 ──
        if line.strip().startswith('|'):
            flush_p(); close_lists()
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if not in_table:
                in_table = True
                table_head_done = False
                out.append('<table><thead><tr>' +
                           ''.join(f'<th>{inline(c)}</th>' for c in cells) +
                           '</tr></thead><tbody>')
                table_head_done = True
            elif re.match(r'^[\s|:-]+$', line):
                pass  # 구분선 무시
            else:
                out.append('<tr>' +
                           ''.join(f'<td>{inline(c)}</td>' for c in cells) +
                           '</tr>')
            continue
        else:
            close_table()

        # ── 헤더 ──
        m = re.match(r'^(#{1,4})\s+(.*)', line)
        if m:
            flush_p(); close_lists()
            lvl = len(m.group(1))
            out.append(f'<h{lvl}>{inline(m.group(2))}</h{lvl}>')
            continue

        # ── 수평선 ──
        if re.match(r'^[-*]{3,}\s*$', line):
            flush_p(); close_lists(); close_table()
            out.append('<hr>')
            continue

        # ── 순서없는 목록 ──
        m = re.match(r'^(\s*)[-*]\s+(.*)', line)
        if m:
            flush_p(); close_table()
            if not in_ul:
                out.append('<ul>'); in_ul = True
            out.append(f'<li>{inline(m.group(2))}</li>')
            continue

        # ── 순서있는 목록 ──
        m = re.match(r'^(\s*)\d+\.\s+(.*)', line)
        if m:
            flush_p(); close_table()
            if not in_ol:
                out.append('<ol>'); in_ol = True
            out.append(f'<li>{inline(m.group(2))}</li>')
            continue

        # ── 빈 줄 ──
        if not line.strip():
            flush_p(); close_lists(); close_table()
            continue

        # ── 일반 텍스트 ──
        close_lists(); close_table()
        buf_p.append(inline(line))

    flush_p(); close_lists(); close_table()
    if in_code: out.append('</pre>')
    return '\n'.join(out)


def _build_print_html(p: "Params", initial_capital: int,
                      start_year: int, start_month: int,
                      end_year: int, end_month: int,
                      strategy_md_text: str) -> str:
    """현재 파라미터 + 전략 설명을 담은 인쇄용 HTML 생성."""
    today_str  = date.today().strftime("%Y년 %m월 %d일")
    period_str = f"{start_year}.{start_month:02d} ~ {end_year}.{end_month:02d}"
    hedge_cash = (1 - p.hedge_alloc_pct) * 100
    hedge_gld  = p.hedge_gld_ratio * p.hedge_alloc_pct * 100
    hedge_tlt  = (1 - p.hedge_gld_ratio) * p.hedge_alloc_pct * 100

    def row(name, value):
        return f'<tr><td>{name}</td><td><strong>{value}</strong></td></tr>'

    def section(title, rows_html):
        return f"""
<div class="psec">
  <div class="psec-title">{title}</div>
  <table><tbody>{rows_html}</tbody></table>
</div>"""

    params_html = section("🟢 상승 익절 (랠리)", "".join([
        row("1단계 임계값 → 매도 비율", f"+{p.rally_thresh_1*100:.0f}% → TQQQ {p.rally_sell_pct_1*100:.0f}% 매도"),
        row("2단계 임계값 → 매도 비율", f"+{p.rally_thresh_2*100:.0f}% → TQQQ {p.rally_sell_pct_2*100:.0f}% 매도"),
        row("3단계 임계값 → 매도 비율", f"+{p.rally_thresh_3*100:.0f}% → TQQQ {p.rally_sell_pct_3*100:.0f}% 매도"),
        row("익절 대금 즉시 헷지매수", "사용" if p.rally_sell_to_hedge else "미사용"),
    ])) + section("🔴 하락 대응", "".join([
        row("-3% 트리거 임계값", f"{p.selloff_thresh*100:.0f}%"),
        row("MA200 전량매도", "사용" if p.use_ma200_sell else "미사용"),
        row("MA200 터치 배수", f"{p.ma200_mult:.3f}"),
    ])) + section("⏳ 재진입 대기", "".join([
        row("단기 관망 기간",        f"{p.wait_short}거래일"),
        row("장기 관망 기간",           f"{p.wait_long}거래일"),
        row("장기 모드 전환 기준",   f"{p.heavy_sell_count}차 매도 이상"),
        row("MA200 관망 기간",       f"{p.ma200_wait_days}거래일"),
    ])) + section("📊 분할매수 — -3% 트리거", "".join(
        [
            row("A구간 하락 간격",  f"-{p.so_a_step*100:.0f}% 간격, {p.so_a_cnt}단계"),
            row("A구간 매수 비율", f"잔여자산의 {p.so_a_pct*100:.0f}%"),
            row("B구간 사용", "사용" if p.so_b_enabled else "미사용"),
        ] + ([
            row("B구간 시작 낙폭", f"-{p.so_b_start*100:.0f}%"),
            row("B구간 하락 간격", f"{p.so_b_step*100:.0f}% 간격, {p.so_b_cnt}단계"),
            row("B구간 매수 비율", f"잔여자산의 {p.so_b_pct*100:.0f}%"),
        ] if p.so_b_enabled else [])
    )) + section("📊 분할매수 — MA200 트리거", "".join(
        [
            row("A구간 하락 간격",  f"-{p.ma_a_step*100:.0f}% 간격, {p.ma_a_cnt}단계"),
            row("A구간 매수 비율", f"잔여자산의 {p.ma_a_pct*100:.0f}%"),
            row("B구간 사용", "사용" if p.ma_b_enabled else "미사용"),
        ] + ([
            row("B구간 시작 낙폭", f"-{p.ma_b_start*100:.0f}%"),
            row("B구간 하락 간격", f"{p.ma_b_step*100:.0f}% 간격, {p.ma_b_cnt}단계"),
            row("B구간 매수 비율", f"잔여자산의 {p.ma_b_pct*100:.0f}%"),
        ] if p.ma_b_enabled else [])
    )) + section("📈 추세 확인 재진입", "".join([
        row("MA5>MA200 연속",    f"{p.ma200_reentry_streak}일" if p.trend_require_ma5_above_ma200 else "미사용"),
        row("QQQ > MA50 요구",   "예" if p.trend_require_above_ma50   else "아니오"),
        row("MA200 상승 추세 확인",   "예" if p.trend_require_ma200_rising  else "아니오"),
        row("MA200 기울기 기간", f"{p.ma200_slope_lookback}거래일"),
        row("필터 모드",         p.trend_filter_mode),

    ])) + section("🛡️ 관망 구간 헷지", "".join([
        row("헷지 활성화",  "사용" if p.use_hedge else "미사용"),
        row("현금 보유 비율", f"{hedge_cash:.0f}%"),
        row("GLD 비율",      f"{hedge_gld:.0f}%"),
        row("SHY 비율",      f"{hedge_tlt:.0f}%"),
    ])) + section("🔍 조건부 무시 필터", "".join([
        row("활성화",     "사용" if p.use_ignore_filter else "비활성"),
        row("필터 기간",  f"{p.ignore_filter_lookback}거래일"),
        row("필터 임계값", f"{p.ignore_filter_thresh*100:.0f}%"),
    ]))

    strategy_html = _md_to_html(strategy_md_text)

    css = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif;
  max-width: 960px; margin: 0 auto; padding: 32px 44px;
  font-size: 10.5pt; line-height: 1.65; color: #1a1a1a;
}
.cover { text-align: center; padding: 48px 0 36px; border-bottom: 3px solid #1565c0; margin-bottom: 28px; }
.cover h1 { font-size: 22pt; color: #1565c0; }
.cover .meta { color: #555; margin-top: 10px; font-size: 10pt; }
h1 { font-size: 17pt; color: #1565c0; margin: 28px 0 10px; border-left: 5px solid #1565c0; padding-left: 10px; }
h2 { font-size: 13pt; color: #1a237e; margin: 22px 0 8px; }
h3 { font-size: 11pt; color: #333; margin: 16px 0 6px; }
h4 { font-size: 10.5pt; color: #555; margin: 12px 0 4px; }
p { margin: 8px 0; }
hr { border: none; border-top: 1px solid #ddd; margin: 20px 0; }
ul, ol { padding-left: 22px; margin: 6px 0; }
li { margin: 3px 0; }
code { background: #f0f4ff; padding: 1px 5px; border-radius: 3px; font-size: 9pt;
       font-family: 'Consolas', 'D2Coding', monospace; }
pre { background: #f5f5f5; padding: 10px 14px; border-radius: 4px; margin: 10px 0;
      font-size: 8pt; font-family: 'Consolas', 'D2Coding', monospace;
      white-space: pre-wrap; word-break: break-all; border: 1px solid #ddd; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 9.5pt; }
th { background: #1565c0; color: #fff; padding: 6px 10px; text-align: left; font-weight: bold; }
td { padding: 5px 10px; border: 1px solid #ddd; }
tr:nth-child(even) td { background: #f3f6ff; }
.params-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
.psec { margin-bottom: 0; }
.psec-title { font-weight: bold; color: #1565c0; font-size: 10pt; padding: 4px 0 4px 6px;
              border-left: 3px solid #1565c0; margin-bottom: 6px; }
.psec table { font-size: 9pt; }
.print-btn { text-align: center; margin: 16px 0 24px; }
.print-btn button {
  background: #1565c0; color: white; padding: 10px 36px; border: none;
  border-radius: 5px; font-size: 12pt; cursor: pointer; font-family: inherit;
}
.print-btn button:hover { background: #0d47a1; }
@media print {
  body { font-size: 9.5pt; padding: 8px 16px; }
  .cover { padding: 24px 0 18px; }
  .print-btn { display: none; }
  .params-grid { grid-template-columns: 1fr 1fr; }
  pre { font-size: 7pt; }
  h1, h2 { page-break-after: avoid; }
}
"""
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>TQQQ 매매 전략 매뉴얼</title>
  <script>
    window.onload = function() {{
      // 자동 인쇄 (브라우저 팝업 차단 시 버튼으로 수동 인쇄 가능)
      try {{ window.print(); }} catch(e) {{}}
    }};
  </script>
  <style>{css}</style>
</head>
<body>
  <div class="cover">
    <h1>📈 TQQQ 매매 전략 매뉴얼</h1>
    <div class="meta">
      출력일: {today_str} &nbsp;|&nbsp; 백테스트 기간: {period_str} &nbsp;|&nbsp;
      초기자본: ${initial_capital:,}
    </div>
  </div>

  <div class="print-btn">
    <button onclick="window.print()">🖨️ 인쇄하기</button>
  </div>

  <h1>⚙️ 현재 전략 파라미터 설정값</h1>
  <div class="params-grid">
    {params_html}
  </div>

  <hr>
  <h1>📖 전략 설명</h1>
  {strategy_html}
</body>
</html>"""


# ── 페이지 설정 ──────────────────────────────────────────────
st.set_page_config(page_title="TQQQ 투자 시그널", page_icon="📱", layout="wide")
st.title("📱 TQQQ 투자 시그널")
st.caption("QQQ 신호 기반 TQQQ 매매 전략 | 데이터: Yahoo Finance")

# ══════════════════════════════════════════════════════════
# 사이드바 — 내 포지션 입력 (투자 시그널용)
# ══════════════════════════════════════════════════════════
with st.sidebar:
    st.header("👤 내 포지션")
    st.caption("오늘의 투자 시그널 탭에서 사용됩니다.")

    # 포지션 불러오기 (처음 접속 시)
    if "my_position" not in st.session_state:
        _loaded = load_position()
        st.session_state["my_position"] = _loaded if _loaded else default_position()
    _pos_cur = st.session_state["my_position"]

    _default_start = _pos_cur.get("trade_start_date", "") or "2024-01-02"
    try:
        _d_init = pd.Timestamp(_default_start).date()
    except Exception:
        _d_init = date(2024, 1, 2)

    _sb_trade_start = st.date_input(
        "거래 시작일",
        value=_d_init,
        min_value=date(2010, 2, 11),
        max_value=date.today(),
        key="sb_trade_start",
    )
    _sb_tqqq_shares = st.number_input(
        "TQQQ 보유 주수", min_value=0.0, step=1.0, format="%.4f",
        value=float(_pos_cur.get("tqqq_shares", 0.0)), key="sb_tqqq_shares",
    )
    _sb_tqqq_avg_cost = st.number_input(
        "TQQQ 평균 매수단가 (USD)", min_value=0.0, step=0.01, format="%.2f",
        value=float(_pos_cur.get("tqqq_avg_cost", 0.0)), key="sb_tqqq_avg_cost",
    )
    _sb_cash_usd = st.number_input(
        "현재 현금 잔액 (USD)", min_value=0.0, step=100.0,
        value=float(_pos_cur.get("cash_usd", 0.0)), key="sb_cash_usd",
    )
    _sb_gld_shares = st.number_input(
        "GLD 보유 주수", min_value=0.0, step=1.0, format="%.4f",
        value=float(_pos_cur.get("gld_shares", 0.0)), key="sb_gld_shares",
    )
    _sb_tlt_shares = st.number_input(
        "SHY 보유 주수", min_value=0.0, step=1.0, format="%.4f",
        value=float(_pos_cur.get("tlt_shares", 0.0)), key="sb_tlt_shares",
    )
    _sb_initial_inv = st.number_input(
        "거래 시작일 총 투자금 (USD, 선택)",
        min_value=0.0, step=100.0,
        value=float(_pos_cur.get("initial_investment", 0.0)),
        help="모르면 0으로 두세요. 자동 추정됩니다. (오늘의 신호 판정에는 영향 없음)",
        key="sb_initial_inv",
    )

    _sb_c1, _sb_c2 = st.columns(2)
    with _sb_c1:
        if st.button("💾 저장", type="primary", use_container_width=True, key="sb_save_pos"):
            _new_pos = {
                "trade_start_date":   _sb_trade_start.strftime("%Y-%m-%d"),
                "initial_investment": float(_sb_initial_inv),
                "tqqq_shares":        float(_sb_tqqq_shares),
                "tqqq_avg_cost":      float(_sb_tqqq_avg_cost),
                "cash_usd":           float(_sb_cash_usd),
                "gld_shares":         float(_sb_gld_shares),
                "tlt_shares":         float(_sb_tlt_shares),
            }
            save_position(_new_pos)
            st.session_state["my_position"] = _new_pos
            st.success("저장됨")
            st.rerun()
    with _sb_c2:
        if st.button("🔄 초기화", use_container_width=True, key="sb_reset_pos"):
            st.session_state["my_position"] = default_position()
            save_position(default_position())
            st.rerun()

    with st.expander("🔐 JSON 백업 / 복원"):
        import json as _json_sb
        st.caption("Streamlit Cloud 재시작 대비 백업")
        st.code(_json_sb.dumps(st.session_state["my_position"], ensure_ascii=False, indent=2),
                language="json")
        _imp_sb = st.text_area("복원용 JSON", height=100, key="sb_import_json")
        if st.button("복원", key="sb_import_pos_btn"):
            try:
                _new = _json_sb.loads(_imp_sb)
                st.session_state["my_position"] = _new
                save_position(_new)
                st.success("복원 완료")
                st.rerun()
            except Exception as _e:
                st.error(f"JSON 오류: {_e}")

    st.divider()

# ══════════════════════════════════════════════════════════
# 사이드바 — 백테스트 전용 파라미터 (결과/하락장/일별 탭 전용)
# ══════════════════════════════════════════════════════════
with st.sidebar.expander("🔬 백테스트 전용 설정 (펼쳐서 조정)", expanded=False):
    st.caption("아래 설정은 '📊 결과 · 🔴 하락장 분석 · 📋 일별 시그널' 탭에서만 사용됩니다. "
               "오늘의 투자 시그널에는 전략 프리셋의 기본 파라미터가 적용됩니다.")
    st.header("⚙️ 전략 파라미터")

    # ── 프리셋 ────────────────────────────────────────────
    st.subheader("📁 전략 프리셋")
    _all_presets = get_all_presets()
    _preset_names = [p["name"] for p in _all_presets]

    _col_sel, _col_load = st.columns([3, 1])
    with _col_sel:
        _selected_name = st.selectbox(
            "프리셋 선택",
            options=_preset_names,
            label_visibility="collapsed",
            key="preset_selector",
        )
    with _col_load:
        _load_clicked = st.button("불러오기", use_container_width=True)

    if _load_clicked:
        _preset = next(p for p in _all_presets if p["name"] == _selected_name)
        _merged = {**DEFAULT_PARAMS, **_preset["params"]}
        for _k, _v in _merged.items():
            st.session_state[_k] = _v
        st.rerun()

    with st.expander("현재 설정 저장 / 삭제"):
        _save_name = st.text_input(
            "프리셋 이름", placeholder="예: MA200+VIX_v2",
            key="preset_save_name_input",
        )
        if st.button("💾 프리셋 저장", use_container_width=True):
            if not _save_name.strip():
                st.warning("이름을 입력해 주세요.")
            else:
                save_user_preset(_save_name.strip(), collect_raw_params())
                st.success(f"저장 완료: {_save_name.strip()}")
                st.rerun()

        _user_presets = [p for p in _all_presets if p["_source"] == "user"]
        if _user_presets:
            st.markdown("---")
            _del_name = st.selectbox(
                "삭제할 프리셋",
                [p["name"] for p in _user_presets],
                key="preset_delete_selector",
            )
            if st.button("🗑️ 삭제", type="secondary", use_container_width=True):
                _target = next(p for p in _user_presets if p["name"] == _del_name)
                delete_user_preset(_target["_filename"])
                st.rerun()

    # 선택된 프리셋 설명 표시
    _sel_preset = next((p for p in _all_presets if p["name"] == _selected_name), None)
    if _sel_preset and _sel_preset.get("description"):
        st.caption(_sel_preset["description"])

    st.divider()

    # ── 백테스트 기간 ─────────────────────────────────────
    st.subheader("📅 백테스트 기간")
    today = date.today()

    col_sy, col_sm = st.columns(2)
    with col_sy:
        start_year  = st.selectbox("시작 연도", list(range(1985, today.year + 1)), index=0)
    with col_sm:
        start_month = st.selectbox("시작 월", list(range(1, 13)), index=0,
                                   format_func=lambda x: f"{x:02d}월")

    col_ey, col_em = st.columns(2)
    with col_ey:
        end_year  = st.selectbox("종료 연도", list(range(1985, today.year + 2)),
                                 index=list(range(1985, today.year + 2)).index(today.year))
    with col_em:
        end_month = st.selectbox("종료 월", list(range(1, 13)), index=today.month - 1,
                                 format_func=lambda x: f"{x:02d}월")

    initial_capital = st.number_input("초기 자본 ($)", value=100_000, step=10_000, min_value=1_000,
                                      help="백테스트 시작 시 투자 원금")
    st.divider()

    # ── 랠리 익절 ─────────────────────────────────────────
    st.subheader("🟢 상승 익절 (랠리)")
    col1, col2 = st.columns(2)
    with col1:
        rally_thresh_1 = st.slider("1단계 임계값 (%)", 3, 20, 15, key="rally_thresh_1",
                                   help="QQQ가 ATH(전고점) 대비 이 % 이상 상승하면 1단계 익절 발동") / 100
        rally_thresh_2 = st.slider("2단계 임계값 (%)", 5, 40, 21, key="rally_thresh_2",
                                   help="1단계 이후, ATH 대비 이 % 이상 상승 시 2단계 익절") / 100
        rally_thresh_3 = st.slider("3단계 임계값 (%)", 10, 60, 25, key="rally_thresh_3",
                                   help="2단계 이후, ATH 대비 이 % 이상 상승 시 3단계 익절") / 100
    with col2:
        rally_sell_pct_1 = st.slider("1단계 매도 비율 (%)", 5, 50, 10, key="rally_sell_pct_1",
                                     help="1단계 발동 시 보유 TQQQ 중 매도할 비율") / 100
        rally_sell_pct_2 = st.slider("2단계 매도 비율 (%)", 5, 50, 20, key="rally_sell_pct_2",
                                     help="2단계 발동 시 보유 TQQQ 중 매도할 비율") / 100
        rally_sell_pct_3 = st.slider("3단계 매도 비율 (%)", 5, 50, 30, key="rally_sell_pct_3",
                                     help="3단계 발동 시 보유 TQQQ 중 매도할 비율") / 100
    rally_sell_to_hedge = st.toggle("익절 대금 → 즉시 헷지매수", value=True, key="rally_sell_to_hedge",
                                    help="ON: 랠리 익절 매도 대금을 즉시 GLD/SHY로 매수 (비율은 헷지 설정 따름)\n"
                                         "OFF: 매도 대금을 현금으로 보유 (기존 동작)")
    st.divider()

    # ── 하락 대응 ─────────────────────────────────────────
    st.subheader("🔴 하락 대응")
    selloff_thresh = st.slider("-3% 트리거 임계값 (%)", -10, -1, -3, key="selloff_thresh",
                               help="QQQ 일일 등락률이 이 값 이하이면 TQQQ 전량 매도 (기본 -3%)") / 100
    use_ma200_sell = st.toggle("MA200 전량매도 사용", value=True, key="use_ma200_sell",
                               help="랠리 익절 1단계 이후 QQQ가 MA200에 터치하면 전량 매도. OFF시 MA200 매도 비활성화")
    ma200_mult     = st.slider("MA200 터치 배수", 1.00, 1.05, 1.01, step=0.001, format="%.3f", key="ma200_mult",
                               help="QQQ ≤ MA200 × 이 배수이면 MA200 매도 발동. 1.01이면 MA200보다 1% 위에서도 매도",
                               disabled=not use_ma200_sell)
    # ── 조건부 무시 필터 ──────────────────────────────────────
    use_ignore_filter = st.toggle(
        "🔍 조건부 무시 필터",
        value=True,
        key="use_ignore_filter",
        help="첫 -3% 무시(케이스 B-3) 시, QQQ 누적 수익률이 임계값 이하이면 무시하지 않고 전량매도")
    if use_ignore_filter:
        ignore_filter_lookback = st.slider(
            "무시 필터 기간 (거래일)", 5, 30, 15, key="ignore_filter_lookback",
            help="이 기간 동안의 QQQ 누적 수익률을 계산하여 무시 여부 판단")
        ignore_filter_thresh = st.slider(
            "무시 필터 임계값 (%)", -15, -1, -5, key="ignore_filter_thresh",
            help="QQQ 누적 수익률이 이 값 이하이면 무시하지 않고 전량매도") / 100
        st.caption(f"첫 -3% 시 QQQ {ignore_filter_lookback}일 누적 ≤ {ignore_filter_thresh*100:.0f}% → 전량매도")
    else:
        ignore_filter_lookback = 10
        ignore_filter_thresh = -0.05
    st.divider()

    # ── 재진입 대기 ────────────────────────────────────────
    st.subheader("⏳ 재진입 대기")
    wait_short       = st.slider("단기 관망 기간 (거래일)", 5, 60, 24, key="wait_short",
                                 help="-3% 매도 후 1~3차까지 대기하는 거래일 수 (기본 21일)")
    wait_long        = st.slider("장기 관망 기간 (거래일)", 10, 120, 24, key="wait_long",
                                 help="4차 이상 매도 시 적용되는 장기 대기 타이머 (기본 24일)")
    heavy_sell_count = st.slider("장기 모드 전환 기준 (매도 횟수)", 2, 8, 4, key="heavy_sell_count",
                                 help="매도 횟수가 이 값 이상이면 장기 관망 기간로 전환")
    ma200_wait_days  = st.slider("MA200 관망 기간 (거래일)", 0, 45, 10, key="ma200_wait_days",
                                 help="MA200 전량매도 후 최소 대기 거래일. 이 기간이 지나야 추세 필터가 평가되어 재진입이 가능합니다 (기본 10일, 0일=관망 없이 추세필터만)")
    st.divider()

    # ── 분할매수: -3% 트리거 ──────────────────────────────
    st.subheader("📊 분할매수 (-3% 트리거)")
    so_a_step  = st.slider("A 간격 (%)", 1, 10, 3, key="so_a_step",
                           help="A구간 각 단계 간 하락 간격. 3%면 -3%, -6%, -9%... 에서 매수") / 100
    so_a_pct   = st.slider("A 매수 비율 (%)", 5, 30, 10, key="so_a_pct",
                           help="A구간 각 단계에서 잔여 현금의 몇 %를 매수할지") / 100
    so_a_cnt   = st.slider("A 횟수", 1, 10, 5, key="so_a_cnt",
                           help="A구간 분할매수 단계 수")
    st.divider()

    # ── 분할매수: MA200 트리거 ────────────────────────────
    _ma200_off = not use_ma200_sell  # MA200 꺼지면 이 섹션 전체 비활성화
    st.subheader("📊 분할매수 (MA200 트리거)")
    if not use_ma200_sell:
        st.caption("⚠️ MA200 전량매도가 꺼져 있어 이 설정은 적용되지 않습니다.")
    ma_a_step  = st.slider("A 간격 (%)", 1, 10, 2, key="ma_a_step",
                           help="MA200 매도 후 A구간 하락 간격. 2%면 -3%, -5%, -7%... 에서 매수",
                           disabled=_ma200_off) / 100
    ma_a_pct   = st.slider("A 매수 비율 (%)", 5, 30, 10, key="ma_a_pct",
                           help="MA200 A구간 각 단계에서 잔여 현금의 몇 %를 매수할지",
                           disabled=_ma200_off) / 100
    ma_a_cnt   = st.slider("A 횟수", 1, 10, 5, key="ma_a_cnt",
                           help="MA200 A구간 분할매수 단계 수",
                           disabled=_ma200_off)
    st.divider()

    # ── 분할매수 노출 캡 (A·B 구간 공통) ────
    st.subheader("🛡️ 분할매수 TQQQ 노출 제한")
    split_buy_max_exposure = st.slider(
        "분할매수 최대 TQQQ 비중 (%)", 10, 100, 40, step=5,
        key="split_buy_max_exposure",
        help="분할매수 시 TQQQ 평가액이 전체 자산(NAV)의 이 비율 이상이면 추가 매수를 건너뜁니다. "
             "A·B 구간 모두에 독립 적용됩니다. "
             "100%=제한 없음(기존 동작). 낮출수록 보수적."
    ) / 100
    if split_buy_max_exposure < 1.0:
        st.caption(f"TQQQ 비중 ≥ NAV의 {split_buy_max_exposure*100:.0f}% → 추가 분할매수 중단 (A·B 공통)")
    else:
        st.caption("제한 없음 — 기존 분할매수 동작과 동일")

    # ── 추세 확인 재진입 (항상 활성) ─────────────────────────
    use_trend_filter = True
    st.subheader("📈 추세 확인 재진입")
    st.caption("대기일 종료 후 추세 미확인이면 재진입을 연기합니다. (항상 활성)")
    trend_require_ma5_above_ma200 = st.checkbox("MA5 > MA200 연속 N일", value=True,
                                                 key="trend_require_ma5_above_ma200",
                                                 help="QQQ 5일 이동평균이 200일 이동평균 위에 N일 연속 유지되어야 재진입 허용")
    ma200_reentry_streak = st.slider("MA5>MA200 연속 일수", 0, 10, 3, key="ma200_reentry_streak",
                                      help="MA5>MA200 연속 일수 조건 (0=즉시)",
                                      disabled=not trend_require_ma5_above_ma200)
    trend_require_above_ma50 = st.checkbox("QQQ > MA50 요구", value=True,
                                            key="trend_require_above_ma50",
                                            help="QQQ가 50일 이동평균 위에 있어야 재진입 허용")
    trend_require_ma200_rising = st.checkbox("MA200 상승 추세 확인", value=True,
                                              key="trend_require_ma200_rising",
                                              help="MA200이 N일 전보다 상승 중이어야 재진입 허용")
    ma200_slope_lookback = st.slider("MA200 기울기 기간 (거래일)", 10, 40, 20,
                                      key="ma200_slope_lookback",
                                      help="MA200 상승 여부를 판단할 때 비교하는 과거 기간")
    trend_filter_mode = st.radio("필터 모드", ["OR", "AND"], horizontal=True,
                                  key="trend_filter_mode",
                                  help="OR: 조건 중 하나만 충족하면 통과. AND: 모든 조건을 동시에 충족해야 통과")
    mode_desc = "하나만 충족" if trend_filter_mode == "OR" else "모두 충족"
    conds = []
    if trend_require_ma5_above_ma200:
        conds.append(f"MA5>MA200 {ma200_reentry_streak}일")
    if trend_require_above_ma50:
        conds.append("QQQ>MA50")
    if trend_require_ma200_rising:
        conds.append(f"MA200 {ma200_slope_lookback}일 상승")
    cap = f"{' + '.join(conds) if conds else '조건 없음'} ({mode_desc} 시 재진입)"
    st.caption(cap)
    st.divider()

    # ── 관망 구간 헷지 ────────────────────────────────────────
    st.subheader("🛡️ 관망 구간 헷지")
    use_hedge = st.checkbox("헷지 활성화 (관망 중 GLD/SHY 투자)", value=True,
                            key="use_hedge",
                            help="OFF 시 관망 구간에서 현금을 그대로 보유합니다")
    if use_hedge:
        hedge_cash_pct = st.slider(
            "현금 보유 비율 (%)", 0, 100, 20,
            key="hedge_cash_pct",
            help="관망 중 이 비율만큼 현금으로 유보, 나머지를 GLD/SHY에 투자") / 100
        hedge_alloc_pct = 1.0 - hedge_cash_pct
        hedge_gld_ratio = st.slider(
            "GLD 비율 (%) — 나머지=SHY", 0, 100, 75,
            key="hedge_gld_ratio",
            help="헷지 금액 중 GLD(금)에 넣을 비율. 나머지는 SHY(단기국채, 듀레이션 1~3년)") / 100
        st.caption(
            f"현금 {hedge_cash_pct*100:.0f}% | "
            f"GLD {hedge_gld_ratio*(1-hedge_cash_pct)*100:.0f}% | "
            f"SHY {(1-hedge_gld_ratio)*(1-hedge_cash_pct)*100:.0f}%"
        )
    else:
        hedge_alloc_pct = 0.0
        hedge_gld_ratio = 0.75
    st.divider()

    # ── 세금 설정 ─────────────────────────────────────────
    st.subheader("💰 세금 설정")
    use_tax = st.toggle("양도소득세 반영", value=True, key="use_tax",
                        help="한국 거주자 해외주식 양도소득세(22%)를 매년 정산하여 NAV에서 차감합니다")
    if use_tax:
        tax_rate = st.slider("세율 (%)", 0, 50, 22, key="tax_rate",
                             help="양도소득세 20% + 지방소득세 2% = 22%") / 100
        tax_deduction_usd = st.number_input(
            "연간 공제액 ($)", value=1800, min_value=0, step=100,
            key="tax_deduction_usd",
            help="한국 기준 연 250만원 (약 $1,800). 0이면 공제 없음")
        st.caption(f"세율 {tax_rate*100:.0f}% | 연간 공제 ${tax_deduction_usd:,}")
    else:
        tax_rate = 0.22
        tax_deduction_usd = 0
    st.divider()

    # ── 폐기된 파라미터 ──────────────────────────────────────
    st.subheader("🗄️ 폐기된 파라미터")
    use_staged_loss_cap = st.checkbox(
        "투입 손실 캡",
        value=False,
        key="use_staged_loss_cap",
        help="분할매수 사이클 중 TQQQ 평가액이 사이클 피크 대비 설정값 이상 하락하면 추가 분할매수를 차단합니다 (기존 포지션은 유지)"
    )
    staged_loss_cap_pct = st.slider(
        "투입 손실 캡 임계값 (%)",
        10, 50, 20,
        step=1,
        key="staged_loss_cap_pct",
        help="사이클 피크 대비 하락률이 이 값 이상이면 추가 매수 차단"
    ) / 100

    # ── B구간 분할매수 (폐기) ─────────────────────────────
    with st.expander("B구간 분할매수 (기본 OFF)", expanded=False):
        st.caption(
            "B구간은 기준가 대비 -20% 이상 대폭락 구간에서 촘촘히 추가 매수하는 옵션입니다. "
            "현재 운용 전략에서는 사용하지 않으며, 기본값 OFF로 유지됩니다."
        )
        col_sob, col_mab = st.columns(2)
        with col_sob:
            st.markdown("**-3% 트리거 B구간**")
            so_b_enabled = st.toggle("B구간 사용", value=False, key="so_b_enabled",
                                     help="OFF: B구간 분할매수 비활성화 / ON: B구간 분할매수 활성화")
            _so_b_off = not so_b_enabled
            so_b_start = st.slider("B 시작 낙폭 (%)", 10, 40, 20, key="so_b_start",
                                   help="B구간 첫 매수가 시작되는 하락률 (기준가 대비)",
                                   disabled=_so_b_off) / 100
            so_b_step  = st.slider("B 간격 (%)", 1, 15, 5, key="so_b_step",
                                   help="B구간 각 단계 간 하락 간격",
                                   disabled=_so_b_off) / 100
            so_b_pct   = st.slider("B 매수 비율 (%)", 5, 30, 10, key="so_b_pct",
                                   help="B구간 각 단계에서 잔여 현금의 몇 %를 매수할지",
                                   disabled=_so_b_off) / 100
            so_b_cnt   = st.slider("B 횟수", 1, 10, 5, key="so_b_cnt",
                                   help="B구간 분할매수 단계 수",
                                   disabled=_so_b_off)
        with col_mab:
            st.markdown("**MA200 트리거 B구간**")
            ma_b_enabled = st.toggle("B구간 사용", value=False, key="ma_b_enabled",
                                     help="OFF: MA200 B구간 분할매수 비활성화 / ON: 활성화",
                                     disabled=_ma200_off)
            _ma_b_off = _ma200_off or not ma_b_enabled
            ma_b_start = st.slider("B 시작 낙폭 (%)", 10, 40, 20, key="ma_b_start",
                                   help="MA200 B구간 첫 매수가 시작되는 하락률",
                                   disabled=_ma_b_off) / 100
            ma_b_step  = st.slider("B 간격 (%)", 1, 15, 3, key="ma_b_step",
                                   help="MA200 B구간 각 단계 간 하락 간격",
                                   disabled=_ma_b_off) / 100
            ma_b_pct   = st.slider("B 매수 비율 (%)", 5, 30, 10, key="ma_b_pct",
                                   help="MA200 B구간 각 단계에서 잔여 현금의 몇 %를 매수할지",
                                   disabled=_ma_b_off) / 100
            ma_b_cnt   = st.slider("B 횟수", 1, 10, 5, key="ma_b_cnt",
                                   help="MA200 B구간 분할매수 단계 수",
                                   disabled=_ma_b_off)

# ── 파라미터 객체 ─────────────────────────────────────────
p = Params(
    rally_thresh_1=rally_thresh_1, rally_thresh_2=rally_thresh_2,
    rally_thresh_3=rally_thresh_3,
    rally_sell_pct_1=rally_sell_pct_1, rally_sell_pct_2=rally_sell_pct_2,
    rally_sell_pct_3=rally_sell_pct_3,
    rally_sell_to_hedge=rally_sell_to_hedge,
    selloff_thresh=selloff_thresh, use_ma200_sell=use_ma200_sell, ma200_mult=ma200_mult,
    use_ignore_filter=use_ignore_filter, ignore_filter_lookback=ignore_filter_lookback, ignore_filter_thresh=ignore_filter_thresh,
    wait_short=wait_short, wait_long=wait_long, heavy_sell_count=heavy_sell_count,
    ma200_wait_days=ma200_wait_days,
    so_a_step=so_a_step, so_a_pct=so_a_pct, so_a_cnt=so_a_cnt,
    so_b_enabled=so_b_enabled, so_b_start=so_b_start, so_b_step=so_b_step, so_b_pct=so_b_pct, so_b_cnt=so_b_cnt,
    ma_a_step=ma_a_step, ma_a_pct=ma_a_pct, ma_a_cnt=ma_a_cnt,
    ma_b_enabled=ma_b_enabled, ma_b_start=ma_b_start, ma_b_step=ma_b_step, ma_b_pct=ma_b_pct, ma_b_cnt=ma_b_cnt,
    split_buy_max_exposure=split_buy_max_exposure,
    use_staged_loss_cap=use_staged_loss_cap,
    staged_loss_cap_pct=staged_loss_cap_pct,
    # 추세 필터
    use_trend_filter=use_trend_filter,
    trend_require_ma5_above_ma200=trend_require_ma5_above_ma200,
    ma200_reentry_streak=ma200_reentry_streak,
    trend_require_above_ma50=trend_require_above_ma50,
    trend_require_ma200_rising=trend_require_ma200_rising,
    ma200_slope_lookback=ma200_slope_lookback,
    trend_filter_mode=trend_filter_mode,

    # 헷지
    use_hedge=use_hedge,
    hedge_alloc_pct=hedge_alloc_pct,
    hedge_gld_ratio=hedge_gld_ratio,
    # 세금
    use_tax=use_tax,
    tax_rate=tax_rate,
    tax_deduction_usd=float(tax_deduction_usd),
)

# ── 데이터 로드 & 날짜 필터 ──────────────────────────────
df_full = load_data(start_year)

start_dt = pd.Timestamp(f"{start_year}-{start_month:02d}-01")
end_dt   = pd.Timestamp(f"{end_year}-{end_month:02d}-01") + pd.offsets.MonthEnd(0)
df       = df_full[(df_full.index >= start_dt) & (df_full.index <= end_dt)].copy()

if len(df) < 2:
    st.error("선택한 기간에 데이터가 없습니다. 기간을 다시 설정해 주세요.")
    st.stop()

# ── 백테스트 실행 ─────────────────────────────────────────
with st.spinner("백테스트 계산 중..."):
    result = run_backtest(df, p, float(initial_capital))

nav     = result["nav"]
trades  = result["trades"]
signals = result["signals"]

bnh_shares = float(initial_capital) / df["tqqq"].iloc[0]
bnh_nav    = df["tqqq"] * bnh_shares

m     = calc_metrics(nav, float(initial_capital))
m_bnh = calc_bnh_metrics(df, float(initial_capital))

bear_periods = find_bear_periods(nav, threshold=-0.35)

# ── 탭 구성 ──────────────────────────────────────────────
tab_today, tab_main, tab_bear, tab_signals, tab_compare, tab_manual = st.tabs(
    ["📱 오늘의 신호", "📊 결과", "🔴 하락장 분석", "📋 일별 시그널", "💾 저장·비교", "📖 전략 매뉴얼"]
)

# ════════════════════════════════════════════════════════
# TAB 0: 오늘의 신호 (실전 매매용)
# ════════════════════════════════════════════════════════
with tab_today:
    # ── 포지션 불러오기 ─────────────────────────────────
    if "my_position" not in st.session_state:
        _loaded = load_position()
        st.session_state["my_position"] = _loaded if _loaded else default_position()
    pos = st.session_state["my_position"]

    # ── 신호 계산 ─────────────────────────────────────
    if not pos.get("trade_start_date"):
        st.info("👈 왼쪽 사이드바의 **👤 내 포지션**에서 거래 시작일과 보유 정보를 입력하고 **저장** 버튼을 눌러주세요.")
    else:
        # initial_investment가 0이면 자동 추정
        #   (TQQQ 주수 × 평균단가) + 현금 + 헷지자산(현재가 기준)
        #   자동 추정도 불가능하면 $10,000 사용 (신호 판정에는 영향 없음)
        _init_inv = float(pos.get("initial_investment", 0.0))
        _auto_inv = False
        if _init_inv <= 0:
            _latest = df_full.iloc[-1]
            _gld_p = float(_latest.get("gld", 0)) if "gld" in df_full.columns else 0.0
            _tlt_p = float(_latest.get("tlt", 0)) if "tlt" in df_full.columns else 0.0
            _tqqq_cost = float(pos.get("tqqq_shares", 0)) * float(pos.get("tqqq_avg_cost", 0))
            _gld_val   = float(pos.get("gld_shares", 0)) * _gld_p
            _tlt_val   = float(pos.get("tlt_shares", 0)) * _tlt_p
            _cash      = float(pos.get("cash_usd", 0))
            _init_inv  = _tqqq_cost + _cash + _gld_val + _tlt_val
            if _init_inv <= 0:
                _init_inv = 10000.0
            _auto_inv = True

        if _auto_inv:
            st.caption(f"ℹ️ 거래 시작일 투자금을 자동 추정: **${_init_inv:,.0f}** "
                       f"(= TQQQ 주수 × 평균단가 + 현금 + 헷지자산). "
                       f"이 값은 '전략 기대 포지션' 표시에만 쓰이며, **오늘의 신호 판정에는 영향 없습니다.**")

        with st.spinner("오늘의 신호 계산 중..."):
            sig = compute_today_signal(
                df_full=df_full,
                params=p,
                trade_start_date=pos["trade_start_date"],
                initial_investment=_init_inv,
            )

        if not sig["ok"]:
            st.error(f"신호 계산 실패: {sig['error']}")
        else:
            action_type = sig["action_type"]
            action_badge = {
                "BUY_ALL":    "🟢 **전량매수**",
                "REENTRY":    "🟢 **재진입 (전량매수)**",
                "SELL_ALL":   "🔴 **전량매도**",
                "SPLIT_BUY":  "🟡 **분할매수**",
                "RALLY_SELL": "💰 **랠리 익절 (부분매도)**",
                "HEDGE_BUY":  "🛡️ **헷지 매수**",
                "HEDGE_SELL": "🛡️ **헷지 매도**",
                "WAITING":    "⏳ **관망**",
                "HOLD":       "✅ **보유 유지**",
            }.get(action_type, f"ℹ️ {action_type}")

            # ════════════════════════════════════════════════
            # ① 오늘 취해야 할 행동 (최상단)
            # ════════════════════════════════════════════════
            recs = calc_action_recommendation(sig, pos, p)
            st.markdown(f"## 🎯 오늘 취해야 할 행동  ·  `{sig['today_date']}`")
            st.markdown(f"#### {action_badge} — {sig['today_signal']}")

            if action_type in ("HOLD", "WAITING"):
                if action_type == "WAITING":
                    st.success("👉 **관망 중입니다. 오늘은 아무 것도 하지 마세요.** "
                               "전략이 자동으로 재진입 타이밍을 찾을 때까지 기다립니다.")
                else:
                    st.success("👉 **보유 유지. 오늘은 매매 없음.**")
            elif not recs:
                st.info("특별한 액션이 없습니다. 신호 내용을 참고하세요.")
            else:
                rec_df = pd.DataFrame(recs)
                rec_df["수량"]       = rec_df["shares"].apply(lambda x: f"{x:,.4f} 주" if x > 0 else "-")
                rec_df["예상 금액"]  = rec_df["amount_usd"].apply(lambda x: f"${x:,.0f}" if x > 0 else "-")
                rec_df["기준가"]     = rec_df["price"].apply(lambda x: f"${x:,.2f}" if x > 0 else "-")
                st.dataframe(
                    rec_df[["asset", "action", "수량", "예상 금액", "기준가", "note"]].rename(
                        columns={"asset": "자산", "action": "행동", "note": "비고"}
                    ),
                    use_container_width=True, hide_index=True
                )
                st.caption("⚠️ 실제 체결은 **미국장 개장 후 시가**에 진행됩니다. "
                           "위 '기준가'는 어제 종가 기준이므로 실제 체결가는 다를 수 있습니다.")

            # ════════════════════════════════════════════════
            # ② 내 자산 추이 (오늘 실제 잔액 기준 역산)
            # ════════════════════════════════════════════════
            st.markdown("### 📈 내 자산 추이 (오늘 실제 잔액 기준 역산)")

            _nav_series = sig.get("nav_series")
            _df_slice   = sig.get("df_slice")

            if _nav_series is not None and len(_nav_series) >= 2:
                # 오늘 실제 총자산 계산
                user_today_total = compute_user_current_total(pos, sig["prices"])

                # 스케일 팩터: 실제 오늘 자산 / 백테스트 마지막 NAV
                _sim_last = float(_nav_series.iloc[-1])

                if user_today_total > 0 and _sim_last > 0:
                    scale = user_today_total / _sim_last
                    scaled_nav = _nav_series * scale
                    _scaled_initial = float(scaled_nav.iloc[0])
                    _profit = user_today_total - _scaled_initial
                    _profit_pct = (_profit / _scaled_initial * 100) if _scaled_initial > 0 else 0.0

                    # TQQQ Buy & Hold 비교 (같은 초기값으로 시작했을 때)
                    if _df_slice is not None and "tqqq" in _df_slice.columns:
                        _tqqq_start = float(_df_slice["tqqq"].iloc[0])
                        _bnh = _df_slice["tqqq"] / _tqqq_start * _scaled_initial
                    else:
                        _bnh = None

                    import plotly.graph_objects as _go
                    fig = _go.Figure()
                    fig.add_trace(_go.Scatter(
                        x=scaled_nav.index, y=scaled_nav.values,
                        name="전략 (내 자산)", line=dict(color="#2E86DE", width=2.5),
                        hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.0f}<extra></extra>"
                    ))
                    if _bnh is not None:
                        fig.add_trace(_go.Scatter(
                            x=_bnh.index, y=_bnh.values,
                            name="TQQQ Buy & Hold", line=dict(color="#A0A0A0", width=1.5, dash="dot"),
                            hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.0f}<extra></extra>"
                        ))
                    # 오늘 실제 총자산 점 강조
                    fig.add_trace(_go.Scatter(
                        x=[scaled_nav.index[-1]], y=[user_today_total],
                        name="오늘 실제", mode="markers",
                        marker=dict(color="#EB5757", size=11, symbol="circle"),
                        hovertemplate="오늘 실제 자산<br>$%{y:,.0f}<extra></extra>"
                    ))
                    fig.update_layout(
                        height=380,
                        margin=dict(l=10, r=10, t=30, b=10),
                        hovermode="x unified",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                        yaxis_title="USD",
                    )
                    _log_t = st.toggle("로그 스케일", value=False, key="today_nav_logscale")
                    if _log_t:
                        fig.update_yaxes(type="log")
                    st.plotly_chart(fig, use_container_width=True)

                    # 요약 지표
                    _nav_max = float(scaled_nav.cummax().iloc[-1])
                    _mdd = (user_today_total / _nav_max - 1) * 100 if _nav_max > 0 else 0.0
                    _days = (scaled_nav.index[-1] - scaled_nav.index[0]).days
                    _years = _days / 365.25 if _days > 0 else 0.01
                    _cagr = ((user_today_total / _scaled_initial) ** (1/_years) - 1) * 100 if _scaled_initial > 0 and _years > 0 else 0.0

                    mc1, mc2, mc3, mc4 = st.columns(4)
                    mc1.metric("역산 시작 자산", f"${_scaled_initial:,.0f}",
                               help=f"오늘 실제 자산에서 역산한 {pos['trade_start_date']} 시점의 자산")
                    mc2.metric("오늘 자산", f"${user_today_total:,.0f}",
                               delta=f"${_profit:+,.0f}")
                    mc3.metric("총 수익률", f"{_profit_pct:+.1f}%")
                    mc4.metric("연평균 수익률(CAGR)", f"{_cagr:+.1f}%")

                    st.caption(f"ℹ️ 차트의 마지막 점(빨간 원)은 오늘 입력하신 실제 보유 자산입니다. "
                               f"과거 곡선은 **전략을 그대로 따랐다고 가정**했을 때의 추정 궤적이며, "
                               f"스케일은 오늘 실제 금액(${user_today_total:,.0f})에 맞춰 역산되었습니다. "
                               f"실제 수익률이 위 CAGR과 다르다면 전략과 다른 매매를 하셨을 가능성이 큽니다.")
                else:
                    st.info("오늘 실제 총자산이 0입니다. TQQQ 주수·평균단가·현금을 입력해 주세요.")
            else:
                st.info("그래프를 그릴 데이터가 부족합니다.")

            # ════════════════════════════════════════════════
            # ③ 참고 정보 (오늘 가격, 비교, 다음 트리거)
            # ════════════════════════════════════════════════
            st.markdown("### 💹 오늘 가격")
            pr = sig["prices"]
            cols = st.columns(4)
            cols[0].metric("QQQ", f"${pr['qqq']:,.2f}",
                           delta=f"MA200 ${pr['ma200']:,.2f}")
            cols[1].metric("TQQQ", f"${pr['tqqq']:,.2f}")
            if pr.get("gld"):
                cols[2].metric("GLD", f"${pr['gld']:,.2f}")
            if pr.get("tlt"):
                cols[3].metric("SHY", f"${pr['tlt']:,.2f}")

            # ── 전략 기준 포지션 vs 사용자 포지션 비교 ──
            with st.expander("📊 전략 기준 포지션 vs 내 실제 포지션 비교"):
                exp = sig["expected"]
                _mytqqq = float(pos.get("tqqq_shares", 0))
                _mycash = float(pos.get("cash_usd", 0))
                _mygld  = float(pos.get("gld_shares", 0))
                _mytlt  = float(pos.get("tlt_shares", 0))
                comp_df = pd.DataFrame([
                    ["TQQQ 주수",  f"{exp['tqqq_shares']:,.2f}",  f"{_mytqqq:,.2f}"],
                    ["현금 (USD)", f"${exp['cash_usd']:,.0f}",    f"${_mycash:,.0f}"],
                    ["GLD 주수",   f"{exp['gld_shares']:,.2f}",   f"{_mygld:,.2f}"],
                    ["SHY 주수",   f"{exp['tlt_shares']:,.2f}",   f"{_mytlt:,.2f}"],
                ], columns=["항목", "전략이 기대하는 보유", "내 실제 보유"])
                st.dataframe(comp_df, use_container_width=True, hide_index=True)
                st.caption("차이가 크다면 그동안 일부 신호를 놓쳤거나 전략과 다른 거래를 한 것입니다. "
                           "앞으로 이 앱의 신호를 따라가면 점차 일치하게 됩니다.")

            # ── 다음 트리거 예상가 ──
            if sig["next_triggers"]:
                with st.expander("🔮 다음 주요 이벤트 예상가"):
                    for t in sig["next_triggers"]:
                        st.markdown(f"- {t}")

            # ── 최근 거래 ──
            if len(sig["last_trades"]) > 0:
                with st.expander("📋 최근 5건 거래 (거래 시작일 이후 전략 기준)"):
                    _lt = sig["last_trades"].copy()
                    _lt["date"] = pd.to_datetime(_lt["date"]).dt.strftime("%Y-%m-%d")
                    st.dataframe(_lt[["date", "action", "tqqq_price", "qqq_price", "reason"]],
                                 use_container_width=True, hide_index=True)

            st.divider()
            st.caption(f"📅 데이터 최종 업데이트: {sig['today_date']} | "
                       f"전략 시뮬레이션 NAV: ${sig['nav']:,.0f} | "
                       f"데이터는 Yahoo Finance 기준이며 접속 시마다 최신 데이터로 갱신됩니다.")

# ════════════════════════════════════════════════════════
# TAB 1: 결과
# ════════════════════════════════════════════════════════
with tab_main:
    st.subheader("📊 성과 요약")

    _total_tax = result.get("total_tax_paid", 0)
    if use_tax and _total_tax > 0:
        col_a, col_b, col_c, col_d, col_e, col_f = st.columns(6)
    else:
        col_a, col_b, col_c, col_d, col_e = st.columns(5)
        col_f = None
    final     = m.get("최종 잔고", 0)
    profit    = final - float(initial_capital)
    with col_a:
        st.metric("최종 잔고", fmt_money(final),
                  delta=fmt_money(profit))
    with col_b:
        st.metric("CAGR", fmt_pct(m.get("CAGR")),
                  delta=f"B&H: {fmt_pct(m_bnh.get('CAGR'))}")
    with col_c:
        st.metric("MDD", fmt_pct(m.get("MDD")),
                  delta=f"B&H: {fmt_pct(m_bnh.get('MDD'))}", delta_color="inverse")
    with col_d:
        st.metric("Sharpe", fmt_ratio(m.get("Sharpe Ratio")),
                  delta=f"B&H: {fmt_ratio(m_bnh.get('Sharpe Ratio'))}")
    with col_e:
        st.metric("총 수익률", fmt_pct(m.get("총 수익률")),
                  delta=f"B&H: {fmt_pct(m_bnh.get('총 수익률'))}")
    if col_f is not None:
        with col_f:
            st.metric("💰 총 납부 세금", fmt_money(_total_tax))

    with st.expander("상세 지표"):
        cx, cy = st.columns(2)
        with cx:
            st.markdown("**전략**")
            st.write(f"- 기간: {m.get('백테스트 기간 (년)', 0):.1f}년")
            st.write(f"- 연도별 승률: {m.get('승률 (연도별)', 0)*100:.0f}%")
            st.write(f"- 최고 연도: {m.get('최고의 연도')} ({fmt_pct(m.get('최고의 연도 수익률'))})")
            st.write(f"- 최악 연도: {m.get('최악의 연도')} ({fmt_pct(m.get('최악의 연도 수익률'))})")
        with cy:
            st.markdown("**TQQQ Buy & Hold**")
            st.write(f"- 연도별 승률: {m_bnh.get('승률 (연도별)', 0)*100:.0f}%")
            st.write(f"- 최고 연도: {m_bnh.get('최고의 연도')} ({fmt_pct(m_bnh.get('최고의 연도 수익률'))})")
            st.write(f"- 최악 연도: {m_bnh.get('최악의 연도')} ({fmt_pct(m_bnh.get('최악의 연도 수익률'))})")

    log_scale = st.toggle("로그 스케일", value=True)
    st.plotly_chart(chart_nav(nav, bnh_nav, trades, bear_periods, log_scale=log_scale), use_container_width=True)

    cl, cr = st.columns(2)
    with cl:
        st.plotly_chart(chart_drawdown(nav, bnh_nav, bear_periods), use_container_width=True)
    with cr:
        annual_strat = m.get("연도별 수익률", pd.Series(dtype=float))
        annual_bnh   = m_bnh.get("연도별 수익률", pd.Series(dtype=float))
        st.plotly_chart(chart_annual_returns(annual_strat, annual_bnh), use_container_width=True)

    st.plotly_chart(chart_cash_ratio(nav, signals), use_container_width=True)

    # 거래 기록
    st.subheader("📋 거래 기록")
    if len(trades) > 0:
        td = trades.copy()
        td["date"] = pd.to_datetime(td["date"]).dt.strftime("%Y-%m-%d")

        # 액션별 한글 레이블
        _action_map = {
            "BUY_ALL":         "📈 전량매수",
            "SELL_ALL":        "📉 -3% 전량매도",
            "SELL_ALL_MA200":  "◆ MA200 전량매도",
            "HEDGE_BUY":       "🛡️ 헷지매수",
            "HEDGE_SELL":      "🛡️ 헷지매도",
            "SELLOFF_WAIT":    "⏳ 관망유지",
            "SELLOFF_IGNORED": "🚫 -3%무시",
            "RALLY_SELL_1":    "💰 랠리1단계",
            "RALLY_SELL_2":    "💰 랠리2단계",
            "RALLY_SELL_3":    "💰 랠리3단계",
            "REGIME_REDUCE":   "🔻 레짐방어",
            "REGIME_RESTORE":  "🔺 레짐복구",
            "SELLOFF_CONDITIONAL_SELL": "🔴 조건부매도",
            "TAX_PAID":        "💰 세금납부",
            "TAX_SHORTFALL":   "⚠️ 세금부족",
        }
        td["구분"] = td["action"].apply(
            lambda a: _action_map.get(a, a) if not str(a).startswith("SPLIT_BUY") else "📊 분할매수"
        )

        # TQQQ 거래 금액
        td["TQQQ금액"] = td.apply(
            lambda r: f"${r['shares']*r['tqqq_price']:,.0f}"
            if r["shares"] > 0 and r["tqqq_price"] > 0 else "", axis=1
        )

        # 헷지 자산 금액 (gld_val + tlt_val)
        if "gld_val" in td.columns:
            td["헷지금액"] = td.apply(
                lambda r: (
                    f"GLD ${r['gld_val']:,.0f} + SHY ${r['tlt_val']:,.0f}"
                    if (r.get("gld_val", 0) + r.get("tlt_val", 0)) > 0 else ""
                ), axis=1
            )
        else:
            td["헷지금액"] = ""

        td["TQQQ가"] = td["tqqq_price"].apply(lambda x: f"${x:,.2f}" if x > 0 else "")
        td["QQQ가"]  = td["qqq_price"].apply(lambda x: f"${x:,.2f}" if x > 0 else "")

        st.dataframe(
            td[["date", "구분", "TQQQ금액", "헷지금액", "TQQQ가", "QQQ가", "reason"]].rename(
                columns={"date": "날짜", "reason": "내용"}
            ),
            use_container_width=True, height=340,
        )
        _tqqq_cnt  = len(td[td["action"].isin(["BUY_ALL", "SELL_ALL", "SELL_ALL_MA200"])])
        _hedge_cnt = len(td[td["action"].isin(["HEDGE_BUY", "HEDGE_SELL"])])
        _split_cnt = len(td[td["action"].str.startswith("SPLIT_BUY", na=False)])
        st.caption(
            f"총 {len(trades):,}건 | 전량매수/매도: {_tqqq_cnt}건 | "
            f"헷지거래: {_hedge_cnt}건 | 분할매수: {_split_cnt}건"
        )
    else:
        st.info("거래 기록이 없습니다.")

# ════════════════════════════════════════════════════════
# TAB 2: 하락장 분석
# ════════════════════════════════════════════════════════
with tab_bear:
    st.subheader("🔴 하락장 구간 분석 (전략 MDD -35% 이상)")
    st.caption("전략 포트폴리오 NAV 기준으로 -35% 이상 낙폭 구간을 표시합니다.")

    if bear_periods:
        rows = []
        for bp in bear_periods:
            duration = (pd.Timestamp(bp["end"]) - pd.Timestamp(bp["start"])).days
            rows.append({
                "이슈":       bp["issue"],
                "시작":       str(bp["start"])[:10],
                "종료":       str(bp["end"])[:10],
                "바닥 날짜":  str(bp["bottom"])[:10],
                "최대 낙폭":  f"{bp['mdd']*100:.1f}%",
                "기간 (일)":  f"{duration:,}일",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.markdown("---")
        for bp in bear_periods:
            mdd_str = f"{bp['mdd']*100:.1f}%"
            label = f"📉 {bp['issue']}  |  {str(bp['start'])[:10]} ~ {str(bp['end'])[:10]}  |  MDD {mdd_str}"
            with st.expander(label):
                seg     = nav[bp["start"]:bp["end"]]
                bnh_seg = bnh_nav.reindex(seg.index)
                import plotly.graph_objects as _go
                fig2 = _go.Figure()
                fig2.add_trace(_go.Scatter(x=seg.index, y=seg.values,
                    name="전략", line=dict(color="#2196F3")))
                fig2.add_trace(_go.Scatter(x=bnh_seg.index, y=bnh_seg.values,
                    name="TQQQ B&H", line=dict(color="#FF9800", dash="dot")))
                fig2.update_layout(height=260, margin=dict(t=10, b=10), yaxis_type="log")
                st.plotly_chart(fig2, use_container_width=True)
    else:
        st.success("✅ 선택 기간 중 전략 MDD -35% 이상 구간 없음")

# ════════════════════════════════════════════════════════
# TAB 3: 일별 시그널 & 엑셀 다운로드
# ════════════════════════════════════════════════════════
with tab_signals:
    st.subheader("📋 일별 시그널 / 엑셀 다운로드")

    col_order = ["date", "signal", "nav", "stock_weight(%)", "drawdown(%)",
                 "qqq_vs_ath(%)", "ma200_dev(%)", "qqq_change(%)", "qqq", "ma200",
                 "stock_value", "hedge_value", "cash_value",
                 "ytd_realized_pnl", "total_tax_paid", "vix"]
    display_cols = [c for c in col_order if c in signals.columns]
    sig_df = signals[display_cols].copy()

    # 컬럼 한글 이름 매핑
    _col_rename = {
        "date":           "날짜",
        "signal":         "시그널",
        "nav":            "NAV",
        "stock_weight(%)":"TQQQ비중(%)",
        "drawdown(%)":    "낙폭(%)",
        "qqq_vs_ath(%)":  "QQQ vs ATH(%)",
        "ma200_dev(%)":   "QQQ vs MA200(%)",
        "qqq_change(%)":  "QQQ등락(%)",
        "qqq":            "QQQ가격",
        "ma200":          "MA200",
        "stock_value":    "TQQQ평가액",
        "hedge_value":    "헷지자산액",
        "cash_value":     "현금",
        "ytd_realized_pnl": "연간실현손익",
        "total_tax_paid":   "누적세금",
        "vix":            "VIX",
    }

    # 시그널 필터
    sig_filter = st.multiselect(
        "시그널 필터 (비워두면 전체)",
        options=sorted(sig_df["signal"].unique().tolist()),
        default=[],
    )
    filtered = sig_df[sig_df["signal"].isin(sig_filter)] if sig_filter else sig_df

    # 포맷된 표시용 DataFrame
    disp = filtered.copy()
    for col in ["nav", "stock_value", "hedge_value", "cash_value",
                 "ytd_realized_pnl", "total_tax_paid"]:
        if col in disp.columns:
            disp[col] = disp[col].apply(lambda x: f"${x:,.0f}" if pd.notna(x) else "")
    for col in ["stock_weight(%)", "drawdown(%)", "ma200_dev(%)", "qqq_change(%)", "qqq_vs_ath(%)"]:
        if col in disp.columns:
            disp[col] = disp[col].apply(lambda x: f"{x:+.1f}%" if pd.notna(x) else "")
    for col in ["qqq", "ma200"]:
        if col in disp.columns:
            disp[col] = disp[col].apply(lambda x: f"${x:,.2f}" if pd.notna(x) else "")
    if "vix" in disp.columns:
        disp["vix"] = disp["vix"].apply(lambda x: f"{x:.1f}" if pd.notna(x) else "")

    disp = disp.rename(columns=_col_rename)
    st.dataframe(disp, use_container_width=True, height=420)
    st.caption(f"{len(filtered):,}행 표시 / 전체 {len(sig_df):,}일")

    # ── 엑셀 다운로드 ──────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 📥 엑셀 다운로드")

    def build_excel(df_signals, df_trades, metrics_dict) -> bytes:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_signals.to_excel(writer, index=False, sheet_name="일별 시그널")
            if len(df_trades) > 0:
                df_trades.to_excel(writer, index=False, sheet_name="거래 기록")
            summary = [{"지표": k, "값": v}
                       for k, v in metrics_dict.items()
                       if not isinstance(v, pd.Series)]
            pd.DataFrame(summary).to_excel(writer, index=False, sheet_name="성과 요약")
        output.seek(0)
        return output.read()

    excel_bytes = build_excel(sig_df, trades, m)
    fname = (f"backtest_{start_year}{start_month:02d}_{end_year}{end_month:02d}"
             f"_{datetime.now().strftime('%H%M%S')}.xlsx")

    st.download_button(
        label="📥 엑셀 파일 다운로드 (일별 시그널 + 거래 기록 + 성과 요약)",
        data=excel_bytes,
        file_name=fname,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

# ════════════════════════════════════════════════════════
# TAB 4: 저장·비교
# ════════════════════════════════════════════════════════
with tab_compare:
    st.subheader("💾 현재 결과 저장")
    save_name = st.text_input("저장 이름", placeholder="예: 기본전략_40년, VIX25_2010~")

    if st.button("💾 저장", type="primary", use_container_width=True):
        if not save_name.strip():
            st.warning("저장 이름을 입력해 주세요.")
        else:
            fname = save_result(
                save_name.strip(),
                p.__dict__,
                m,
                {"start": f"{start_year}-{start_month:02d}",
                 "end":   f"{end_year}-{end_month:02d}"},
            )
            st.success(f"✅ 저장 완료: {fname}")
            st.rerun()

    st.divider()
    st.subheader("📊 저장된 결과 비교")

    saves = load_all_saves()
    if saves:
        cmp_df = get_comparison_df(saves)

        # 비교 테이블 포맷
        display_cmp = cmp_df.drop(columns=["파일명"]).copy()
        if "최종잔고($)" in display_cmp.columns:
            display_cmp["최종잔고($)"] = display_cmp["최종잔고($)"].apply(
                lambda x: f"${float(x):,.0f}" if x else "")

        st.dataframe(display_cmp, use_container_width=True, hide_index=True)

        # 삭제
        st.markdown("---")
        save_options = {f"{r['이름']} ({r['기간']})": r["파일명"]
                        for _, r in cmp_df.iterrows()}
        to_delete = st.selectbox("삭제할 결과", options=list(save_options.keys()))
        if st.button("🗑️ 선택 결과 삭제", type="secondary"):
            delete_save(save_options[to_delete])
            st.success("삭제 완료")
            st.rerun()

        # 파라미터 비교
        if len(saves) >= 2:
            st.markdown("---")
            st.markdown("#### 🔍 파라미터 상세 비교")
            selected = st.multiselect(
                "비교할 결과 (2개 이상)",
                [s["name"] for s in saves],
                default=[s["name"] for s in saves[:2]],
            )
            chosen = [s for s in saves if s["name"] in selected]
            if len(chosen) >= 2:
                pdict = {}
                for sv in chosen:
                    for k, v in sv.get("params", {}).items():
                        pdict.setdefault(k, {})[sv["name"]] = v
                st.dataframe(pd.DataFrame(pdict).T, use_container_width=True)
    else:
        st.info("저장된 결과가 없습니다. 위에서 현재 결과를 저장해 보세요.")

# ════════════════════════════════════════════════════════
# TAB 5: 전략 매뉴얼
# ════════════════════════════════════════════════════════
with tab_manual:
    st.subheader("📖 전략 매뉴얼")

    # ── 초보자 가이드 (상단 임베드) ────────────────────────────
    _user_manual_path = Path(__file__).parent / "TQQQ_사용자_매뉴얼.html"
    with st.expander("🧭 초보자 가이드 — 이걸 먼저 읽으세요 (클릭해서 펼치기)", expanded=True):
        if _user_manual_path.exists():
            _user_manual_html = _user_manual_path.read_text(encoding="utf-8")
            components.html(_user_manual_html, height=5200, scrolling=True)
        else:
            st.warning(
                f"사용자 매뉴얼 파일을 찾을 수 없습니다: `{_user_manual_path.name}`  \n"
                "프로젝트 루트에 해당 파일이 있는지 확인해 주세요."
            )

    st.divider()
    st.markdown("### ⚙️ 파라미터 상세")
    st.caption("아래는 코드·파라미터 수준의 상세 설명입니다. 설정 슬라이더는 좌측 사이드바에 있습니다.")

    # ── 인쇄 버튼 ──────────────────────────────────────────
    _manual_path = Path(__file__).parent / "strategy.md"
    _strategy_text = _manual_path.read_text(encoding="utf-8") if _manual_path.exists() else ""
    _print_html = _build_print_html(
        p, int(initial_capital),
        start_year, start_month, end_year, end_month,
        _strategy_text,
    )
    _print_fname = f"TQQQ전략매뉴얼_{date.today().strftime('%Y%m%d')}.html"
    col_pbtn, col_pinfo = st.columns([1, 3])
    with col_pbtn:
        st.download_button(
            label="🖨️ 매뉴얼 인쇄 (HTML 다운로드)",
            data=_print_html.encode("utf-8"),
            file_name=_print_fname,
            mime="text/html",
            use_container_width=True,
            help="파일을 다운로드 후 브라우저에서 열면 인쇄 대화상자가 자동으로 열립니다.",
        )
    with col_pinfo:
        st.info(
            "⬅️ 버튼을 클릭하면 **현재 파라미터 설정값 + 전략 설명**이 담긴 HTML 파일이 다운로드됩니다.  \n"
            "파일을 열면 인쇄 대화상자가 자동으로 열립니다. **PDF로 저장**도 가능합니다.",
            icon=None,
        )
    st.markdown("---")

    # ── 전략 개요 ──────────────────────────────────────────────
    st.info(
        "**TQQQ 전략 개요:** QQQ 일간 수익률 기준 이벤트 감지 → TQQQ 전량매도(방어) / 분할매수 / 익절(랠리)을 자동 실행합니다.  \n"
        "관망 중에는 GLD/SHY 헷지 자산을 보유하며, 추세 조건 확인 후 재진입합니다.  \n"
        "아래 표는 **기본값** 기준입니다. 사이드바 파라미터에서 조정할 수 있습니다."
    )

    # ── CSS: 툴팁 스타일 ───────────────────────────────────────
    st.markdown("""<style>
.param-table{border-collapse:collapse;width:100%;margin:4px 0 10px 0;overflow:visible}
.param-table th{background:#1e293b;color:#94a3b8;font-size:11.5px;padding:6px 10px;
  text-align:left;font-weight:600;letter-spacing:.3px;white-space:nowrap}
.param-table td{padding:7px 10px;border-bottom:1px solid #1e293b33;font-size:13px;
  vertical-align:middle;overflow:visible}
.param-table tr:last-child td{border-bottom:none}
.ptip{position:relative;border-bottom:1.5px dashed #60a5fa;cursor:help;
  display:inline-block;line-height:1.3}
.ptip .tipbox{visibility:hidden;opacity:0;position:absolute;z-index:9999;
  left:0;top:calc(100% + 6px);background:#0f172a;color:#e2e8f0;
  border:1px solid #334155;border-radius:8px;padding:11px 14px;width:330px;
  font-size:12.5px;line-height:1.6;box-shadow:0 6px 24px rgba(0,0,0,.6);
  transition:opacity .15s ease;pointer-events:none;white-space:normal}
.ptip:hover .tipbox{visibility:visible;opacity:1}
</style>""", unsafe_allow_html=True)

    # ── 헬퍼 함수 ─────────────────────────────────────────────
    def _pct(v: float) -> str:
        return f"{v*100:.0f}%"

    def _tip(label: str, tip: str) -> str:
        return f'<span class="ptip">{label}<span class="tipbox">{tip}</span></span>'

    def _htable(rows: list, headers: list = None) -> str:
        """rows: list of (label, tip, val1, val2, ...) tuples"""
        ncols = len(rows[0]) - 2
        if headers is None:
            if ncols == 3:
                headers = ["파라미터", "<b>기본값</b>", "조정 범위", "현재 설정"]
            else:
                headers = ["파라미터", "<b>기본값</b>", "현재 설정"]
        ths = "".join(f"<th>{h}</th>" for h in headers)
        trs = "".join(
            "<tr>" + f"<td>{_tip(r[0], r[1])}</td>"
            + "".join(f"<td>{v}</td>" for v in r[2:]) + "</tr>"
            for r in rows
        )
        return f'<table class="param-table"><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table>'

    _on  = "✅ ON"
    _off = "⭕ OFF"

    # 1. 상승 익절
    st.subheader("🟢 상승 익절 (랠리)")
    st.caption("TQQQ 보유 중, QQQ가 고점 대비 일정 % 이상 상승하면 단계별로 익절합니다. — 파라미터 이름에 마우스를 올리면 설명이 나타납니다.")
    st.markdown(_htable([
        ("1단계 임계값",
         "QQQ(나스닥 100 ETF)가 과거 최고점보다 15% 이상 올랐을 때 처음 익절합니다.<br><br>"
         "<b>왜 이게 필요한가?</b> TQQQ는 QQQ의 3배로 움직이는 레버리지 상품입니다. "
         "폭락 시 손실이 매우 크기 때문에, 오르는 도중에 조금씩 팔아 이익을 미리 '확정'해 두면 나중에 급락해도 이미 챙긴 이익이 있습니다.",
         "<b>+15%</b>", "3~20%", f"+{_pct(p.rally_thresh_1)}"),
        ("2단계 임계값",
         "고점 대비 21% 이상 올랐을 때 2번째 익절합니다.<br><br>"
         "<b>왜 단계를 나누나?</b> 1차 때 전부 팔지 않는 이유는 '더 오를 수도 있기 때문'입니다. "
         "단계별로 나눠 팔면 너무 일찍 팔아버렸다는 후회를 줄일 수 있습니다.",
         "<b>+21%</b>", "5~40%", f"+{_pct(p.rally_thresh_2)}"),
        ("3단계 임계값",
         "고점 대비 25% 이상 올랐을 때 3번째 익절합니다.<br><br>"
         "<b>이 구간의 의미?</b> 나스닥이 고점 대비 25% 이상 오른 상황은 단기적으로 과열된 상태일 가능성이 높습니다. 이때 보유량을 줄여 리스크를 낮춥니다.",
         "<b>+25%</b>", "10~60%", f"+{_pct(p.rally_thresh_3)}"),
        ("1단계 매도 비율",
         "1단계 조건 달성 시 현재 보유 TQQQ의 10%를 팝니다.<br><br>"
         "<b>왜 10%만?</b> 전부 팔지 않는 이유는 아직 더 오를 가능성이 있기 때문입니다. 조금씩 나눠 팔면 평균 매도가를 높일 수 있습니다.",
         "<b>10%</b>", "5~50%", _pct(p.rally_sell_pct_1)),
        ("2단계 매도 비율",
         "2단계 조건 달성 시 그 시점 보유 TQQQ의 20%를 팝니다.<br><br>"
         "<b>왜 더 많이?</b> 단계가 높아질수록 과열 가능성이 높으므로 더 많이 팝니다.",
         "<b>20%</b>", "5~50%", _pct(p.rally_sell_pct_2)),
        ("3단계 매도 비율",
         "3단계 조건 달성 시 그 시점 보유 TQQQ의 30%를 팝니다.<br><br>"
         "<b>이 구간은?</b> 3단계까지 올랐다면 상당한 과열 구간입니다. 적극적으로 이익을 실현합니다.",
         "<b>30%</b>", "5~50%", _pct(p.rally_sell_pct_3)),
    ]), unsafe_allow_html=True)
    st.divider()

    # 2. 하락 대응
    st.subheader("🔴 하락 대응")
    st.caption("QQQ 일간 수익률이 임계값 이하이거나 MA200에 터치하면 TQQQ를 전량매도하고 방어 모드로 전환합니다.")
    _ignore_on = _on if p.use_ignore_filter else _off
    _ma200_on  = _on if p.use_ma200_sell    else _off
    st.markdown(_htable([
        ("-3% 트리거 임계값",
         "QQQ가 하루에 3% 이상 하락하면 TQQQ를 전부 팝니다.<br><br>"
         "<b>왜 -3%인가?</b> TQQQ는 QQQ의 3배로 움직입니다. QQQ가 하루 -3%이면 TQQQ는 약 -9% 손실입니다. "
         "이 정도의 하락은 '더 큰 하락의 시작'일 수 있어 빠르게 대피하는 것이 핵심입니다.",
         "<b>-3%</b>", "-10~-1%", _pct(p.selloff_thresh)),
        ("MA200 전량매도",
         "MA200(200일 이동평균선)이란? 지난 200 거래일(약 10개월)의 평균 주가입니다. "
         "QQQ 주가가 이 선 아래로 내려오면 '장기 하락장'으로 판단해 TQQQ를 전부 팝니다.<br><br>"
         "<b>왜 MA200인가?</b> 역사적으로 QQQ가 MA200 아래로 내려가면 본격적인 하락장(베어마켓)이 시작되는 경우가 많았습니다. 미리 탈출해 큰 손실을 막습니다.",
         f"<b>{_on}</b>", "ON/OFF", _ma200_on),
        ("MA200 터치 배수",
         "QQQ 주가가 MA200보다 딱 1% 위(1.010배) 지점에 도달하면 매도 신호로 인식합니다.<br><br>"
         "<b>왜 1.010인가?</b> MA200에 정확히 닿는 순간을 기다리면 이미 너무 늦을 수 있습니다. "
         "약간 여유를 두고 미리 팔면 더 좋은 가격에 매도할 수 있습니다. "
         "1.010 = '200일 평균보다 1% 위에서 미리 팔겠다'는 뜻입니다.",
         "<b>1.010</b>", "1.000~1.050", f"{p.ma200_mult:.3f}"),
        ("조건부 무시 필터",
         "재진입 직후 첫 번째 -3% 하락은 보통 '무시'합니다(신호 오류 방지). "
         "단, 이미 QQQ가 많이 하락한 상태라면 무시하지 않고 바로 팝니다.<br><br>"
         "<b>왜 필요한가?</b> 재진입 바로 다음 날 -3%가 나올 수도 있습니다. 이게 '진짜 위험 신호'인지 '일시적 조정'인지 구분하기 위해, "
         "최근 시장 흐름(누적 수익률)을 확인합니다. 이미 많이 빠진 상태라면 무시하지 않고 팝니다.",
         f"<b>{_on}</b>", "ON/OFF", _ignore_on),
        ("무시 필터 기간",
         "최근 15 거래일(약 3주) 동안의 QQQ 누적 수익률을 계산합니다.<br><br>"
         "<b>왜 15일?</b> 15거래일은 단기 추세를 파악하기에 적당한 기간입니다. "
         "너무 짧으면 하루하루 노이즈에 민감하고, 너무 길면 반응이 늦어집니다.",
         "<b>15 거래일</b>", "5~30 거래일", f"{p.ignore_filter_lookback} 거래일"),
        ("무시 필터 임계값",
         "15일 누적 수익률이 -5% 이하이면 이미 시장이 하락 중이라 판단해 무시 규칙을 적용하지 않습니다.<br><br>"
         "<b>즉,</b> 3주 동안 QQQ가 5% 이상 빠진 상황이라면, 재진입 직후 -3% 신호도 진짜 위험 신호로 보고 팝니다.",
         "<b>-5%</b>", "-15~-1%", _pct(p.ignore_filter_thresh)),
    ]), unsafe_allow_html=True)
    st.caption("첫 -3% 무시(케이스 B-3) 시 QQQ 15일 누적 ≤ -5% → 전량매도")
    st.divider()

    # 3. 재진입 대기
    st.subheader("⏳ 재진입 대기")
    st.caption("매도 후 관망 기간 동안 TQQQ 신규 매수를 보류합니다. 누적 매도 횟수에 따라 단기/장기 관망 기간가 달라집니다.")
    st.markdown(_htable([
        ("단기 관망 기간",
         "TQQQ를 판 후 24 거래일(약 5주) 동안은 다시 사지 않고 기다립니다. 1~3차 매도에 적용됩니다.<br><br>"
         "<b>왜 기다리나?</b> 하락 직후 곧바로 다시 사면 추가 하락으로 손실이 날 수 있습니다. "
         "'열기가 식을 때까지 기다리는' 쿨다운 기간입니다.",
         "<b>24 거래일</b>", "5~60 거래일", f"{p.wait_short} 거래일"),
        ("장기 관망 기간",
         "매도가 4번 이상 누적되면 장기 관망 기간(24 거래일) 동안 기다립니다.<br><br>"
         "<b>왜 더 오래?</b> 여러 번 매도가 반복된다는 건 시장이 장기 하락장일 가능성이 높다는 뜻입니다. "
         "더 오래 기다려야 안전하게 재진입할 수 있습니다.",
         "<b>24 거래일</b>", "10~120 거래일", f"{p.wait_long} 거래일"),
        ("장기 모드 전환 기준",
         "매도 횟수가 이 값(4회) 이상이 되면 장기 관망 기간로 전환합니다.<br><br>"
         "<b>왜 4회?</b> 4번 이상 반복 매도가 발생했다는 건 하락장이 꽤 심각하다는 신호입니다. "
         "이때는 더 신중하게 기다려야 합니다.",
         "<b>4회</b>", "2~8회", f"{p.heavy_sell_count}회"),
        ("MA200 관망 기간",
         "MA200 전량매도 후 최소 대기하는 거래일 수입니다. 이 기간이 지나야 추세 필터가 평가되어 재진입 여부가 결정됩니다.<br><br>"
         "<b>왜 필요한가?</b> MA200 매도는 하루 만에도 발생할 수 있는데, 매도 다음날 MA200이 아직 상승 중이라는 이유만으로 즉시 재진입이 일어나는 허점을 막습니다. "
         "-3% 매도의 '단기 관망 기간'과 같은 쿨다운 역할을 합니다.",
         "<b>10 거래일</b>", "0~45 거래일", f"{p.ma200_wait_days} 거래일"),
    ]), unsafe_allow_html=True)
    st.divider()

    # 4. 분할매수 (-3% 트리거)
    st.subheader("📊 분할매수 (-3% 트리거)")
    st.caption("4차 이상 매도 시 방어 중에도 추가 하락 시 단계적으로 TQQQ를 매수합니다.")
    col_so_a, col_so_b = st.columns(2)
    with col_so_a:
        st.markdown("**A구간** — 소폭 하락 구간")
        st.markdown(_htable([
            ("간격",
             "기준가 대비 3% 하락할 때마다 TQQQ를 조금씩 삽니다. 즉 -3%, -6%, -9%... 순서로 매수합니다.<br><br>"
             "<b>왜 나눠서 사나?</b> 하락 중에 한 번에 전부 사면 더 내려갈 때 크게 물릴 수 있습니다. "
             "나눠서 사면 평균 매입가를 낮출 수 있습니다(이른바 '물타기' 전략).",
             "<b>3%</b>", "1~10%", _pct(p.so_a_step)),
            ("매수 비율",
             "각 단계에서 현재 남은 현금의 10%만 삽니다.<br><br>"
             "<b>왜 10%?</b> 전체를 한 번에 쓰지 않고 10%씩 나눠 씀으로써, 더 내려가도 추가 매수할 여력을 남깁니다.",
             "<b>10%</b>", "5~30%", _pct(p.so_a_pct)),
            ("횟수",
             "A구간에서 최대 5번까지 나눠 삽니다. (-3%부터 -15%까지 커버)<br><br>"
             "<b>그 이상의 하락은?</b> -15%를 넘어서면 B구간에서 처리합니다.",
             "<b>5단계</b>", "1~10", f"{p.so_a_cnt}단계"),
        ], headers=["파라미터", "<b>기본값</b>", "범위", "현재"]), unsafe_allow_html=True)
    with col_so_b:
        st.markdown("**B구간** — 대폭락 구간 <span style='color:#999'>(※ 폐기된 파라미터 — 기본 OFF)</span>", unsafe_allow_html=True)
        st.markdown(_htable([
            ("시작 낙폭",
             "기준가 대비 20% 이상 하락하면 B구간 매수가 시작됩니다.<br><br>"
             "<b>왜 20%?</b> 20% 폭락은 '베어마켓 진입' 수준의 큰 하락입니다. "
             "이 정도의 폭락은 역사적으로 반등 가능성이 높아 보다 적극적으로 매수합니다.",
             "<b>20%</b>", "10~40%", _pct(p.so_b_start)),
            ("간격",
             "B구간에서는 5% 하락마다 추가 매수합니다.<br><br>"
             "<b>왜 A구간보다 간격이 넓나?</b> 이미 큰 폭락 구간이라 변동성이 크고 추가 하락 폭도 크기 때문에 간격을 넓게 잡습니다.",
             "<b>5%</b>", "1~15%", _pct(p.so_b_step)),
            ("매수 비율",
             "각 단계에서 남은 현금의 10%를 삽니다.",
             "<b>10%</b>", "5~30%", _pct(p.so_b_pct)),
            ("횟수",
             "B구간에서 최대 5번 나눠 삽니다. (-20%부터 -40%까지 5% 간격으로 커버)",
             "<b>5단계</b>", "1~10", f"{p.so_b_cnt}단계"),
        ], headers=["파라미터", "<b>기본값</b>", "범위", "현재"]), unsafe_allow_html=True)
    st.divider()

    # 5. 분할매수 (MA200 트리거)
    st.subheader("📊 분할매수 (MA200 트리거)")
    st.caption("MA200 전량매도 후 방어 중 추가 하락 시 단계적으로 TQQQ를 매수합니다.")
    col_ma_a, col_ma_b = st.columns(2)
    with col_ma_a:
        st.markdown("**A구간** — 소폭 하락 구간")
        st.markdown(_htable([
            ("간격",
             "MA200 매도 후 기준가 대비 2% 하락마다 TQQQ를 조금씩 삽니다.<br><br>"
             "<b>왜 -3% 트리거보다 간격이 촘촘한가?</b> MA200 매도는 -3% 급락보다 '덜 공격적인 신호'입니다. "
             "상대적으로 안정적인 상황이므로 더 촘촘하게 매수합니다.",
             "<b>2%</b>", "1~10%", _pct(p.ma_a_step)),
            ("매수 비율",
             "각 단계에서 남은 현금의 10%를 삽니다.",
             "<b>10%</b>", "5~30%", _pct(p.ma_a_pct)),
            ("횟수",
             "A구간에서 최대 5번 나눠 삽니다.",
             "<b>5단계</b>", "1~10", f"{p.ma_a_cnt}단계"),
        ], headers=["파라미터", "<b>기본값</b>", "범위", "현재"]), unsafe_allow_html=True)
    with col_ma_b:
        st.markdown("**B구간** — 대폭락 구간 <span style='color:#999'>(※ 폐기된 파라미터 — 기본 OFF)</span>", unsafe_allow_html=True)
        st.markdown(_htable([
            ("시작 낙폭",
             "기준가 대비 20% 이상 하락하면 MA200 B구간 매수가 시작됩니다.<br><br>"
             "<b>이 구간의 의미?</b> 이미 MA200 아래인데 20%까지 더 하락했다면, 역사적으로 반등 가능성이 매우 높은 극단적 구간입니다.",
             "<b>20%</b>", "10~40%", _pct(p.ma_b_start)),
            ("간격",
             "B구간에서는 3% 하락마다 추가 매수합니다.<br><br>"
             "<b>왜 -3% 트리거 B구간보다 촘촘한가(5%→3%)?</b> MA200 매도는 좀 더 일찍 나온 신호라 반등 가능성이 상대적으로 높습니다. 더 촘촘하게 사서 기회를 잡습니다.",
             "<b>3%</b>", "1~15%", _pct(p.ma_b_step)),
            ("매수 비율",
             "각 단계에서 남은 현금의 10%를 삽니다.",
             "<b>10%</b>", "5~30%", _pct(p.ma_b_pct)),
            ("횟수",
             "B구간에서 최대 5번 나눠 삽니다.",
             "<b>5단계</b>", "1~10", f"{p.ma_b_cnt}단계"),
        ], headers=["파라미터", "<b>기본값</b>", "범위", "현재"]), unsafe_allow_html=True)
    st.divider()

    # 6. 분할매수 TQQQ 노출 제한
    st.subheader("🛡️ 분할매수 TQQQ 노출 제한")
    st.caption("분할매수 시 TQQQ 비중이 이 값 이상이면 추가 매수를 중단합니다. A·B 구간 공통, VIX 무관.")
    st.markdown(_htable([
        ("최대 TQQQ 비중",
         "분할매수로 TQQQ를 사더라도, 전체 자산(NAV) 대비 TQQQ 비중이 40%를 넘으면 더 이상 사지 않습니다.<br><br>"
         "<b>왜 제한하나?</b> 하락장 도중에 TQQQ를 너무 많이 사면 추가 하락 시 자산이 크게 줄어들 위험이 있습니다. "
         "최대 40%로 제한해 하락장에서의 과도한 위험 노출을 막습니다. "
         "100%로 설정하면 제한 없이 매수합니다.",
         "<b>40%</b>", "10~100%", _pct(p.split_buy_max_exposure)),
    ]), unsafe_allow_html=True)
    st.divider()

    # 7. 추세 확인 재진입
    _ma5_streak_on = _on if p.trend_require_ma5_above_ma200 else _off
    _ma50_on   = _on if p.trend_require_above_ma50    else _off
    _ma200r_on = _on if p.trend_require_ma200_rising  else _off
    st.subheader("📈 추세 확인 재진입")
    st.caption("대기일 종료 후 추세 미확인이면 재진입을 연기합니다. **(항상 활성)**")
    st.markdown(_htable([
        ("MA5 > MA200 연속 N일",
         "QQQ의 5일 이동평균(MA5)이 200일 이동평균(MA200)보다 높은 상태가 N일 연속 유지돼야 재진입을 허용합니다.<br><br>"
         "<b>왜?</b> 단기 반등인지 진짜 추세 전환인지 구별하기 위해서입니다. "
         "연속 유지되면 일시적 반등이 아닌 진짜 회복이라고 볼 수 있습니다.",
         f"<b>{_on}</b>", "ON/OFF", _ma5_streak_on),
        ("MA5>MA200 연속 일수",
         "위 조건이 ON일 때 몇 일 연속 유지돼야 하는지 설정합니다.",
         "<b>3일</b>", "0~10일", f"{p.ma200_reentry_streak}일"),
        ("QQQ > MA50 요구",
         "MA50(50일 이동평균선)이란? 지난 50 거래일(약 2.5개월)의 평균 주가입니다. "
         "QQQ 주가가 이 선 위에 있어야 재진입을 허용합니다.<br><br>"
         "<b>왜?</b> QQQ가 50일 평균보다 위에 있다는 건 '단기 상승 추세'라는 뜻입니다. "
         "단기 추세가 좋을 때 재진입해야 이익을 볼 가능성이 높습니다.",
         f"<b>{_on}</b>", "ON/OFF", _ma50_on),
        ("MA200 상승 추세 확인",
         "200일 이동평균선(MA200) 자체가 상승 중이어야 재진입을 허용합니다.<br><br>"
         "<b>왜?</b> MA200이 오르고 있다는 건 '장기 상승장'이라는 뜻입니다. "
         "장기 추세가 좋을 때 재진입해야 큰 수익을 기대할 수 있습니다. "
         "MA200이 하락 중이면 아직 하락장이 끝나지 않았을 수 있습니다.",
         f"<b>{_on}</b>", "ON/OFF", _ma200r_on),
        ("MA200 기울기 기간",
         "현재 MA200과 20 거래일(약 4주) 전 MA200을 비교해 상승 여부를 판단합니다.<br><br>"
         "<b>왜 20일?</b> MA200은 서서히 변하기 때문에 20일 정도의 기간을 비교해야 추세 방향을 정확히 알 수 있습니다.",
         "<b>20 거래일</b>", "10~40 거래일", f"{p.ma200_slope_lookback} 거래일"),
        ("필터 모드",
         "<b>OR 모드(기본):</b> 체크된 조건 중 하나만 충족해도 재진입합니다. 더 빨리 재진입합니다.<br>"
         "<b>AND 모드:</b> 체크된 조건을 모두 충족해야 재진입합니다. 더 신중하게 재진입합니다.<br><br>"
         "<b>어느 게 나은가?</b> OR은 기회를 더 빨리 잡고, AND는 안전을 더 중시합니다. "
         "하락장이 자주 오는 시기에는 AND가 유리할 수 있습니다.",
         "<b>OR</b>", "OR/AND", p.trend_filter_mode),

    ]), unsafe_allow_html=True)
    st.caption("체크된 조건들을 필터 모드(OR/AND)에 따라 적용하여 재진입 판단")
    st.divider()

    # 9. 관망 구간 헷지
    _hedge_on  = _on if p.use_hedge else _off
    _cash_pct  = 1.0 - p.hedge_alloc_pct if p.use_hedge else 1.0
    _gld_alloc = p.hedge_gld_ratio * p.hedge_alloc_pct if p.use_hedge else 0.0
    _tlt_alloc = (1 - p.hedge_gld_ratio) * p.hedge_alloc_pct if p.use_hedge else 0.0
    st.subheader("🛡️ 관망 구간 헷지")
    st.caption("방어 모드(관망) 중 현금 일부를 GLD(금)와 SHY(단기국채)에 투자합니다.")
    st.markdown(_htable([
        ("헷지 활성화",
         "TQQQ를 팔고 기다리는 관망 기간 동안 현금 일부를 금(GLD)과 단기국채(SHY)에 투자합니다.<br><br>"
         "<b>왜 헷지를 하나?</b> 현금만 들고 있으면 시간이 지날수록 인플레이션으로 가치가 줄어듭니다. "
         "금과 단기국채는 자산 가치를 지키면서 소액의 이자 수익도 제공합니다.",
         f"<b>{_on}</b>", "ON/OFF", _hedge_on),
        ("현금 보유 비율",
         "전체 자산의 20%는 현금으로 유지하고, 나머지 80%를 GLD/SHY에 투자합니다.<br><br>"
         "<b>왜 현금을 남기나?</b> 분할매수 기회가 왔을 때 바로 사용할 수 있도록 일부는 현금으로 비워둡니다.",
         "<b>20%</b>", "0~100%", f"{_cash_pct*100:.0f}%"),
        ("GLD 비율",
         "헷지 자산(전체의 80%) 중 75%는 GLD(금 ETF), 나머지 25%는 SHY(미국 단기국채 ETF, 듀레이션 1~3년)에 투자합니다.<br><br>"
         "<b>GLD(금) 역할?</b> 금은 인플레이션, 달러 약세, 지정학적 위기 시 가격이 오르는 경향이 있습니다.<br>"
         "<b>SHY(단기국채) 역할?</b> 듀레이션이 짧아 금리 상승기에도 가격 하락이 거의 없고, "
         "현금에 가까운 안정성을 가지면서 소액의 이자 수익을 제공합니다.",
         "<b>75%</b>", "0~100%", f"{p.hedge_gld_ratio*100:.0f}%"),
    ]), unsafe_allow_html=True)
    st.caption(f"현금 {_cash_pct*100:.0f}% | GLD {_gld_alloc*100:.0f}% | SHY {_tlt_alloc*100:.0f}%")
    st.divider()

    st.markdown("---")

    # ── 상세 전략 로직 (AI/개발자용) ─────────────────────────
    with st.expander("📐 상세 전략 로직 (AI/개발자용)", expanded=False):
        st.markdown("#### 1. 시스템 상태 모델")
        st.code(
            """
  ┌══════════════════┐       ┌═══════════════════════┐       ┌══════════════════┐
  │   공격 모드       │       │   방어 모드 (관망 중)   │       │   초기 포지션     │
  │   (정상 보유)     │       │                       │       │   (initial_pos)  │
  │                  │       │ trigger_type=          │       │                  │
  │ trigger_type=""  │       │   "selloff" │ "ma200"  │       │ initial_pos=True │
  │ waiting=False    │       │ waiting=True           │       │ waiting=False    │
  │ tqqq_shares > 0  │       │                       │       │ tqqq_shares > 0  │
  └══════════════════┘       └═══════════════════════┘       └══════════════════┘

  방어 모드 하위 상태:
  ├─ [S1] selloff 관망, tqqq=0, count < 4   → 21일 대기
  ├─ [S2] selloff 관망, tqqq=0, count >= 4  → 42일 + 분할매수 활성
  ├─ [S3] selloff 관망, tqqq>0 (분할매수),  count < 4
  ├─ [S4] selloff 관망, tqqq>0 (분할매수),  count >= 4
  ├─ [M1] ma200 관망,  tqqq=0              → 추세 필터 통과 대기
  └─ [M2] ma200 관망,  tqqq>0 (분할매수)
  ※ S3/S4/M2: 분할매수 포지션 있어도 재진입 가능 (데드락 수정 완료)
""", language=None)

        st.markdown("#### 2. 매일 이벤트 처리 플로우 (우선순위)")
        st.code(
            """
  ┌──────────────────────────────────────────┐
  │           DAY i 시작                      │
  │  MA5/MA200 streak 갱신                    │
  │  VIX streak 갱신                          │
  │  running_max_qqq 갱신 (tqqq>0일 때)       │
  └────────────────┬───────────────────────────┘
                   │
                   ▼
          ┌────────────────┐
          │ QQQ 일간수익률  │
          │   <= -3% ?     │
          └───┬────────┬───┘
           YES│        │NO
              ▼        ▼
  ┌────────────┐  ┌─────────────────┐
  │ ① -3% 처리 │  │ ② MA200 터치?    │
  │ (6가지     │  │  (rally>0 AND    │
  │  케이스)    │  │   q<=MA200*1.01) │
  └─────┬──────┘  └───┬─────────┬───┘
        │          YES│         │NO
        │             ▼         ▼
        │     ┌───────────┐ ┌──────────────┐
        │     │ 전량매도    │ │ ③ 재진입 체크 │
        │     │→방어모드   │ │ (waiting AND  │
        │     └───────────┘ │  타이머 만료)  │
        │                   └───┬──────────┘
        │                       │
        ▼───────────────────────▼
  ┌──────────────────────────────────────────┐
  │ ④ 분할매수 (event_handled와 무관, 항상!)   │
  │   waiting=True AND split_buy_base>0이면   │
  │   각 레벨별 10% 매수 (잔여자산 매번 재계산) │
  └────────────────┬───────────────────────────┘
                   │
                   ▼
  ┌──────────────────────────────────────────┐
  │ ⑤ 랠리 익절                               │
  │   trigger_type="" AND tqqq>0일 때만       │
  │   +9%→10%, +21%→20%, +28%→30% 매도       │
  └────────────────┬───────────────────────────┘
                   │
                   ▼
            [DAY i 종료]
""", language=None)

        st.markdown("#### 3. -3% 이벤트 분기 (6가지 케이스)")
        st.code(
            """
  ┌═══════════════════════════════════════════┐
  │          -3% 이벤트 발동 (ret <= -0.03)    │
  └═════════════════┬═════════════════════════┘
                    │
            ┌───────┴───────┐
            │ initial_pos?  │
            └──┬─────────┬──┘
            YES│         │NO
               ▼         ▼
  ┌─────────────┐  ┌──────────────┐
  │ 케이스 A     │  │ tqqq > 0 ?   │
  │ 전량매도     │  └──┬────────┬──┘
  │ →[S1]       │  YES│        │NO
  └─────────────┘     ▼        ▼
              ┌────────────┐ ┌────────────┐
              │trigger_type│ │trigger_type│
              └─┬────┬──┬─┘ └──┬──────┬──┘
             "" │ma200│so│  "so"│  "ma200"│
                ▼    ▼   ▼     ▼        ▼
             [B분기] [C] [D]  [E]      [F]

  ────────────────────────────────────────────
  케이스 A: 초기포지션 첫 -3% → 전량매도 → [S1]
  ────────────────────────────────────────────
  케이스 B (trigger_type="", 정상보유):
    B-1: rally_level >= 1       → 전량매도
    B-2: sell_off_count >= 1    → 전량매도
    B-3: 둘 다 0 (재진입 직후)  → 무시! (카운트만 +1)
  ────────────────────────────────────────────
  케이스 C: ma200관망 + tqqq>0  → 전량매도!
            trigger_type "ma200"→"selloff" 전환
  ────────────────────────────────────────────
  케이스 D: selloff관망 + tqqq>0 → 매도 안 함
            카운트 +1, 타이머 리셋
  ────────────────────────────────────────────
  케이스 E: selloff관망 + tqqq=0 → 케이스 D와 동일
  ────────────────────────────────────────────
  케이스 F: ma200관망 + tqqq=0  → timer_42 갱신만
  ────────────────────────────────────────────

  매도 후 공통처리:
  - count 1~3차: 21일 관망, split_buy_base=0 (분할매수 비활성)
  - count 4차+:  42일 관망, split_buy_base=현재가 (분할매수 활성)
  - 헷지 매수 (현금→GLD/SHY)
""", language=None)

        st.markdown("#### 4. 전체 상태 전이 다이어그램")
        st.code(
            """
  [초기포지션] ──(-3%: 케이스A)──▶ [S1: selloff, tqqq=0, cnt<4]
                                        │              │
                                  21일 경과        -3% 재발 (E)
                                  재진입 ✓         cnt 증가
                                        │              │
                                        ▼         cnt>=4 되면
                                  [공격모드] ◀──── split_buy_base=q
                                    │    ▲              │
                              -3%(B)│    │         [S2: tqqq=0, cnt>=4]
                              MA200 │    │              │
                                    │    │         분할매수 체결
                                    │    │              │
                                    │    │         [S4: tqqq>0, cnt>=4]
                                    │    │              │
                                    │    │    42일 경과 → 재진입 ✓
                                    │    │    (분할매수분 유지 + 잔여현금 추가매수)
                                    │    │              │
                                    │    └──────────────┘
                                    │
                              랠리 익절 후
                              MA200 터치
                                    │
                                    ▼
                              [M1: ma200, tqqq=0]
                                    │              │
                              MA5>MA200       분할매수 체결
                              5일 연속              │
                              재진입 ✓         [M2: tqqq>0]
                                    │              │
                                    ▼         -3%(C) → 전량매도 → [S1/S2]
                              [공격모드]        또는
                                          MA5>MA200 5일 → 재진입 ✓
                                          (분할매수분 유지 + 잔여현금 추가매수)

  ※ 재진입 = 헷지 전량매도 + (기존 분할매수 유지) + 남은 현금 전액 TQQQ 매수
             sell_off_count=0, waiting=False, trigger_type="" 초기화
""", language=None)

        st.markdown("---")
        st.markdown("### 📄 전략 명세 전문 (strategy.md)")
        if _strategy_text:
            st.markdown(_strategy_text)
        else:
            st.warning("strategy.md 파일을 찾을 수 없습니다.")

