# CUFA §6 Trade Prompt

## System
Trade 포지션 rationale HTML 작성.

**필수 출력 규칙** (Evaluator v3):
1. `position_size_pct {숫자}%` 형식 포함.
2. `Risk/Reward {숫자}배` 또는 `R/R {숫자}` 형식 포함.
3. `backtest_engine: open-trading-api/QuantPipeline` 명시 (1회).
4. 진입 전략, 분할 매수 계획, 청산 조건 단락.

## User Template
```
종목: {company_name}
진입가: {entry_price}원
목표가: {target_price}원
손절가: {stop_loss}원
Position Size: {position_size_pct}%
Risk/Reward: {risk_reward}배
Horizon: {horizon_months}개월
```

## Expected Output
```html
<h4>포지션 계획</h4>
<p>진입가 {숫자}원, 목표가 {숫자}원, 손절가 {숫자}원.
   Risk/Reward {숫자}배, position_size_pct {숫자}%로 포트폴리오 내 비중 할당.</p>
<h4>진입 전략</h4>
<p>{분할 매수/타이밍 2-3문장}</p>
<h4>청산 조건</h4>
<p>{목표 도달 / 손절 발동 / Kill Condition 발현 시 대응}</p>
<p>Backtest: <code>backtest_engine: open-trading-api/QuantPipeline</code> — 기간 {기간} 기준 검증.</p>
```
