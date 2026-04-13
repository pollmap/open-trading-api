"""OpenCode HTTP 서버 클라이언트.

opencode serve (default :4870)를 Python에서 호출하는 래퍼.
기존 router.py와 별도 — OpenCode는 세션 기반 + tool/MCP 자동, 대화형.

Usage:
    client = OpenCodeClient()
    client.ensure_server()          # serve 백그라운드 기동
    sid = client.new_session()
    reply = client.ask(sid, "backtester/ 구조 요약해줘")
    print(reply)
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_URL = os.getenv("OPENCODE_URL", "http://127.0.0.1:4870")
DEFAULT_PROJECT_ID = "9711647965b7a752535f8a100e03f24dc457f1ff"  # open-trading-api
DEFAULT_MODEL = {"providerID": "ollama", "modelID": "qwen3:14b"}


@dataclass
class OpenCodeClient:
    base_url: str = DEFAULT_URL
    timeout: float = 600.0

    def health(self) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/global/health", timeout=3.0)
            return r.status_code == 200 and r.json().get("healthy") is True
        except Exception:
            return False

    def ensure_server(self, port: int = 4870) -> None:
        """서버 없으면 백그라운드 기동 (Windows)."""
        if self.health():
            return
        logger.info("opencode serve 기동")
        subprocess.Popen(
            ["opencode.cmd", "serve", "--port", str(port), "--hostname", "127.0.0.1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        for _ in range(30):
            time.sleep(1)
            if self.health():
                return
        raise RuntimeError("opencode serve 기동 실패")

    def new_session(self, project_id: str = DEFAULT_PROJECT_ID) -> str:
        r = httpx.post(
            f"{self.base_url}/session",
            json={"projectID": project_id},
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()["id"]

    def list_sessions(self) -> list[dict]:
        r = httpx.get(f"{self.base_url}/session", timeout=5.0)
        r.raise_for_status()
        return r.json()

    def _post_message(self, session_id: str, text: str, model: Optional[dict] = None) -> None:
        """POST는 assistant 응답 완성까지 스트리밍 block. 짧은 connect 후 ReadTimeout 정상."""
        payload = {
            "parts": [{"type": "text", "text": text}],
            "model": model or DEFAULT_MODEL,
        }
        try:
            httpx.post(
                f"{self.base_url}/session/{session_id}/message",
                json=payload,
                timeout=httpx.Timeout(connect=5.0, read=2.0, write=5.0, pool=5.0),
            )
        except httpx.ReadTimeout:
            # 예상 동작 — 메시지 전송은 완료, 생성은 비동기
            pass

    def _fetch_messages(self, session_id: str) -> list[dict]:
        r = httpx.get(f"{self.base_url}/session/{session_id}/message", timeout=10.0)
        r.raise_for_status()
        return r.json()

    def ask(
        self,
        session_id: str,
        text: str,
        *,
        model: Optional[dict] = None,
        poll_interval: float = 2.0,
        max_wait: float = 300.0,
    ) -> str:
        """메시지 → 응답 완성까지 폴링 → assistant text 반환."""
        self._post_message(session_id, text, model=model)

        deadline = time.time() + max_wait
        last_assistant_id: Optional[str] = None
        last_text = ""

        while time.time() < deadline:
            msgs = self._fetch_messages(session_id)
            # 최신 assistant 메시지 찾기
            for m in reversed(msgs):
                info = m.get("info", {})
                if info.get("role") != "assistant":
                    continue
                parts = m.get("parts", [])
                texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
                combined = "".join(texts).strip()
                # 완료 신호: 시간이 "completed" 있거나 토큰이 > 0
                done = info.get("time", {}).get("completed") is not None
                tokens_out = info.get("tokens", {}).get("output", 0)
                if combined:
                    last_text = combined
                    last_assistant_id = info.get("id")
                if done and combined:
                    return combined
                break
            time.sleep(poll_interval)

        if last_text:
            logger.warning("타임아웃이지만 부분 응답 반환")
            return last_text
        raise TimeoutError(f"{max_wait}s 내 응답 없음")


def ask_oneshot(prompt: str, *, model: Optional[dict] = None) -> str:
    """편의: 1회 질의 + 세션 폐기."""
    client = OpenCodeClient()
    client.ensure_server()
    sid = client.new_session()
    return client.ask(sid, prompt, model=model)
