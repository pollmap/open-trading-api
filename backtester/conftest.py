"""Root conftest — ensures kis_backtest is importable from any working directory."""

import sys
from pathlib import Path

# backtester/ 디렉토리를 sys.path에 추가
_backtester_root = Path(__file__).resolve().parent
if str(_backtester_root) not in sys.path:
    sys.path.insert(0, str(_backtester_root))
