"""투자 서한 모듈 테스트

pytest -v tests/test_investor_letter.py
"""

from __future__ import annotations

import os
import tempfile

import pytest

from kis_backtest.portfolio.investor_letter import (
    InvestorLetter,
    LetterGenerator,
    LetterMetrics,
    PositionEntry,
    _slugify,
)


# ─── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def sample_position() -> PositionEntry:
    return PositionEntry(
        symbol="005930",
        name="삼성전자",
        weight=25.0,
        return_pct=12.5,
        thesis="반도체 사이클 저점 진입",
        catalyst="HBM 수주 확대",
        lesson="사이클 타이밍 중요",
    )


@pytest.fixture
def ongoing_position() -> PositionEntry:
    return PositionEntry(
        symbol="000660",
        name="SK하이닉스",
        weight=20.0,
        return_pct=8.3,
        thesis="AI 메모리 수요 증가",
        catalyst="엔비디아 공급 계약",
        lesson="",
    )


@pytest.fixture
def sample_metrics() -> LetterMetrics:
    return LetterMetrics(
        period="2026-Q2",
        total_return=15.3,
        benchmark_return=5.1,
        alpha=10.2,
        sharpe=1.85,
        max_dd=-7.2,
        win_rate=72.0,
        positions_count=5,
    )


@pytest.fixture
def sample_letter(
    sample_metrics: LetterMetrics,
    sample_position: PositionEntry,
    ongoing_position: PositionEntry,
) -> InvestorLetter:
    return InvestorLetter(
        period="2026-Q2",
        metrics=sample_metrics,
        positions=[sample_position, ongoing_position],
        macro_regime="글로벌 확장기: 미 연준 금리 인하 사이클 진입",
        outlook="하반기 반도체 업사이클과 AI 투자 확대로 긍정적 전망",
        created_at="2026-07-01 09:00",
    )


@pytest.fixture
def generator() -> LetterGenerator:
    return LetterGenerator()


@pytest.fixture
def custom_generator() -> LetterGenerator:
    return LetterGenerator(author="찬희", fund_name="Alpha Fund")


@pytest.fixture
def review_snapshots() -> list[dict]:
    return [
        {
            "symbol": "005930",
            "name": "삼성전자",
            "weight": 30.0,
            "return_pct": 10.0,
            "thesis": "반도체 회복",
            "catalyst": "HBM",
            "lesson": "사이클 주의",
            "portfolio_return": 8.5,
            "benchmark_return": 3.0,
            "sharpe": 1.5,
            "max_dd": -4.0,
            "win_rate": 70.0,
            "macro_regime": "확장기",
            "outlook": "긍정적",
        },
        {
            "symbol": "000660",
            "name": "SK하이닉스",
            "weight": 20.0,
            "return_pct": -5.0,
            "thesis": "AI 메모리",
            "catalyst": "엔비디아",
            "lesson": "",
            "portfolio_return": 8.5,
            "benchmark_return": 3.0,
            "sharpe": 1.5,
            "max_dd": -4.0,
            "win_rate": 70.0,
            "macro_regime": "확장기",
            "outlook": "긍정적",
        },
    ]


# ─── PositionEntry Tests ─────────────────────────────────────────────


class TestPositionEntry:
    def test_creation(self, sample_position: PositionEntry) -> None:
        assert sample_position.symbol == "005930"
        assert sample_position.name == "삼성전자"
        assert sample_position.weight == 25.0
        assert sample_position.return_pct == 12.5

    def test_frozen(self, sample_position: PositionEntry) -> None:
        with pytest.raises(AttributeError):
            sample_position.weight = 30.0  # type: ignore[misc]

    def test_empty_lesson(self, ongoing_position: PositionEntry) -> None:
        assert ongoing_position.lesson == ""

    def test_thesis_and_catalyst(self, sample_position: PositionEntry) -> None:
        assert "반도체" in sample_position.thesis
        assert "HBM" in sample_position.catalyst


# ─── LetterMetrics Tests ─────────────────────────────────────────────


