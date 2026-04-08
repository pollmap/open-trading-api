#!/usr/bin/env python3
"""피드백 순환 루프 — 복기 → 분석 → 전략 조정 → 기록

전체 순환:
    1. Vault에서 최근 복기 로드
    2. 성과 분석 (Sharpe 추이, DD 추이, 비용 분석)
    3. 전략 조정 권고 생성
    4. 블로그 발행 (publish_review.py 호출)
    5. Vault에 피드백 기록 저장
    6. Capital Ladder 상태 업데이트

Usage:
    python scripts/feedback_loop.py              # 전체 루프 실행
    python scripts/feedback_loop.py --analyze    # 분석만
    python scripts/feedback_loop.py --publish    # 발행 포함
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("feedback_loop")

VAULT_ROOT = Path.home() / "obsidian-vault"
VAULT_TRADING = VAULT_ROOT / "02-Areas" / "trading-ops"
FEEDBACK_DIR = VAULT_TRADING / "feedback"


def load_equity_curve() -> List[Dict[str, Any]]:
    """자산 곡선 CSV 로드"""
    csv_path = VAULT_TRADING / "equity_curve.csv"
    if not csv_path.exists():
        return []
    import csv
    with open(csv_path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_recent_snapshots(n: int = 20) -> List[Dict[str, str]]:
    """최근 N일 스냅샷 로드"""
    daily_dir = VAULT_TRADING / "daily"
    if not daily_dir.exists():
        return []
    files = sorted(daily_dir.glob("*.md"), reverse=True)[:n]
    return [{"file": str(f), "date": f.stem} for f in files]


def analyze_performance(equity_data: List[Dict]) -> Dict[str, Any]:
    """성과 분석"""
    if len(equity_data) < 5:
        return {"status": "insufficient_data", "days": len(equity_data)}

    equities = [float(e["equity"]) for e in equity_data]
    initial = equities[0]
    current = equities[-1]

    # 수익률
    total_return = (current - initial) / initial if initial > 0 else 0
    daily_returns = [
        (equities[i] - equities[i-1]) / equities[i-1]
        for i in range(1, len(equities))
        if equities[i-1] > 0
    ]

    # Sharpe
    import math
    if daily_returns:
        mean_r = sum(daily_returns) / len(daily_returns)
        var_r = sum((r - mean_r)**2 for r in daily_returns) / max(len(daily_returns)-1, 1)
        std_r = math.sqrt(var_r) if var_r > 0 else 1e-10
        sharpe = (mean_r / std_r) * math.sqrt(252)
    else:
        sharpe = 0.0

    # Max DD
    peak = equities[0]
    max_dd = 0.0
    for eq in equities:
        peak = max(peak, eq)
        dd = (eq - peak) / peak if peak > 0 else 0
        max_dd = min(max_dd, dd)

    return {
        "status": "ok",
        "days": len(equity_data),
        "total_return": round(total_return, 4),
        "sharpe": round(sharpe, 3),
        "max_dd": round(max_dd, 4),
        "current_equity": current,
        "initial_equity": initial,
    }


def generate_recommendations(analysis: Dict) -> List[str]:
    """전략 조정 권고 생성"""
    recs = []

    if analysis.get("status") == "insufficient_data":
        recs.append("데이터 부족 — 최소 5일 이상 운용 후 분석 가능")
        return recs

    sharpe = analysis.get("sharpe", 0)
    max_dd = analysis.get("max_dd", 0)
    total_return = analysis.get("total_return", 0)

    # Sharpe 기반 권고
    if sharpe < 0:
        recs.append(f"Sharpe {sharpe:.3f} 음수 — 전략 교체 또는 현금 비중 확대 권고")
    elif sharpe < 0.3:
        recs.append(f"Sharpe {sharpe:.3f} 낮음 — 팩터 재검토 또는 리밸런싱 주기 조정")
    elif sharpe > 1.5:
        recs.append(f"Sharpe {sharpe:.3f} 매우 높음 — 과최적화 가능성 Walk-Forward 검증 필수")

    # DD 기반 권고
    if max_dd < -0.10:
        recs.append(f"MDD {max_dd:.1%} 위험 — Capital Ladder 강등 검토")
    elif max_dd < -0.05:
        recs.append(f"MDD {max_dd:.1%} 경고 — 변동성 타겟 축소 고려")

    if not recs:
        recs.append("현재 지표 정상 범위 — 유지")

    return recs


def save_feedback(analysis: Dict, recommendations: List[str]) -> Path:
    """피드백을 Vault에 저장"""
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    content = [
        "---",
        f"date: {today}",
        "type: feedback-loop",
        f"sharpe: {analysis.get('sharpe', 0)}",
        f"max_dd: {analysis.get('max_dd', 0)}",
        "tags: [trading, feedback, auto]",
        "---",
        "",
        f"# 피드백 루프 {today}",
        "",
        "## 성과 분석",
        "",
        f"- 운용일수: {analysis.get('days', 0)}일",
        f"- 총 수익률: {analysis.get('total_return', 0):.2%}",
        f"- Sharpe: {analysis.get('sharpe', 0):.3f}",
        f"- Max DD: {analysis.get('max_dd', 0):.1%}",
        f"- 현재 자산: {analysis.get('current_equity', 0):,.0f}원",
        "",
        "## 전략 조정 권고",
        "",
    ]
    for rec in recommendations:
        content.append(f"- {rec}")

    content.extend(["", f"---", f"*자동 생성: {datetime.now().isoformat()}*", ""])

    filepath = FEEDBACK_DIR / f"{today}-feedback.md"
    filepath.write_text("\n".join(content), encoding="utf-8")
    logger.info("피드백 저장: %s", filepath)
    return filepath


def main() -> int:
    parser = argparse.ArgumentParser(description="피드백 순환 루프")
    parser.add_argument("--analyze", action="store_true", help="분석만 실행")
    parser.add_argument("--publish", action="store_true", help="블로그 발행 포함")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("=" * 60)
    print("  Luxon Quant — 피드백 순환 루프")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 자산 곡선 로드
    print("\n[1/4] 자산 곡선 로드...")
    equity_data = load_equity_curve()
    print(f"  {len(equity_data)}일 데이터")

    # 2. 성과 분석
    print("\n[2/4] 성과 분석...")
    analysis = analyze_performance(equity_data)
    if analysis["status"] == "ok":
        print(f"  Sharpe: {analysis['sharpe']}")
        print(f"  Max DD: {analysis['max_dd']:.1%}")
        print(f"  총 수익률: {analysis['total_return']:.2%}")
    else:
        print(f"  상태: {analysis['status']}")

    # 3. 권고 생성
    print("\n[3/4] 전략 조정 권고...")
    recommendations = generate_recommendations(analysis)
    for rec in recommendations:
        print(f"  - {rec}")

    # 4. Vault 저장
    print("\n[4/4] 피드백 저장...")
    filepath = save_feedback(analysis, recommendations)
    print(f"  저장: {filepath}")

    # 선택: 블로그 발행
    if args.publish:
        print("\n[발행] 블로그 게시...")
        import subprocess
        subprocess.run([
            sys.executable, str(_ROOT / "scripts" / "publish_review.py"),
            "--type", "weekly",
        ])

    print(f"\n{'='*60}")
    print("  피드백 루프 완료!")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
