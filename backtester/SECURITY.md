# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.0.x   | ✅ |
| < 1.0   | ❌ (pre-release) |

## Reporting a Vulnerability

**Do NOT open a public issue for security vulnerabilities.**

Email: **security@luxon-terminal.example** (replace with your org address)
or use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability).

Response SLA:
- **Critical** (remote code exec, key leakage): 48h acknowledgment, 7d patch
- **High** (privilege escalation, auth bypass): 7d acknowledgment, 30d patch
- **Medium/Low**: 14d acknowledgment, next minor release

## Scope

In scope:
- `kis_backtest/` Python package
- `scripts/` CLI tools
- Public API endpoints (`scripts/luxon_server.py` dashboard)

Out of scope:
- Third-party MCP servers
- KIS Open API infrastructure (report to Korea Investment & Securities)
- User-hosted derivatives / forks

## Sensitive Data Handling

**This project handles live trading credentials.** Known sensitive patterns:

| Type | Storage | Never commit |
|------|---------|-------------|
| KIS app key/secret | `~/KIS/config/kis_devlp.yaml` | ✅ `.gitignore` |
| HTS ID | same | ✅ |
| Account number | same | ✅ |
| JWT tokens | runtime only | ✅ |
| MCP bearer tokens | env var `MCP_VPS_TOKEN` | ✅ |
| Position history | `~/.luxon/` | ✅ |
| Live fills | `fills/live/` | ✅ |

All sensitive paths are blocked by `.gitignore` at commit time.

## Security Best Practices

When deploying Luxon Terminal:

1. **Credentials**: Use OS keyring or a secrets manager (HashiCorp Vault,
   AWS Secrets Manager) — do not rely on plaintext YAML.
2. **Network**: Bind dashboards (`luxon_server.py`) to `127.0.0.1` only.
   Expose via reverse proxy with authentication (nginx + basic auth / OAuth2).
3. **Rate limiting**: Upstream broker APIs enforce rate limits. Never bypass
   `RiskGateway._check_rate_limit`.
4. **KillSwitch**: Always wire `KillSwitch` to pager/alerting (Discord, PagerDuty).
5. **Live trading**: Start with paper API (`kis_paper=True`). Only promote to
   live API after Walk-Forward OOS ≥ 0.5 Sharpe for 4 weeks.
6. **Audit logs**: Keep `fills/live/*.json` immutable (WORM storage) for
   regulatory compliance.

## Known Limitations

- **No MFA on dashboard**: `luxon_server.py` has no built-in auth. Use reverse
  proxy.
- **Single-user**: Current state files (`~/.luxon/`) assume single-user.
  Multi-tenant requires external state store.
- **Financial disclaimer**: See [LICENSE](LICENSE) — this is research software,
  not investment advice.