class TestLetterMetrics:
    def test_creation(self, sample_metrics: LetterMetrics) -> None:
        assert sample_metrics.period == "2026-Q2"
        assert sample_metrics.total_return == 15.3
        assert sample_metrics.positions_count == 5

    def test_alpha_calculation(self, sample_metrics: LetterMetrics) -> None:
        assert sample_metrics.alpha == pytest.approx(10.2, abs=0.01)

    def test_frozen(self, sample_metrics: LetterMetrics) -> None:
        with pytest.raises(AttributeError):
            sample_metrics.sharpe = 2.0  # type: ignore[misc]

    def test_negative_returns(self) -> None:
        metrics = LetterMetrics(
            period="2026-Q1",
            total_return=-5.0,
            benchmark_return=-2.0,
            alpha=-3.0,
            sharpe=-0.5,
            max_dd=-15.0,
            win_rate=30.0,
            positions_count=3,
        )
        assert metrics.total_return < 0
        assert metrics.alpha < 0


# ─── InvestorLetter Tests ────────────────────────────────────────────


class TestInvestorLetter:
    def test_creation(self, sample_letter: InvestorLetter) -> None:
        assert sample_letter.period == "2026-Q2"
        assert len(sample_letter.positions) == 2

    def test_frozen(self, sample_letter: InvestorLetter) -> None:
        with pytest.raises(AttributeError):
            sample_letter.period = "2026-Q3"  # type: ignore[misc]

    def test_default_author(self, sample_letter: InvestorLetter) -> None:
        assert sample_letter.author == "Luxon AI"
        assert sample_letter.fund_name == "Luxon Quant Fund"


# ─── to_markdown Tests ───────────────────────────────────────────────


class TestToMarkdown:
    def test_contains_title(self, sample_letter: InvestorLetter) -> None:
        md = sample_letter.to_markdown()
        assert "# Luxon Quant Fund 투자 서한 — 2026-Q2" in md

    def test_contains_metrics_table(self, sample_letter: InvestorLetter) -> None:
        md = sample_letter.to_markdown()
        assert "| 총 수익률 | 15.30% |" in md
        assert "| 벤치마크 | 5.10% |" in md
        assert "| 알파 | 10.20% |" in md
        assert "| Sharpe | 1.85 |" in md
        assert "| 최대 낙폭 | -7.20% |" in md
        assert "| 승률 | 72.00% |" in md

    def test_contains_macro_section(self, sample_letter: InvestorLetter) -> None:
        md = sample_letter.to_markdown()
        assert "## 매크로 환경" in md
        assert "글로벌 확장기" in md

    def test_contains_position_sections(self, sample_letter: InvestorLetter) -> None:
        md = sample_letter.to_markdown()
        assert "### 005930 (삼성전자) — 비중 25.0%" in md
        assert "### 000660 (SK하이닉스) — 비중 20.0%" in md

    def test_contains_thesis(self, sample_letter: InvestorLetter) -> None:
        md = sample_letter.to_markdown()
        assert "**논리:** 반도체 사이클 저점 진입" in md

    def test_contains_catalyst(self, sample_letter: InvestorLetter) -> None:
        md = sample_letter.to_markdown()
        assert "**카탈리스트:** HBM 수주 확대" in md

    def test_ongoing_position_lesson(self, sample_letter: InvestorLetter) -> None:
        md = sample_letter.to_markdown()
        assert "(진행 중)" in md

    def test_completed_position_lesson(self, sample_letter: InvestorLetter) -> None:
        md = sample_letter.to_markdown()
        assert "사이클 타이밍 중요" in md

    def test_contains_outlook(self, sample_letter: InvestorLetter) -> None:
        md = sample_letter.to_markdown()
        assert "## 전망" in md
        assert "반도체 업사이클" in md

    def test_contains_footer(self, sample_letter: InvestorLetter) -> None:
        md = sample_letter.to_markdown()
        assert "*Luxon AI | 2026-07-01 09:00*" in md

    def test_custom_author(
        self, sample_metrics: LetterMetrics, sample_position: PositionEntry
    ) -> None:
        letter = InvestorLetter(
            period="2026-Q1",
            metrics=sample_metrics,
            positions=[sample_position],
            macro_regime="수축기",
            outlook="보수적",
            created_at="2026-04-01 10:00",
            author="찬희",
            fund_name="Alpha Fund",
        )
        md = letter.to_markdown()
        assert "# Alpha Fund 투자 서한 — 2026-Q1" in md
        assert "*찬희 | 2026-04-01 10:00*" in md


