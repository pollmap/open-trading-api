"""v1.2 IBKR + Upbit/Crypto.com adapter + i18n prompt tests."""
from __future__ import annotations

import pytest


# ── IBKR ───────────────────────────────────────────────────


def test_ibkr_imports():
    from kis_backtest.providers.ibkr import (
        IBKRBrokerageProvider,
        IBKRPriceAdapter,
    )
    assert IBKRBrokerageProvider is not None
    assert IBKRPriceAdapter is not None


def test_ibkr_raises_without_tws(monkeypatch):
    """TWS 미실행 + ib-insync 미설치 모두 에러."""
    monkeypatch.setenv("IBKR_HOST", "127.0.0.1")
    monkeypatch.setenv("IBKR_PORT", "9999")  # 없는 포트
    from kis_backtest.providers.ibkr import IBKRBrokerageProvider
    with pytest.raises((ImportError, ConnectionError, Exception)):
        IBKRBrokerageProvider()


# ── Upbit Brokerage adapter ────────────────────────────────


def test_upbit_brokerage_importable():
    from kis_backtest.providers.upbit import (
        UpbitBrokerageProvider,
        UpbitPriceAdapter,
    )
    assert UpbitBrokerageProvider is not None
    assert UpbitPriceAdapter is not None


def test_upbit_exports_match_init():
    """__init__.py __all__에 adapter 두 개 추가 확인."""
    from kis_backtest.providers import upbit
    assert "UpbitBrokerageProvider" in upbit.__all__
    assert "UpbitPriceAdapter" in upbit.__all__


# ── Crypto.com ─────────────────────────────────────────────


def test_cryptocom_imports():
    from kis_backtest.providers.cryptocom import (
        CryptoComBrokerageProvider,
        CryptoComPriceAdapter,
    )
    assert CryptoComBrokerageProvider is not None


def test_cryptocom_requires_credentials(monkeypatch):
    monkeypatch.delenv("CRYPTO_COM_API_KEY", raising=False)
    monkeypatch.delenv("CRYPTO_COM_API_SECRET", raising=False)
    from kis_backtest.providers.cryptocom import CryptoComBrokerageProvider
    with pytest.raises(ValueError, match="credentials"):
        CryptoComBrokerageProvider()


def test_cryptocom_price_adapter_no_credentials():
    """public ticker는 credential 없이도 생성 가능."""
    from kis_backtest.providers.cryptocom import CryptoComPriceAdapter
    adapter = CryptoComPriceAdapter()
    assert hasattr(adapter, "get_current_price")


# ── Agent prompts i18n ─────────────────────────────────────


def test_prompts_all_locales_have_fast_tier():
    from kis_backtest.luxon.intelligence.i18n_prompts import Locale, Tier, get_prompt

    for loc in Locale:
        prompt = get_prompt(Tier.FAST, loc)
        assert isinstance(prompt, str)
        assert len(prompt) > 10


def test_prompts_korean_content():
    from kis_backtest.luxon.intelligence.i18n_prompts import Locale, Tier, get_prompt
    ko = get_prompt(Tier.DEFAULT, Locale.KO)
    assert "Luxon" in ko
    assert "마크다운" in ko or "퀀트" in ko


def test_prompts_english_default():
    from kis_backtest.luxon.intelligence.i18n_prompts import Tier, get_prompt
    en = get_prompt(Tier.FAST)  # default locale = EN
    assert "bullish" in en.lower() or "bearish" in en.lower()


def test_prompts_fallback_to_english():
    """지원 안 되는 locale은 EN fallback."""
    from kis_backtest.luxon.intelligence.i18n_prompts import Tier, get_prompt

    class _FakeLocale:
        value = "xx-XX"

    prompt = get_prompt(Tier.FAST, _FakeLocale())  # type: ignore
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_available_locales_includes_en_ko_ja_zh():
    from kis_backtest.luxon.intelligence.i18n_prompts import available_locales
    locales = available_locales()
    for code in ("en", "ko", "ja", "zh-CN"):
        assert code in locales


# ── Docker config ──────────────────────────────────────────


def test_dockerfile_exists():
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    assert (repo / "Dockerfile").exists()
    assert (repo / "docker-compose.yml").exists()
    assert (repo / ".dockerignore").exists()


def test_dockerfile_pins_python_version():
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    dockerfile = (repo / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.11" in dockerfile
    assert "USER luxon" in dockerfile  # non-root
    assert "HEALTHCHECK" in dockerfile


def test_compose_has_healthcheck_and_volumes():
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    compose = (repo / "docker-compose.yml").read_text(encoding="utf-8")
    assert "luxon_state:" in compose
    assert "healthcheck" in compose
    assert "restart: unless-stopped" in compose
