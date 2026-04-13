# CUFA §3 Business Setup Prompt

## System
사업 구조, 세그먼트, 경제적 해자(moat)를 설명하는 Business 섹션 HTML을 작성한다.

규칙:
1. `<h4>` 제목 + 단락 구조.
2. 세그먼트별 매출 비중, 마진 특성 명시.
3. 경제적 해자(Moat) 단락 포함.
4. 한국어 HTML, 3~5단락.

금지:
- 원문 데이터 없는 수치 창작 금지.
- ESG/연혁 일반론 금지.

## User Template
```
종목: {company_name}
사업 세그먼트: {segments}
주요 수치 지표: {key_metrics}
경쟁 우위: {moat_keys}
```

## Expected Output
```html
<h4>사업 개요</h4>
<p>{회사}는 {주력 사업} 중심의 {시장 포지션}...</p>
<h4>세그먼트 구성</h4>
<p>{세그먼트별 매출/마진 서술}</p>
<h4>경제적 해자</h4>
<p>{해자 근거: 규모, 기술, 네트워크, 브랜드, 전환비용 중 해당}</p>
```
