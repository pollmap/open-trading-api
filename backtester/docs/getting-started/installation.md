# Installation

## Requirements

- Python **3.11+** (3.12 supported)
- pip or uv
- (Optional) Docker Desktop for QuantConnect Lean backtest engine
- (Optional) MCP server for extended data tools

## Standard install

```bash
pip install luxon-terminal
```

## With extras

```bash
pip install "luxon-terminal[exchange]"   # WebSocket + crypto libs
pip install "luxon-terminal[viz]"        # Dashboard (FastAPI + Plotly)
pip install "luxon-terminal[mcp]"        # MCP integration
pip install "luxon-terminal[all]"        # everything
pip install "luxon-terminal[dev]"        # pytest + ruff + mypy + bandit
```

## From source

```bash
git clone https://github.com/pollmap/luxon-terminal.git
cd luxon-terminal/backtester
pip install -e ".[dev]"
pytest tests/ -q
```

## Verify

```bash
luxon-run --version    # 1.0.0
luxon-wf --version     # 1.0.0

python -c "from kis_backtest.luxon import LuxonTerminal, __version__; print(__version__)"
```

## Broker-specific setup

=== "KIS (Korea)"

    ```bash
    # 1. Apply for KIS Open API
    #    https://apiportal.koreainvestment.com/
    # 2. Create config
    mkdir -p ~/KIS/config
    cp kis_devlp.yaml.template ~/KIS/config/kis_devlp.yaml
    # 3. Edit with your app key, secret, account number, HTS ID
    ```

=== "Alpaca (US)"

    ```bash
    # 1. Sign up at https://alpaca.markets/
    # 2. Paper trading keys from dashboard
    # 3. Install optional dep
    pip install alpaca-py
    # 4. Set env
    export ALPACA_API_KEY="..."
    export ALPACA_API_SECRET="..."
    export ALPACA_PAPER=true
    ```

=== "IBKR (v1.2)"

    Interactive Brokers provider is planned for v1.2.
    Track progress: [issue tracker](https://github.com/pollmap/luxon-terminal/issues).