# ─── to_blog_post Tests ──────────────────────────────────────────────


class TestToBlogPost:
    def test_returns_dict(self, sample_letter: InvestorLetter) -> None:
        post = sample_letter.to_blog_post()
        assert isinstance(post, dict)

    def test_has_required_keys(self, sample_letter: InvestorLetter) -> None:
        post = sample_letter.to_blog_post()
        assert "title" in post
        assert "slug" in post
        assert "content" in post

    def test_title_format(self, sample_letter: InvestorLetter) -> None:
        post = sample_letter.to_blog_post()
        assert "Luxon Quant Fund" in post["title"]
        assert "2026-Q2" in post["title"]

    def test_slug_format(self, sample_letter: InvestorLetter) -> None:
        post = sample_letter.to_blog_post()
        slug = post["slug"]
        assert " " not in slug
        assert slug == slug.lower()

    def test_content_is_markdown(self, sample_letter: InvestorLetter) -> None:
        post = sample_letter.to_blog_post()
        assert post["content"].startswith("#")


# ─── _slugify Tests ──────────────────────────────────────────────────


class TestSlugify:
    def test_basic(self) -> None:
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars(self) -> None:
        assert _slugify("Luxon Quant Fund — 2026-Q2") == "luxon-quant-fund-2026-q2"

    def test_multiple_spaces(self) -> None:
        assert _slugify("a   b") == "a-b"

    def test_empty_string(self) -> None:
        assert _slugify("") == ""


# ─── LetterGenerator Tests ───────────────────────────────────────────


class TestLetterGenerator:
    def test_default_init(self, generator: LetterGenerator) -> None:
        assert generator.author == "Luxon AI"
        assert generator.fund_name == "Luxon Quant Fund"

    def test_custom_init(self, custom_generator: LetterGenerator) -> None:
        assert custom_generator.author == "찬희"
        assert custom_generator.fund_name == "Alpha Fund"

    def test_generate_returns_letter(
        self,
        generator: LetterGenerator,
        sample_metrics: LetterMetrics,
        sample_position: PositionEntry,
    ) -> None:
        letter = generator.generate(
            period="2026-Q2",
            metrics=sample_metrics,
            positions=[sample_position],
            macro_regime="확장기",
            outlook="긍정적",
        )
        assert isinstance(letter, InvestorLetter)
        assert letter.period == "2026-Q2"

    def test_generate_preserves_positions(
        self,
        generator: LetterGenerator,
        sample_metrics: LetterMetrics,
        sample_position: PositionEntry,
        ongoing_position: PositionEntry,
    ) -> None:
        letter = generator.generate(
            period="2026-Q2",
            metrics=sample_metrics,
            positions=[sample_position, ongoing_position],
            macro_regime="확장기",
            outlook="긍정적",
        )
        assert len(letter.positions) == 2

    def test_generate_sets_created_at(
        self,
        generator: LetterGenerator,
        sample_metrics: LetterMetrics,
        sample_position: PositionEntry,
    ) -> None:
        letter = generator.generate(
            period="2026-Q2",
            metrics=sample_metrics,
            positions=[sample_position],
            macro_regime="확장기",
            outlook="긍정적",
        )
        assert letter.created_at  # non-empty
        assert "2026" in letter.created_at

    def test_generate_empty_period_raises(
        self,
        generator: LetterGenerator,
        sample_metrics: LetterMetrics,
        sample_position: PositionEntry,
    ) -> None:
        with pytest.raises(ValueError, match="period"):
            generator.generate(
                period="",
                metrics=sample_metrics,
                positions=[sample_position],
                macro_regime="확장기",
                outlook="긍정적",
            )

    def test_generate_empty_positions_raises(
        self,
        generator: LetterGenerator,
        sample_metrics: LetterMetrics,
    ) -> None:
        with pytest.raises(ValueError, match="positions"):
            generator.generate(
                period="2026-Q2",
                metrics=sample_metrics,
                positions=[],
                macro_regime="확장기",
                outlook="긍정적",
            )

    def test_generate_uses_custom_author(
        self,
        custom_generator: LetterGenerator,
        sample_metrics: LetterMetrics,
        sample_position: PositionEntry,
    ) -> None:
        letter = custom_generator.generate(
            period="2026-Q2",
            metrics=sample_metrics,
            positions=[sample_position],
            macro_regime="확장기",
            outlook="긍정적",
        )
        assert letter.author == "찬희"
        assert letter.fund_name == "Alpha Fund"


