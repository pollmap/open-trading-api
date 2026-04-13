"""CUFA 로컬 빌드용 샘플 config 템플릿 모음.

사용:
    python -m kis_backtest.luxon.intelligence cufa \\
        --config=./samples/hhi_config.py --out=./output/hhi.html

새 종목 추가 시:
    1. hhi_config.py 복사 후 편집
    2. META/PRICE/THESIS/VALUATION_SCENARIOS/trade_ticket 값 교체
    3. 검증: python -c "import samples.내종목 as c; print(c.META)"
"""
