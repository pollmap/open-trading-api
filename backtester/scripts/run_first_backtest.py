#!/usr/bin/env python3
"""Phase 4 검증 스크립트 — 첫 번째 실전 MCP 연동 백테스트

실행: uv run python scripts/run_first_backtest.py
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
from kis_backtest.core.pipeline import QuantPipeline, PipelineConfig


def main():
    print("=" * 60)
    print("  Luxon Quant — 첫 MCP 연동 백테스트")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. MCP 프로바이더 초기화
    token = os.environ.get("MCP_VPS_TOKEN", "")
    provider = MCPDataProvider(vps_token=token)

    # 2. VPS health check
    print("\n[1/5] VPS MCP health check...")
    health = provider.health_check_sync()
    print(f"  상태: {health.get('status', 'unknown')}")
    if health.get("status") != "ok":
        print("  ⚠ VPS 연결 실패 — fallback 모드로 진행")

    # 3. 기준금리 조회
    print("\n[2/5] ECOS 기준금리 조회...")
    rf = provider.get_risk_free_rate_sync()
    print(f"  한국은행 기준금리: {rf*100:.2f}%")

    # 4. 파이프라인 실행
    print("\n[3/5] QuantPipeline 실행 (MCP 기준금리 적용)...")
    pipeline = QuantPipeline(mcp_provider=provider)
    print(f"  적용된 rf: {pipeline.config.risk_free_rate*100:.2f}%")

    result = pipeline.run(
        factor_scores={
            "005930": {"name": "삼성전자", "score": 0.82, "sector": "IT"},
            "000660": {"name": "SK하이닉스", "score": 0.75, "sector": "IT"},
            "035420": {"name": "NAVER", "score": 0.68, "sector": "IT"},
        },
        optimal_weights={"005930": 0.20, "000660": 0.15, "035420": 0.10},
        backtest_sharpe=0.85,
        backtest_max_dd=-0.12,
    )

    print(f"  리스크 판정: {'PASS' if result.risk_passed else 'FAIL'}")
    print(f"  Kelly 할당: {result.kelly_allocation:.4f}")
    print(f"  연간 비용: {result.estimated_annual_cost*100:.2f}%")
    print(f"  터뷸런스: {result.turb_index:.2f}")
    if result.order:
        print(f"\n  포트폴리오 오더:")
        print(f"  {result.order.summary()}")

    # 5. 벤치마크 데이터 조회
    print("\n[4/5] KODEX200 벤치마크 수익률 조회...")
    bench = provider.get_benchmark_returns_sync(period="3m")
    print(f"  수신: {len(bench)}일")
    if bench:
        avg_daily = sum(bench) / len(bench)
        print(f"  일평균 수익률: {avg_daily*100:.4f}%")

    # 6. 결과 저장
    print("\n[5/5] 결과 저장...")
    output_dir = Path(__file__).parent.parent / "results"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / f"first_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    result_data = {
        "timestamp": datetime.now().isoformat(),
        "risk_free_rate": pipeline.config.risk_free_rate,
        "risk_passed": result.risk_passed,
        "kelly_allocation": result.kelly_allocation,
        "annual_cost": result.estimated_annual_cost,
        "turb_index": result.turb_index,
        "risk_details": result.risk_details,
        "benchmark_days": len(bench),
        "health": health,
    }

    output_file.write_text(
        json.dumps(result_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  저장 완료: {output_file}")

    print("\n" + "=" * 60)
    print("  Phase 4 검증 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
