<!-- Thank you for contributing to Luxon Terminal! -->

## Summary

<!-- One sentence: what does this PR do and why? -->

## Type of change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that causes existing behavior to change)
- [ ] Documentation update
- [ ] Refactor (no functional change)
- [ ] CI / build

## Test plan

<!-- How did you verify the change? Commands, expected output -->

- [ ] `pytest tests/ -q` passes (950+)
- [ ] New tests added for new behavior
- [ ] Manual verification (describe)

## Security checklist

- [ ] No hardcoded credentials / API keys / account numbers
- [ ] No private infrastructure references (IPs, hostnames)
- [ ] New env vars documented in `.env.example`
- [ ] `.gitignore` updated for new sensitive paths

## Trading safety (if changes execution path)

- [ ] `RiskGateway` still enforced before `LiveOrderExecutor.execute()`
- [ ] `KillSwitch` respected at cycle start
- [ ] Tested in `paper_mode=True` first
- [ ] No auto-promotion bypass

## Related issues

Closes #
