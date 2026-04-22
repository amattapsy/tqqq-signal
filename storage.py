"""
storage.py — 백테스트 결과 저장 / 불러오기 / 비교
saves/ 폴더에 JSON 파일로 저장
"""

import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

SAVES_DIR = Path(__file__).parent / "saves"


def _ensure_dir():
    SAVES_DIR.mkdir(exist_ok=True)


def save_result(name: str, params: dict, metrics: dict, period: dict) -> str:
    """
    백테스트 결과 저장.
    Returns: 저장된 파일명
    """
    _ensure_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"{timestamp}_{name.replace(' ', '_')}.json"
    filepath  = SAVES_DIR / filename

    # metrics에서 직렬화 불가 타입 제거
    clean_metrics = {}
    for k, v in metrics.items():
        if isinstance(v, pd.Series):
            clean_metrics[k] = {str(idx): float(val) for idx, val in v.items() if pd.notna(val)}
        elif isinstance(v, (int, float)) and pd.notna(v):
            clean_metrics[k] = float(v)
        elif isinstance(v, str):
            clean_metrics[k] = v
        elif v is None:
            clean_metrics[k] = None

    data = {
        "name":      name,
        "timestamp": timestamp,
        "period":    period,
        "params":    params,
        "metrics":   clean_metrics,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return filename


def load_all_saves() -> list[dict]:
    """저장된 모든 결과 불러오기 (최신순)"""
    _ensure_dir()
    results = []
    for fpath in sorted(SAVES_DIR.glob("*.json"), reverse=True):
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
                data["_filename"] = fpath.name
                results.append(data)
        except Exception:
            pass
    return results


def delete_save(filename: str):
    """저장 파일 삭제"""
    fpath = SAVES_DIR / filename
    if fpath.exists():
        fpath.unlink()


def get_comparison_df(saves: list[dict]) -> pd.DataFrame:
    """저장된 결과들을 비교 테이블로 반환"""
    rows = []
    for s in saves:
        m = s.get("metrics", {})
        p = s.get("period", {})
        rows.append({
            "이름":        s.get("name", ""),
            "저장시각":    s.get("timestamp", ""),
            "기간":        f"{p.get('start','')} ~ {p.get('end','')}",
            "최종잔고($)": m.get("최종 잔고"),
            "총수익률":    f"{m.get('총 수익률', 0)*100:.1f}%" if m.get("총 수익률") is not None else "",
            "CAGR":        f"{m.get('CAGR', 0)*100:.1f}%" if m.get("CAGR") is not None else "",
            "MDD":         f"{m.get('MDD', 0)*100:.1f}%" if m.get("MDD") is not None else "",
            "Sharpe":      f"{m.get('Sharpe Ratio', 0):.2f}" if m.get("Sharpe Ratio") is not None else "",
            "파일명":      s.get("_filename", ""),
        })
    return pd.DataFrame(rows)
