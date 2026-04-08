"""파일 기반 킬 스위치

~/kis_kill_switch.lock 파일 존재 시 모든 주문 즉시 중단.

설계 철학:
    - DB/API 의존 없음 → 네트워크 장애 시에도 작동
    - 어떤 환경에서든 활성화 가능:
        - VPS: touch ~/kis_kill_switch.lock
        - WSL: touch ~/kis_kill_switch.lock
        - Windows: echo "emergency" > %USERPROFILE%\\kis_kill_switch.lock
    - 에이전트(HERMES/NEXUS/DOGE) 누구든 즉시 활성화 가능
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

LOCK_FILE = Path.home() / "kis_kill_switch.lock"


class KillSwitch:
    """파일 기반 긴급 정지 스위치"""

    def __init__(self, lock_path: Path = LOCK_FILE):
        self._lock_path = lock_path

    @property
    def is_active(self) -> bool:
        """킬 스위치 활성 여부"""
        return self._lock_path.exists()

    def activate(self, reason: str) -> None:
        """킬 스위치 활성화

        Args:
            reason: 활성화 사유 (로그 + 파일에 기록)
        """
        timestamp = datetime.now().isoformat()
        content = f"{timestamp}: {reason}\n"
        self._lock_path.write_text(content, encoding="utf-8")
        logger.critical(f"KILL SWITCH ACTIVATED: {reason}")

    def deactivate(self) -> None:
        """킬 스위치 해제"""
        if self._lock_path.exists():
            reason = self._lock_path.read_text(encoding="utf-8").strip()
            self._lock_path.unlink()
            logger.warning(f"킬 스위치 해제 (이전 사유: {reason})")
        else:
            logger.info("킬 스위치가 이미 비활성 상태")

    def check_or_raise(self) -> None:
        """킬 스위치 활성 시 예외 발생"""
        if self.is_active:
            reason = self._lock_path.read_text(encoding="utf-8").strip()
            raise KillSwitchActiveError(
                f"킬 스위치 활성 상태 — 모든 주문 중단. 사유: {reason}"
            )

    @property
    def reason(self) -> str:
        """현재 킬 스위치 사유 (비활성 시 빈 문자열)"""
        if not self.is_active:
            return ""
        return self._lock_path.read_text(encoding="utf-8").strip()


class KillSwitchActiveError(Exception):
    """킬 스위치 활성 상태 예외"""
    pass
