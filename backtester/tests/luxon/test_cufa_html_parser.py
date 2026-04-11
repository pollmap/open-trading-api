"""Sprint 8 — CufaHtmlParser 단위 테스트.

Coverage:
    - 정상 HTML → CufaReportDigest 필드 채움 (symbol/sector/themes/CEO/key_persons)
    - 타이틀 없음 → ValueError
    - 타이틀에 종목코드 없음 → ValueError
    - section-title 없음 → sector/themes 빈값
    - 인명 없는 본문 → ceo_name=None, key_persons=[]
    - parse_file 경로 없음 → FileNotFoundError
    - CEO 가 가장 많이 언급된 인물로 선정됨 (빈도 기반)

Style:
    - inline HTML fixture (실 파일 의존 없음)
    - AAA 패턴, fresh CufaHtmlParser 인스턴스 per test
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kis_backtest.luxon.graph.parsers.cufa_html_parser import CufaHtmlParser


# ── Fixtures ─────────────────────────────────────────────────────────


_SAMPLE_HTML = """<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8">
<title>삼성전자 (005930) — CUFA 기업분석보고서</title>
</head><body>
<div class="section">
    <div class="section-header">
        <div class="section-title">기업 개요 — 반도체, 스마트폰, 가전의 완성체</div>
    </div>
    <div class="content-area">
        <p><strong>이재용 부회장의 리더십 복귀와 경영 안정화</strong>는 투자 매력을 높이는 요인이다.
        이재용 부회장은 HBM4 개발을 가속화하고 있다.</p>
        <p>한종희 사장은 DX 부문을 총괄하고 있으며, 경계현 사장이 DS 부문을 이끈다.
        이재용 부회장의 경영 철학이 반영된다.</p>
        <p>반도체 업황 회복과 함께 HBM 수요가 급증하고 있다.</p>
    </div>
</div>
</body></html>
"""


_NO_PERSON_HTML = """<!DOCTYPE html>
<html><head><title>이노스페이스 (462350) — CUFA 기업분석보고서</title></head><body>
<div class="section-title">기업 개요 — 우주발사체, 하이브리드 로켓</div>
<p>회사는 우주 발사 서비스를 제공한다.</p>
</body></html>
"""


_NO_SECTION_HTML = """<!DOCTYPE html>
<html><head><title>인텔리안테크 (189300) — CUFA 기업분석보고서</title></head><body>
<p>김철수 사장은 위성 통신 시장을 개척했다.</p>
</body></html>
"""


# ── Tests ────────────────────────────────────────────────────────────


def test_parse_html_extracts_symbol_and_sector() -> None:
    # Arrange
    parser = CufaHtmlParser()

    # Act
    digest = parser.parse_html(_SAMPLE_HTML)

    # Assert — 종목 코드 + 섹터 + 테마
    assert digest.symbol == "005930"
    assert digest.sector == "반도체"
    assert "스마트폰" in digest.themes
    # "가전의 완성체" → "가전" 로 정리
    assert "가전" in digest.themes


def test_parse_html_extracts_ceo_by_mention_frequency() -> None:
    # Arrange — "이재용 부회장" 이 3회 언급 (최빈)
    parser = CufaHtmlParser()

    # Act
    digest = parser.parse_html(_SAMPLE_HTML)

    # Assert
    assert digest.ceo_name == "이재용"
    # 그 외 인물은 key_persons
    assert "한종희" in digest.key_persons
    assert "경계현" in digest.key_persons
    # ceo_name 은 key_persons 에 중복 X
    assert "이재용" not in digest.key_persons


def test_parse_html_no_person_returns_none_ceo() -> None:
    # Arrange
    parser = CufaHtmlParser()

    # Act
    digest = parser.parse_html(_NO_PERSON_HTML)

    # Assert
    assert digest.symbol == "462350"
    assert digest.ceo_name is None
    assert digest.key_persons == []
    # section 이 존재하면 sector/themes 는 추출됨
    assert digest.sector == "우주발사체"
    assert "하이브리드 로켓" in digest.themes


def test_parse_html_no_section_title_empty_sector() -> None:
    # Arrange
    parser = CufaHtmlParser()

    # Act
    digest = parser.parse_html(_NO_SECTION_HTML)

    # Assert
    assert digest.symbol == "189300"
    assert digest.sector == ""
    assert digest.themes == []
    # 인명은 있음
    assert digest.ceo_name == "김철수"


def test_parse_html_missing_title_raises() -> None:
    # Arrange
    parser = CufaHtmlParser()
    html_without_title = "<html><body><p>내용</p></body></html>"

    # Act / Assert
    with pytest.raises(ValueError, match="title"):
        parser.parse_html(html_without_title)


def test_parse_html_title_missing_symbol_code_raises() -> None:
    # Arrange
    parser = CufaHtmlParser()
    bad_title_html = "<html><head><title>CUFA 분석 자료</title></head><body></body></html>"

    # Act / Assert
    with pytest.raises(ValueError, match="타이틀 형식"):
        parser.parse_html(bad_title_html)


def test_parse_file_missing_path_raises(tmp_path: Path) -> None:
    # Arrange
    parser = CufaHtmlParser()
    missing = tmp_path / "nowhere.html"

    # Act / Assert
    with pytest.raises(FileNotFoundError):
        parser.parse_file(missing)


def test_parse_file_reads_utf8_html(tmp_path: Path) -> None:
    # Arrange — 파일 저장 후 파싱
    path = tmp_path / "sample.html"
    path.write_text(_SAMPLE_HTML, encoding="utf-8")
    parser = CufaHtmlParser()

    # Act
    digest = parser.parse_file(path)

    # Assert
    assert digest.symbol == "005930"
    assert digest.ceo_name == "이재용"


def test_parse_html_then_ingest_into_graph() -> None:
    """parser → ingestor 통합: digest 가 CufaIngestor 로 그대로 주입 가능한지."""
    # Arrange
    from kis_backtest.luxon.graph.graph import GothamGraph
    from kis_backtest.luxon.graph.ingestors.cufa_ingestor import CufaIngestor
    from kis_backtest.luxon.graph.edges import EdgeKind

    graph = GothamGraph()
    parser = CufaHtmlParser()
    ingestor = CufaIngestor(graph)

    # Act
    digest = parser.parse_html(_SAMPLE_HTML)
    result = ingestor.ingest_digest(digest)

    # Assert
    assert result["symbol_id"] == "symbol:005930"
    assert result["sector_id"] == "sector:반도체"
    # 인물 3명 (이재용 + 한종희 + 경계현)
    assert len(result["person_ids"]) == 3
    # HOLDS 엣지 3개
    assert len(graph.edges_by_kind(EdgeKind.HOLDS)) == 3
    # BELONGS_TO 1개
    assert len(graph.edges_by_kind(EdgeKind.BELONGS_TO)) == 1
