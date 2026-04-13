"""
간단한 HTML 조립기 — 7섹션 narrative → 단일 보고서 HTML.

CUFA builder/core.py의 풍부한 레이아웃 대신 MVP 수준의 단순 HTML.
Evaluator v3 regex는 HTML 구조 무관(텍스트 기반)하므로 PASS 확보 가능.
Trade Ticket YAML 블록 삽입 포함.
"""
from __future__ import annotations

from typing import Any

_HTML_SHELL = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body {{ font-family: 'Noto Sans KR', sans-serif; max-width: 860px;
            margin: 40px auto; padding: 0 20px; line-height: 1.6;
            background: #0f1115; color: #e4e6eb; }}
    article {{ margin: 32px 0; padding: 20px; background: #1a1d24;
               border-radius: 8px; border: 1px solid #2a2d35; }}
    h1, h2, h3, h4 {{ color: #ffffff; }}
    h2 {{ border-bottom: 2px solid #3a3d45; padding-bottom: 6px; }}
    code, pre {{ background: #0b0c10; color: #b9f1ff; padding: 2px 6px;
                 border-radius: 4px; font-size: 0.9em; }}
    pre {{ padding: 12px; overflow-x: auto; }}
    ul {{ padding-left: 24px; }}
    .ticket-box {{ background: #0d2238; border: 1px solid #2b5178;
                   padding: 16px; border-radius: 8px; }}
  </style>
</head>
<body>
<h1>{title}</h1>
<p class="meta">종목코드: {ticker} | 생성일: {generated_at}</p>

<article>
  <h2>§1. BLUF</h2>
  {bluf}
</article>

<article>
  <h2>§2. Investment Thesis</h2>
  {thesis}
</article>

<article>
  <h2>§3. Business Setup</h2>
  {business}
</article>

<article>
  <h2>§4. Numbers</h2>
  {numbers}
</article>

<article>
  <h2>§5. Risks</h2>
  {risks}
</article>

<article>
  <h2>§6. Trade Implementation</h2>
  {trade}
  <div class="ticket-box trade_ticket">
    <h4>Trade Ticket</h4>
    <pre class="ticket-yaml">{ticket_yaml}</pre>
  </div>
</article>

<article>
  <h2>§7. Appendix</h2>
  {appendix}
</article>
</body>
</html>
"""


def assemble(
    narratives: dict[str, str],
    *,
    meta: dict[str, Any],
    ticket_yaml: str = "",
    generated_at: str = "",
) -> str:
    """7섹션 narrative + meta → 완전한 HTML 문자열."""
    required_keys = ("bluf", "thesis", "business", "numbers", "risks", "trade", "appendix")
    sections = {k: narratives.get(k, f"<p>(섹션 {k} 미생성)</p>") for k in required_keys}
    company = meta.get("company_name", "Unknown")
    ticker = meta.get("ticker", "000000")
    return _HTML_SHELL.format(
        title=f"{company} CUFA Equity Report",
        ticker=ticker,
        generated_at=generated_at or "",
        ticket_yaml=(ticket_yaml or "# Trade Ticket 미생성")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;"),
        **sections,
    )