# ─── generate_from_reviews Tests ─────────────────────────────────────


class TestGenerateFromReviews:
    def test_generates_from_snapshots(
        self,
        generator: LetterGenerator,
        review_snapshots: list[dict],
    ) -> None:
        letter = generator.generate_from_reviews(review_snapshots, "2026-Q2")
        assert isinstance(letter, InvestorLetter)
        assert len(letter.positions) == 2

    def test_extracts_positions(
        self,
        generator: LetterGenerator,
        review_snapshots: list[dict],
    ) -> None:
        letter = generator.generate_from_reviews(review_snapshots, "2026-Q2")
        symbols = [p.symbol for p in letter.positions]
        assert "005930" in symbols
        assert "000660" in symbols

    def test_calculates_weighted_return(
        self,
        generator: LetterGenerator,
        review_snapshots: list[dict],
    ) -> None:
        letter = generator.generate_from_reviews(review_snapshots, "2026-Q2")
        # 30% * 10% + 20% * (-5%) = 3.0 - 1.0 = 2.0
        assert letter.metrics.total_return == pytest.approx(2.0, abs=0.01)

    def test_calculates_win_rate(
        self,
        generator: LetterGenerator,
        review_snapshots: list[dict],
    ) -> None:
        letter = generator.generate_from_reviews(review_snapshots, "2026-Q2")
        # 1 winner out of 2 = 50%
        assert letter.metrics.win_rate == pytest.approx(50.0, abs=0.01)

    def test_uses_first_snapshot_metrics(
        self,
        generator: LetterGenerator,
        review_snapshots: list[dict],
    ) -> None:
        letter = generator.generate_from_reviews(review_snapshots, "2026-Q2")
        assert letter.metrics.benchmark_return == 3.0
        assert letter.metrics.sharpe == 1.5
        assert letter.metrics.max_dd == -4.0

    def test_empty_snapshots_raises(self, generator: LetterGenerator) -> None:
        with pytest.raises(ValueError, match="review_snapshots"):
            generator.generate_from_reviews([], "2026-Q2")

    def test_macro_regime_from_snapshot(
        self,
        generator: LetterGenerator,
        review_snapshots: list[dict],
    ) -> None:
        letter = generator.generate_from_reviews(review_snapshots, "2026-Q2")
        assert letter.macro_regime == "확장기"

    def test_outlook_from_snapshot(
        self,
        generator: LetterGenerator,
        review_snapshots: list[dict],
    ) -> None:
        letter = generator.generate_from_reviews(review_snapshots, "2026-Q2")
        assert letter.outlook == "긍정적"


# ─── save Tests ──────────────────────────────────────────────────────


class TestSave:
    def test_save_creates_file(
        self, generator: LetterGenerator, sample_letter: InvestorLetter
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generator.save(sample_letter, tmpdir)
            assert os.path.exists(path)

    def test_save_returns_path(
        self, generator: LetterGenerator, sample_letter: InvestorLetter
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generator.save(sample_letter, tmpdir)
            assert path.endswith(".md")
            assert "investor_letter_2026-Q2" in path

    def test_save_file_content(
        self, generator: LetterGenerator, sample_letter: InvestorLetter
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generator.save(sample_letter, tmpdir)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "투자 서한" in content
            assert "삼성전자" in content

    def test_save_creates_directory(
        self, generator: LetterGenerator, sample_letter: InvestorLetter
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "nested", "dir")
            path = generator.save(sample_letter, nested)
            assert os.path.exists(path)

    def test_save_utf8_encoding(
        self, generator: LetterGenerator, sample_letter: InvestorLetter
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generator.save(sample_letter, tmpdir)
            with open(path, "rb") as f:
                raw = f.read()
            # UTF-8 한글 확인
            assert "삼성전자".encode("utf-8") in raw
