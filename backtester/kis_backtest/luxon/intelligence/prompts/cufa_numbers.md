# CUFA §4 Numbers Prompt

## System
재무/밸류에이션 서술 Numbers 섹션 HTML 작성.

**필수 출력 규칙** (Evaluator v3):
1. `Bear Case 하방 {숫자}원` 형식 반드시 포함.
2. Bear/Base/Bull 3시나리오 명시.
3. `<h4>` 제목 + 단락.
4. 한국어 HTML.

규칙:
- 입력된 재무 숫자만 사용. 추정 명시("가이던스", "컨센서스") 필수.
- Football Field/Valuation Framework 언급.

## User Template
```
종목: {company_name}
현재가: {current_price}원
Target Price: {target_price}원
Bear/Base/Bull 시나리오:
  Bear: {bear_price}원 (확률 {bear_prob}%)
  Base: {base_price}원 (확률 {base_prob}%)
  Bull: {bull_price}원 (확률 {bull_prob}%)
Peer/Multiple 요약: {peer_summary}
WACC: {wacc}%
```

## Expected Output
```html
<h4>밸류에이션 프레임워크</h4>
<p>{WACC 기반 DCF + Peer Multiple 혼합 접근 서술}</p>
<h4>시나리오 분석</h4>
<p><strong>Bear Case 하방 {숫자}원</strong>({확률}%): {조건 서술}.
   Base Case {숫자}원({확률}%): {조건}.
   Bull Case {숫자}원({확률}%): {조건}.</p>
<h4>Peer Comparison</h4>
<p>{peer_summary 서술}</p>
```
