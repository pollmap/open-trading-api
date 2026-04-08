"""알림 시스템

콘솔 출력 + 선택적 Discord 웹훅 알림.

설계 철학:
    - 콘솔 알림은 항상 활성 (logging 모듈 기반)
    - Discord 웹훅은 선택적 (URL 설정 시만 활성)
    - 웹훅 전송 실패가 트레이딩 로직을 멈추면 안 됨
    - 비동기 전송으로 메인 루프 블로킹 방지
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from threading import Thread
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class AlertLevel(str, Enum):
    """알림 수준"""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    KILL = "KILL"


# Discord embed 색상 매핑
_DISCORD_COLORS: Dict[AlertLevel, int] = {
    AlertLevel.INFO: 0x3498DB,
    AlertLevel.WARNING: 0xF39C12,
    AlertLevel.CRITICAL: 0xE74C3C,
    AlertLevel.KILL: 0x8B0000,
}

# 콘솔 출력용 접두사
_CONSOLE_PREFIX: Dict[AlertLevel, str] = {
    AlertLevel.INFO: "[INFO]",
    AlertLevel.WARNING: "[WARNING]",
    AlertLevel.CRITICAL: "[CRITICAL]",
    AlertLevel.KILL: "[KILL]",
}


@dataclass(frozen=True)
class AlertRecord:
    """발생한 알림의 불변 기록"""
    level: AlertLevel
    title: str
    message: str
    timestamp: datetime
    data: Optional[Dict[str, Any]] = None


class AlertSystem:
    """콘솔 + Discord 알림 시스템

    콘솔 출력은 항상 활성이며, Discord 웹훅 URL이 설정된 경우에만
    Discord로도 전송한다. 웹훅 전송은 별도 스레드에서 수행하여
    메인 트레이딩 루프를 블로킹하지 않는다.

    Usage:
        alerts = AlertSystem(discord_webhook_url="https://discord.com/api/webhooks/...")
        alerts.info("시스템 시작", "파이프라인 초기화 완료")
        alerts.dd_warning(current_dd=-7.5, threshold=-8.0)
        alerts.kill_switch_activated("DD -10% 초과")
    """

    def __init__(self, discord_webhook_url: Optional[str] = None) -> None:
        """AlertSystem 초기화

        Args:
            discord_webhook_url: Discord 웹훅 URL. None이면 콘솔만 사용.
        """
        self._webhook_url = discord_webhook_url

    @property
    def discord_enabled(self) -> bool:
        """Discord 웹훅 활성 여부"""
        return self._webhook_url is not None

    def alert(
        self,
        level: AlertLevel,
        title: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """알림 발송

        콘솔에 로그를 남기고, Discord 웹훅이 설정되어 있으면
        비동기로 웹훅 메시지를 전송한다.

        Args:
            level: 알림 수준
            title: 알림 제목
            message: 알림 본문
            data: 추가 데이터 (콘솔 로그에만 표시)
        """
        now = datetime.now()
        record = AlertRecord(
            level=level,
            title=title,
            message=message,
            timestamp=now,
            data=data,
        )

        self._log_console(record)

        if self._webhook_url is not None:
            self._send_discord_async(record)

    def info(self, title: str, message: str, **kwargs: Any) -> None:
        """INFO 수준 알림"""
        self.alert(AlertLevel.INFO, title, message, data=kwargs or None)

    def warning(self, title: str, message: str, **kwargs: Any) -> None:
        """WARNING 수준 알림"""
        self.alert(AlertLevel.WARNING, title, message, data=kwargs or None)

    def critical(self, title: str, message: str, **kwargs: Any) -> None:
        """CRITICAL 수준 알림"""
        self.alert(AlertLevel.CRITICAL, title, message, data=kwargs or None)

    def kill(self, title: str, message: str, **kwargs: Any) -> None:
        """KILL 수준 알림"""
        self.alert(AlertLevel.KILL, title, message, data=kwargs or None)

    # ── 편의 메서드 ──────────────────────────────────────────────

    def order_executed(self, trade_summary: str, amount: float) -> None:
        """주문 체결 알림

        Args:
            trade_summary: 거래 요약 (예: "삼성전자 10주 매수")
            amount: 거래 금액 (원)
        """
        self.alert(
            AlertLevel.INFO,
            "주문 체결",
            f"{trade_summary} (금액: {amount:,.0f}원)",
            data={"trade_summary": trade_summary, "amount": amount},
        )

    def dd_warning(self, current_dd: float, threshold: float) -> None:
        """드로다운 경고 알림

        Args:
            current_dd: 현재 드로다운 (%, 음수)
            threshold: 임계값 (%, 음수)
        """
        self.alert(
            AlertLevel.CRITICAL,
            "DD 경고",
            f"{current_dd:.1f}% (임계값 {threshold:.1f}%)",
            data={"current_dd": current_dd, "threshold": threshold},
        )

    def kill_switch_activated(self, reason: str) -> None:
        """킬 스위치 활성화 알림

        Args:
            reason: 킬 스위치 활성화 사유
        """
        self.alert(
            AlertLevel.KILL,
            "킬 스위치 활성화",
            reason,
        )

    # ── 내부 메서드 ──────────────────────────────────────────────

    def _log_console(self, record: AlertRecord) -> None:
        """콘솔 로그 출력

        Args:
            record: 알림 기록
        """
        timestamp = record.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        prefix = _CONSOLE_PREFIX[record.level]
        text = f"[{timestamp}] {prefix} {record.title}: {record.message}"

        if record.data:
            text += f" | data={record.data}"

        log_func = {
            AlertLevel.INFO: logger.info,
            AlertLevel.WARNING: logger.warning,
            AlertLevel.CRITICAL: logger.critical,
            AlertLevel.KILL: logger.critical,
        }[record.level]

        log_func(text)

    def _send_discord_async(self, record: AlertRecord) -> None:
        """Discord 웹훅 비동기 전송 (스레드)

        메인 루프를 블로킹하지 않기 위해 데몬 스레드로 전송.

        Args:
            record: 알림 기록
        """
        thread = Thread(
            target=self._send_discord,
            args=(record,),
            daemon=True,
        )
        thread.start()

    def _send_discord(self, record: AlertRecord) -> None:
        """Discord 웹훅 전송

        네트워크 실패 시 로그만 남기고 예외를 삼키지 않되,
        트레이딩 로직에 영향을 주지 않도록 처리한다.

        Args:
            record: 알림 기록
        """
        if self._webhook_url is None:
            return

        payload = {
            "embeds": [
                {
                    "title": f"{_CONSOLE_PREFIX[record.level]} {record.title}",
                    "description": record.message,
                    "color": _DISCORD_COLORS[record.level],
                    "timestamp": record.timestamp.isoformat(),
                    "footer": {"text": "KIS Backtest Alert"},
                }
            ]
        }

        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._webhook_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status >= 400:
                    logger.warning(
                        "Discord 웹훅 전송 실패: HTTP %d", resp.status
                    )
        except Exception:
            logger.warning(
                "Discord 웹훅 전송 실패 (네트워크 오류)", exc_info=True
            )
