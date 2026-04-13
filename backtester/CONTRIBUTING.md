# Contributing to Luxon Terminal

> Luxon Terminal은 **1인 AI 퀀트 운용 시스템**입니다. 기여 환영합니다.

## 개발 환경 셋업

```bash
git clone https://github.com/YOUR_ORG/luxon-terminal.git
cd luxon-terminal/backtester

# 의존성
uv sync          # 또는 pip install -r requirements.txt

# KIS API 키 (모의투자만 테스트 가능)
cp .env.example .env   # 또는 ~/KIS/config/kis_devlp.yaml 수정
```

## 전체 테스트

```bash
pytest tests/ -q --ignore=tests/test_luxon_intelligence.py
# 기대: 950+ PASS
```

## 브랜치 전략

- `main` — 안정 버전, 항상 green tests
- `feat/*` — 신규 기능
- `fix/*` — 버그 수정
- `refactor/*` — 리팩토링

## 커밋 메시지

Conventional Commits 따름:
```
feat(luxon): 신규 피처
fix(execution): 버그 수정
refactor(portfolio): 리팩토링
test(core): 테스트 추가
docs(readme): 문서 수정
```

## PR 체크리스트

- [ ] `pytest tests/ -q` 전부 PASS
- [ ] 신규 코드에 테스트 포함 (커버리지 80%+)
- [ ] `CLAUDE.md` / `ARCHITECTURE.md` 변경 사항 반영
- [ ] 민감 정보 없음 (API 키, HTS ID, 계좌번호)

## 보안 정책

- **절대 하드코딩 금지**: API 키, 앱시크릿, 계좌번호, HTS ID
- `.env` / `~/KIS/config/kis_devlp.yaml` 만 사용
- 민감 파일 패턴은 `.gitignore`에 추가됨:
  - `kis_devlp.yaml`
  - `*.env`
  - `~/.luxon/` (포지션/conviction 상태)

취약점 발견 시: GitHub Issues 비공개 또는 direct message.

## 설계 철학

1. **Extensible AI Philosophy** (`feedback_extensible_ai_philosophy.md`)
   - Agent-Ready: 모든 공개 API는 LLM 에이전트가 호출 가능
   - 범용+개인화: 기본 동작은 일반적, 튜닝은 config로
   - AI-Augmentable: 코드 블록은 LLM 읽기 쉽게 모듈화

2. **선순환 3루프** (자세한 설명은 ARCHITECTURE.md)
   - BREAK1: `WeeklyReport → conviction` (피드백 어댑터)
   - BREAK2: `KillCondition → KillSwitch` (안전 장치)
   - BREAK3: `TA signal → probability learning`

3. **CFS 전용**: 재무제표는 반드시 연결(CFS). 별도(OFS) 금지.

## 라이선스

MIT (FINANCIAL SOFTWARE DISCLAIMER 포함) — [LICENSE](LICENSE) 참조.

이 소프트웨어는 실거래 손실을 책임지지 않습니다. 모의투자에서 충분히 검증 후 사용하세요.
