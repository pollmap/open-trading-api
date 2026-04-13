# CUFA §5 Risks Prompt

## System
리스크 + Kill Condition 섹션 HTML 작성.

**필수 출력 규칙** (Evaluator v3 — STRICT):
1. 본문에 `<h4>Kill Condition</h4>` 또는 `<h4>Kill Conditions</h4>` 헤딩 포함.
2. `<ul>` 목록으로 Kill Condition을 **정확히 3개 이상** 나열.
   각 `<li>`에 구체적 수치 기반 반증 조건 명시.
3. 각 Kill Condition은 "~하면 투자 논리 무효화" 형태.
4. 한국어 HTML.

포맷 예시:
```html
<h4>Kill Conditions</h4>
<ul>
  <li>2026년 2분기 매출이 전년 대비 -15% 이하로 역성장 → 논리 무효화</li>
  <li>영업이익률 8% 미만 지속 2분기 → 논리 무효화</li>
  <li>주력 고객사 이탈로 집중도 상위 3사 비중 60% → 40% 하락 → 논리 무효화</li>
</ul>
```

금지:
- 모호한 조건 ("악재 발생 시", "실적 악화 시")
- 숫자 없는 정성적 조건

## User Template
```
종목: {company_name}
투자 논지 요약: {thesis_summary}
주요 리스크 요인: {risk_factors}
Bear Case: {bear_scenario}
EPS 민감도 데이터: {eps_sensitivity}
```

## Expected Output
위 포맷 예시와 동일한 구조. `<h4>주요 리스크</h4>` 단락 추가 가능.
