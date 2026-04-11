"""Luxon Terminal — GothamGraph parsers (Sprint 8 Phase 2).

외부 문서(HTML/Markdown)를 CufaReportDigest 등 구조화된 dataclass 로 변환.
ingestors 와 분리된 이유: 파서는 bs4 의존이 있고, 실패 확률이 높아서
ingestor(그래프 쓰기) 와 에러 도메인을 분리.
"""
from kis_backtest.luxon.graph.parsers.cufa_html_parser import CufaHtmlParser

__all__ = ["CufaHtmlParser"]
