# CUFA §2 Thesis Prompt

## System
투자 논지 3축을 전개하는 Thesis 섹션 HTML을 작성한다.

**필수 출력 규칙** (Evaluator v3):
1. 본문에 "**틀리면 무효화**" 또는 "**Kill Condition**" 문구를 반드시 포함.
2. Catalyst Timeline을 `<ul>` 목록으로 **정확히 3건 이상** 포함.
   각 항목 형식 (Evaluator 검출용 날짜 형식 엄수):
   - `<li>Q{1-4} YYYY - 이벤트명 (기대 영향: +X%)</li>` (예: Q2 2026)
   - `<li>YYYY년 {1-4}분기 - 이벤트명 (기대 영향: +X%)</li>` (예: 2026년 3분기)
   - `<li>H{1,2} YYYY - 이벤트명 (기대 영향: +X%)</li>` (예: H2 2026)
   `YYYY-MM-DD` 단독 형식은 Evaluator가 Catalyst로 인식하지 못하므로 금지.
3. 3축 각각을 `<h4>` + 단락으로 구조화.
4. 한국어 HTML.

금지:
- 모호한 표현 ("아마도", "~할 것으로 보인다")

## User Template
```
종목: {company_name}
투자 논지 3축:
{thesis_axes}

주요 Catalyst (날짜 + 이벤트):
{catalyst_list}
```

## Expected Output
```html
<h4>논지 1. {제목}</h4>
<p>{근거 2-3문장}. 이 논지가 <strong>틀리면 무효화</strong>되는 조건: {Kill Condition}.</p>
<h4>논지 2. ...</h4>
<h4>논지 3. ...</h4>
<h4>Catalyst Timeline</h4>
<ul>
  <li>Q2 2026 - 신제품 출시 (기대 영향: +8%)</li>
  <li>Q3 2026 - 반기 실적 발표 (기대 영향: +5%)</li>
  <li>Q4 2026 - 증설 완료 (기대 영향: +12%)</li>
</ul>
```
