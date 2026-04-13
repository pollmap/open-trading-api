"""assemble.py — HTML 조립 단위 테스트."""
from __future__ import annotations

from kis_backtest.luxon.intelligence.assemble import assemble


def _sample_narratives() -> dict[str, str]:
    return {
        "bluf": "<p><strong>BUY.</strong> 목표주가 800,000원. 손절가 420,000원.</p>",
        "thesis": "<p>틀리면 무효화. Catalyst: 2026-05-15 실적, 2026-Q3 발주, 2026-Q4 가이던스.</p>",
        "business": "<p>조선 1위.</p>",
        "numbers": "<p>Bear Case 하방 350,000원. Base 750,000원. Bull 1,000,000원.</p>",
        "risks": "<h4>Kill Conditions</h4><ul><li>A</li><li>B</li><li>C</li></ul>",
        "trade": "<p>position_size_pct 7.0%. Risk/Reward 3.5배. backtest_engine: open-trading-api/QuantPipeline.</p>",
        "appendix": "<p>DART, KRX, Nexus MCP.</p>",
    }


class TestAssemble:
    def test_includes_all_7_sections(self):
        html = assemble(
            _sample_narratives(),
            meta={"company_name": "TEST", "ticker": "000000"},
        )
        for key in ("BLUF", "Thesis", "Business", "Numbers", "Risks", "Trade", "Appendix"):
            assert key in html

    def test_includes_trade_ticket_yaml_block(self):
        html = assemble(
            _sample_narratives(),
            meta={"company_name": "T", "ticker": "0"},
            ticket_yaml="opinion: BUY\nticker: 329180",
        )
        assert "ticket-yaml" in html
        assert "opinion: BUY" in html

    def test_missing_section_falls_back_to_placeholder(self):
        partial = {"bluf": "<p>only bluf</p>"}
        html = assemble(partial, meta={"company_name": "T", "ticker": "0"})
        assert "미생성" in html

    def test_html_escapes_ticket_angle_brackets(self):
        html = assemble(
            _sample_narratives(),
            meta={"company_name": "T", "ticker": "0"},
            ticket_yaml="html: <script>evil</script>",
        )
        assert "<script>evil" not in html
        assert "&lt;script&gt;" in html

    def test_evaluator_keywords_present_after_assembly(self):
        """조립된 HTML이 Evaluator v3 키워드를 보존하는지."""
        html = assemble(
            _sample_narratives(),
            meta={"company_name": "T", "ticker": "0"},
            ticket_yaml="backtest_engine: open-trading-api/QuantPipeline",
        )
        # 12 조건 중 regex로 잡힐 키워드 확인
        assert "BUY" in html
        assert "목표주가" in html
        assert "손절가" in html
        assert "position_size_pct" in html
        assert "Bear Case" in html
        assert "Kill Condition" in html
        assert "무효화" in html
        assert "Risk/Reward" in html
        assert "DART" in html
        assert "QuantPipeline" in html
