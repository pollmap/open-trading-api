"""CUFA narrative 생성기 단위 테스트 — 엔드포인트 모킹."""
from __future__ import annotations

import httpx
import pytest

from kis_backtest.luxon.intelligence import Tier
from kis_backtest.luxon.intelligence.tasks import cufa_narrative
from kis_backtest.luxon.intelligence.tasks.cufa_narrative import (
    SECTION_SPECS,
    generate_all,
    generate_section,
)
from kis_backtest.luxon.intelligence.tests.fixtures.sample_config import (
    build_sample_config,
)


# ── 모킹 유틸 ────────────────────────────────────────────────────


def _mock_response(content: str):
    """OpenAI 호환 응답 (FLM, KoboldCpp)."""
    class _R:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": content}}]}

        def raise_for_status(self):
            pass

    return _R()


def _mock_response_ollama(content: str):
    """Ollama native /api/chat 응답."""
    class _R:
        status_code = 200

        def json(self):
            return {"message": {"role": "assistant", "content": content}, "done": True}

        def raise_for_status(self):
            pass

    return _R()


def _smart_mock(content: str):
    """URL에 따라 적절한 포맷 응답."""
    def _build(url: str):
        if "api/chat" in url:
            return _mock_response_ollama(content)
        return _mock_response(content)
    return _build


def _install_section_mock(monkeypatch, responses: dict[str, str] | None = None):
    """섹션별 응답을 매핑. 기본은 모든 섹션에 ALL-PASS fixture 반환."""
    default_map = {
        "cufa_bluf": (
            "<p><strong>BUY.</strong> HD현대중공업 목표주가 800,000원 제시.</p>"
            "<p>LNG 슈퍼사이클, 단가 상승, 해양 흑자 전환 3축.</p>"
            "<p>손절가 420,000원 엄격 준수.</p>"
        ),
        "cufa_thesis": (
            "<h4>논지 1. LNG 슈퍼사이클</h4>"
            "<p>근거 서술. 이 논지가 <strong>틀리면 무효화</strong>.</p>"
            "<h4>Catalyst Timeline</h4>"
            "<ul>"
            "<li>2026-05-15 - 1분기 실적 (기대 영향: +5%)</li>"
            "<li>2026-Q3 - 카타르 LNG (기대 영향: +12%)</li>"
            "<li>2026-11-30 - 가이던스 상향 (기대 영향: +8%)</li>"
            "</ul>"
        ),
        "cufa_business": "<h4>사업 개요</h4><p>조선 세계 1위.</p>",
        "cufa_numbers": (
            "<h4>시나리오</h4>"
            "<p><strong>Bear Case 하방 350,000원</strong>(25%). "
            "Base 750,000원(50%). Bull 1,000,000원(25%).</p>"
        ),
        "cufa_risks": (
            "<h4>Kill Conditions</h4>"
            "<ul>"
            "<li>2026 상반기 수주 50척 미만 → 논리 무효화</li>"
            "<li>후판가 30%↑ + 전가율 50% 미만 → 논리 무효화</li>"
            "<li>해양 영업손실 재발 → 논리 무효화</li>"
            "</ul>"
        ),
        "cufa_trade": (
            "<p>Risk/Reward 3.5배, position_size_pct 7.0%.</p>"
            "<p><code>backtest_engine: open-trading-api/QuantPipeline</code></p>"
        ),
        "cufa_appendix": (
            "<h4>데이터 출처</h4><p>DART, KRX, Nexus MCP 기준.</p>"
        ),
    }
    mapping = {**default_map, **(responses or {})}

    # user 메시지의 고유 라벨로 섹션 식별 (템플릿마다 다른 필드 존재)
    user_markers = {
        "cufa_bluf": "의견:",
        "cufa_thesis": "투자 논지 3축:",
        "cufa_business": "사업 세그먼트:",
        "cufa_numbers": "Bear/Base/Bull",
        "cufa_risks": "주요 리스크 요인:",
        "cufa_trade": "Position Size:",
        "cufa_appendix": "사용 데이터 출처:",
    }

    def fake_post(self, url, json=None, **kwargs):
        user_msg = json["messages"][1]["content"]
        for prompt_key, marker in user_markers.items():
            if marker in user_msg:
                content = mapping[prompt_key]
                if "api/chat" in url:
                    return _mock_response_ollama(content)
                return _mock_response(content)
        if "api/chat" in url:
            return _mock_response_ollama("<p>fallback</p>")
        return _mock_response("<p>fallback</p>")

    monkeypatch.setattr(httpx.Client, "post", fake_post)


# ── 스펙 검증 ────────────────────────────────────────────────────


