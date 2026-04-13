#!/usr/bin/env python
"""P0 Smoke Test -- KIS 모의투자 첫 연결

Step 1: ka.auth() 인증
Step 2: Raw JSON 출력 (필드 매핑 검증)
Step 3: KISBrokerageProvider 통해 get_balance(), get_positions()
"""

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import kis_auth as ka


def main() -> int:
    print("=" * 60)
    print("  P0 Smoke Test -- KIS 모의투자 첫 연결")
    print("=" * 60)

    # ── Step 1: 인증 ────────────────────────────────────────
    print("\n[Step 1] KIS 인증...")
    try:
        ka.auth(svr="vps")
        tr_env = ka.getTREnv()
        print(f"  OK  인증 성공")
        print(f"  URL: {tr_env.my_url}")
        print(f"  계좌: {tr_env.my_acct[:4]}****")
        hts_status = "비어있음 (P1 WebSocket 차단)" if not tr_env.my_htsid else tr_env.my_htsid
        print(f"  HTS ID: {hts_status}")
    except Exception as e:
        print(f"  FAIL  인증 실패: {e}")
        return 1

    # ── Step 2: Raw JSON 출력 ───────────────────────────────
    print("\n[Step 2] 잔고 조회 Raw JSON...")
    raw_output2 = None
    try:
        params = {
            "CANO": tr_env.my_acct,
            "ACNT_PRDT_CD": "01",
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        resp = ka._url_fetch(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            "VTTC8434R",
            "",
            params,
            postFlag=False,
        )

        if resp.isOK():
            body = resp.getBody()

            # output1 (개별 종목)
            print("\n  --- output1 (보유 종목) ---")
            if hasattr(body, "output1") and body.output1:
                for i, item in enumerate(body.output1):
                    print(f"  [{i}] {json.dumps(item, ensure_ascii=False, indent=4)}")
            else:
                print("  (비어있음)")

            # output2 (계좌 요약) -- 필드 매핑의 핵심
            print("\n  --- output2 (계좌 요약) ---")
            if hasattr(body, "output2") and body.output2:
                data2 = body.output2
                if isinstance(data2, list) and len(data2) > 0:
                    raw_output2 = data2[0]
                elif isinstance(data2, dict):
                    raw_output2 = data2

                if raw_output2:
                    print(f"  {json.dumps(raw_output2, ensure_ascii=False, indent=4)}")
            else:
                print("  (비어있음)")

            print(f"\n  OK  잔고 조회 성공 (rt_cd: {body.rt_cd})")
        else:
            print(f"  FAIL  잔고 조회 실패: {resp.getErrorCode()} - {resp.getErrorMessage()}")
            return 1
    except Exception as e:
        print(f"  FAIL  Raw API 호출 실패: {e}")
        import traceback
        traceback.print_exc()
        return 1

    # ── Step 3: KISBrokerageProvider 검증 ───────────────────
    print("\n[Step 3] KISBrokerageProvider 검증...")
    try:
        from kis_backtest.providers.kis.auth import KISAuth
        from kis_backtest.providers.kis.brokerage import KISBrokerageProvider

        auth = KISAuth.from_env(mode="paper")
        bro = KISBrokerageProvider.from_auth(auth)

        balance = bro.get_balance()
        print(f"  총 예수금:     {balance.total_cash:>15,.0f}원")
        print(f"  주문가능금액:  {balance.available_cash:>15,.0f}원")
        print(f"  총 평가금액:   {balance.total_equity:>15,.0f}원")
        print(f"  평가손익:      {balance.total_pnl:>15,.0f}원")
        print(f"  손익률:        {balance.total_pnl_percent:>14.2f}%")

        positions = bro.get_positions()
        if positions:
            print(f"\n  보유 종목: {len(positions)}건")
            for p in positions:
                print(
                    f"    {p.name}({p.symbol}): {p.quantity}주 "
                    f"@{p.current_price:,.0f} | "
                    f"P&L: {p.unrealized_pnl:,.0f}원 ({p.unrealized_pnl_percent:.2f}%)"
                )
        else:
            print("\n  보유 종목: 없음")

        print("\n  OK  Provider 검증 완료")
    except Exception as e:
        print(f"  FAIL  Provider 실패: {e}")
        import traceback
        traceback.print_exc()
        return 1

    # ── 필드 매핑 비교 ──────────────────────────────────────
    print("\n" + "=" * 60)
    print("  필드 매핑 검증 결과")
    print("=" * 60)

    if raw_output2:
        # 현재 매핑
        print("\n  [현재 매핑]")
        print(f"  dnca_tot_amt       -> total_cash:     {raw_output2.get('dnca_tot_amt', 'N/A')}")
        print(f"  nass_amt           -> available_cash:  {raw_output2.get('nass_amt', 'N/A')}")
        print(f"  tot_evlu_amt       -> total_equity:    {raw_output2.get('tot_evlu_amt', 'N/A')}")
        print(f"  evlu_pfls_smtl_amt -> total_pnl:       {raw_output2.get('evlu_pfls_smtl_amt', 'N/A')}")
        print(f"  evlu_pfls_rt       -> total_pnl_%:     {raw_output2.get('evlu_pfls_rt', 'N/A')}")

        # available_cash 후보 필드 찾기
        cash_keywords = ("amt", "cash", "ord", "rcdl", "excc", "prvs", "nass")
        print("\n  [available_cash 후보 필드]")
        for key in sorted(raw_output2.keys()):
            if any(k in key for k in cash_keywords):
                val = raw_output2[key]
                if val and val != "0":
                    print(f"    {key:30s} = {val}")

        # 전체 필드 목록 (디버그용)
        print(f"\n  [output2 전체 필드: {len(raw_output2)}개]")
        for key in sorted(raw_output2.keys()):
            val = raw_output2[key]
            marker = " <--" if key in ("dnca_tot_amt", "nass_amt", "tot_evlu_amt") else ""
            print(f"    {key:30s} = {val}{marker}")

    print("\n" + "=" * 60)
    print("  P0 Smoke Test 완료!")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
