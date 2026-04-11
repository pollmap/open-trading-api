"""
Luxon Terminal — CUFA 보고서 HTML → CufaReportDigest 파서 (Sprint 8 Phase 2).

cufa-equity-report 가 생성하는 표준 HTML 보고서에서:
    1. 타이틀  → 회사명 + 종목 코드
    2. 첫 section-title → sector (키워드 목록 중 첫 단어)
    3. 첫 section-title → themes (sector 뒤 쉼표로 구분된 키워드들)
    4. 본문 <p> 태그 → 인명 + 직함 regex 매칭으로 CEO / key_persons 추정

heuristic 파서 (NER 미사용). 한계:
    - 인명 추출은 regex 기반 ([가-힣]{2,4} + 직함) → 오검출 가능
    - sector/themes 는 section-title 의 " — " 뒤 텍스트 split → 보고서 작성자
      스타일에 종속

Sprint 8 는 **최소 MVP**. 정확도 끌어올리기는 Sprint 8+ 또는 별도 LLM 파서.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

from kis_backtest.luxon.graph.ingestors.cufa_ingestor import CufaReportDigest


class CufaHtmlParser:
    """CUFA HTML 보고서 → CufaReportDigest 변환 파서.

    Usage:
        parser = CufaHtmlParser()
        digest = parser.parse_file(Path("~/Desktop/*_CUFA_보고서.html"))

        # 또는 이미 읽은 문자열로:
        digest = parser.parse_html(html_str)
    """

    # "삼성전자 (005930) — CUFA 기업분석보고서" 에서 회사명 + 종목코드 추출
    _TITLE_PATTERN = re.compile(r"^(.+?)\s*\((\d{6})\)")

    # 한국 인명 + 직함: "이재용 부회장", "한종희 사장", "곽노정 대표이사"
    _PERSON_PATTERN = re.compile(
        r"([가-힣]{2,4})\s*(?:부회장|회장|사장|대표이사|대표|전무|CEO|이사회\s*의장)"
    )

    # theme 문자열 후미 정리용: "가전의 완성체" → "가전"
    _THEME_SUFFIX_TRIM = (
        "의 완성체",
        "의 핵심",
        "의 대표",
        "의 리더",
        "의 강자",
    )

    def parse_file(self, path: str | Path) -> CufaReportDigest:
        """파일 경로에서 파싱. UTF-8 로 읽음."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"CUFA HTML 파일 없음: {path}")
        return self.parse_html(path.read_text(encoding="utf-8"))

    def parse_html(self, html: str) -> CufaReportDigest:
        """HTML 문자열에서 파싱.

        Raises:
            ValueError: 타이틀에서 종목 코드 추출 실패.
        """
        soup = BeautifulSoup(html, "html.parser")

        # 1. 종목 코드 + 회사명 (타이틀)
        title_tag = soup.find("title")
        if title_tag is None:
            raise ValueError("CUFA HTML 에 <title> 태그 없음")
        title_text = title_tag.get_text(strip=True)

        match = self._TITLE_PATTERN.search(title_text)
        if not match:
            raise ValueError(
                f"CUFA HTML 타이틀 형식 불일치 (회사명 (6자리코드) 필요): "
                f"{title_text!r}"
            )
        symbol = match.group(2).strip()

        # 2. 첫 section-title → sector + themes
        sector, themes = self._extract_sector_and_themes(soup)

        # 3. 본문 <p> → 인명 추출 → 언급 빈도 순 정렬 → CEO(1명) + key_persons
        ceo_name, key_persons = self._extract_persons(soup)

        return CufaReportDigest(
            symbol=symbol,
            ceo_name=ceo_name,
            key_persons=key_persons,
            sector=sector,
            themes=themes,
        )

    # ── Helpers ────────────────────────────────────────────

    def _extract_sector_and_themes(
        self, soup: BeautifulSoup,
    ) -> tuple[str, list[str]]:
        """첫 section-title 의 ' — ' 뒤 텍스트를 쉼표 split → sector + themes."""
        section_title = soup.select_one(".section-title")
        if section_title is None:
            return ("", [])

        text = section_title.get_text(strip=True)
        if "—" not in text:
            return ("", [])

        _, keywords_part = text.split("—", 1)
        # 쉼표/가운데점/슬래시로 구분된 키워드 목록
        raw_keywords = re.split(r"[,·/]", keywords_part)
        cleaned = [
            self._clean_theme(k.strip())
            for k in raw_keywords
            if k.strip()
        ]
        if not cleaned:
            return ("", [])

        sector = cleaned[0]
        themes = cleaned[1:]  # 빈 리스트 가능
        return (sector, themes)

    def _extract_persons(
        self, soup: BeautifulSoup,
    ) -> tuple[str | None, list[str]]:
        """본문 <p> 태그 전체에서 인명 + 직함 패턴 매칭. 빈도 순 정렬.

        Returns:
            (ceo_name, key_persons[0..4])
            ceo_name = 가장 많이 언급된 인물 (휴리스틱)
            key_persons = 그 다음 상위 4명까지
        """
        counter: Counter[str] = Counter()
        for p in soup.find_all("p"):
            p_text = p.get_text()
            for match in self._PERSON_PATTERN.finditer(p_text):
                name = match.group(1)
                counter[name] += 1

        if not counter:
            return (None, [])

        ranked = [name for name, _ in counter.most_common()]
        ceo_name = ranked[0]
        key_persons = ranked[1:5]  # 최대 4명
        return (ceo_name, key_persons)

    @classmethod
    def _clean_theme(cls, raw: str) -> str:
        """theme 문자열 후미 조사/수식어 제거. 예: '가전의 완성체' → '가전'."""
        s = raw.strip()
        for suffix in cls._THEME_SUFFIX_TRIM:
            if s.endswith(suffix):
                return s[: -len(suffix)].strip()
        return s


__all__ = ["CufaHtmlParser"]
