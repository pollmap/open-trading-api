# CUFA §7 Appendix Prompt

## System
Appendix 섹션 HTML — 방법론, 데이터 출처, 한계점.

**필수 출력 규칙** (Evaluator v3):
1. 데이터 출처 명시: `DART`, `KRX`, `Nexus MCP`, `FnGuide` 중 1개 이상 언급.
2. `<h4>` 제목 + 단락.
3. 한국어 HTML, 2~3단락.

## User Template
```
종목: {company_name}
사용 데이터 출처: {data_sources}
밸류에이션 방법론: {valuation_methods}
```

## Expected Output
```html
<h4>데이터 출처</h4>
<p>재무제표 — DART 연결(CFS) 기준. 주가 — KRX/pykrx.
   매크로 — ECOS, Nexus MCP. 전부 2026-04 기준.</p>
<h4>방법론</h4>
<p>{DCF/Multiple/Sum-of-Parts 설명}</p>
<h4>한계점</h4>
<p>{가정의 민감도, 데이터 지연, 향후 업데이트 주기}</p>
```