class TestSectionSpecs:
    def test_all_7_sections_defined(self):
        keys = {s.key for s in SECTION_SPECS}
        assert keys == {
            "bluf", "thesis", "business", "numbers",
            "risks", "trade", "appendix",
        }

    def test_hybrid_tier_routing_per_section(self):
        """Sprint E 바벨 하이브리드: 섹션별 티어 구분."""
        tier_map = {s.key: s.tier for s in SECTION_SPECS}
        # DEFAULT (템플릿성, 짧음)
        assert tier_map["bluf"] == Tier.DEFAULT
        assert tier_map["trade"] == Tier.DEFAULT
        assert tier_map["appendix"] == Tier.DEFAULT
        # HEAVY (정밀 요구)
        assert tier_map["thesis"] == Tier.HEAVY
        assert tier_map["numbers"] == Tier.HEAVY
        assert tier_map["risks"] == Tier.HEAVY
        # LONG (긴 컨텍스트)
        assert tier_map["business"] == Tier.LONG

    def test_business_has_larger_max_tokens(self):
        """LONG 티어로 이동한 business는 max_tokens 확장."""
        bs = next(s for s in SECTION_SPECS if s.key == "business")
        assert bs.max_tokens >= 1500


# ── 단일 섹션 ─────────────────────────────────────────────────────


class TestGenerateSection:
    def test_bluf_contains_opinion_keyword(self, monkeypatch):
        _install_section_mock(monkeypatch)
        html = generate_section("bluf", build_sample_config())
        assert "BUY" in html
        assert "목표주가" in html
        assert "손절가" in html

    def test_thesis_contains_falsifiable_and_3_catalysts(self, monkeypatch):
        _install_section_mock(monkeypatch)
        html = generate_section("thesis", build_sample_config())
        assert "틀리면 무효화" in html or "Kill Condition" in html
        assert html.count("<li>") >= 3

    def test_risks_contains_kill_conditions_3plus(self, monkeypatch):
        _install_section_mock(monkeypatch)
        html = generate_section("risks", build_sample_config())
        assert "Kill Condition" in html
        assert html.count("<li>") >= 3

    def test_numbers_contains_bear_floor(self, monkeypatch):
        _install_section_mock(monkeypatch)
        html = generate_section("numbers", build_sample_config())
        assert "Bear Case" in html or "하방" in html

    def test_trade_contains_position_and_backtest(self, monkeypatch):
        _install_section_mock(monkeypatch)
        html = generate_section("trade", build_sample_config())
        assert "position_size_pct" in html
        assert "Risk/Reward" in html or "R/R" in html
        assert "QuantPipeline" in html or "backtest_engine" in html

    def test_appendix_contains_data_source(self, monkeypatch):
        _install_section_mock(monkeypatch)
        html = generate_section("appendix", build_sample_config())
        assert any(src in html for src in ("DART", "KRX", "Nexus MCP"))

    def test_unknown_section_raises(self):
        with pytest.raises(ValueError, match="Unknown section"):
            generate_section("nonexistent", {})


# ── generate_all ────────────────────────────────────────────────


class TestGenerateAll:
    def test_produces_all_7_sections(self, monkeypatch):
        _install_section_mock(monkeypatch)
        result = generate_all(build_sample_config())
        assert result.complete
        assert len(result.sections) == 7

    def test_skip_on_error_collects_errors(self, monkeypatch):
        def fake_post(self, url, json=None, **kwargs):
            raise httpx.ConnectError("down", request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        result = generate_all(build_sample_config(), skip_on_error=True)
        assert not result.complete
        assert len(result.errors) == 7

    def test_hybrid_routing_uses_three_models(self, monkeypatch):
        """Sprint E 바벨 하이브리드: 3개 모델(14b/26b/e4b) 전부 사용."""
        captured_models = []

        def fake_post(self, url, json=None, **kwargs):
            captured_models.append(json["model"])
            # Ollama native API 응답 포맷
            if "api/chat" in url:
                return _mock_response_ollama("<p>ok</p>")
            return _mock_response("<p>ok</p>")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        generate_all(build_sample_config())
        # DEFAULT(qwen3:14b), HEAVY(gemma4:26b), LONG(gemma4-e4b)
        assert "qwen3:14b" in captured_models  # BLUF/Trade/Appendix
        assert "gemma4:26b" in captured_models  # Thesis/Numbers/Risks
        assert any("gemma-4-e4b" in m or "gemma4-e4b" in m for m in captured_models)  # Business

    def test_force_all_heavy_routes_everything_to_26b(self, monkeypatch):
        captured_models = []

        def fake_post(self, url, json=None, **kwargs):
            captured_models.append(json["model"])
            if "api/chat" in url:
                return _mock_response_ollama("<p>ok</p>")
            return _mock_response("<p>ok</p>")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        generate_all(build_sample_config(), force_all_heavy=True)
        assert all(m == "gemma4:26b" for m in captured_models)
        assert len(captured_models) == 7


# ── config 어댑터 ─────────────────────────────────────────────────


class TestConfigAdapter:
    def test_accepts_dict(self, monkeypatch):
        _install_section_mock(monkeypatch)
        html = generate_section("bluf", build_sample_config())
        assert "<p>" in html

    def test_accepts_object_with_attrs(self, monkeypatch):
        _install_section_mock(monkeypatch)

        class CfgObj:
            META = {"company_name": "테스트", "ticker": "000000"}
            PRICE = {"current": 1000}
            TARGET_PRICE = {"weighted": 1500}
            trade_ticket = {"opinion": "BUY", "stop_loss": 900}
            THESIS = []

        html = generate_section("bluf", CfgObj())
        assert "<p>" in html
