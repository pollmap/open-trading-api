"""OpenCode HTTP 클라이언트 단위 테스트 (모킹)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from kis_backtest.luxon.intelligence.opencode_client import OpenCodeClient


def _mock_response(status=200, data=None):
    m = MagicMock()
    m.status_code = status
    m.raise_for_status = MagicMock()
    m.json = MagicMock(return_value=data)
    return m


@patch("httpx.get")
def test_health_returns_true_on_200(mock_get):
    mock_get.return_value = _mock_response(200, {"healthy": True, "version": "1.4.3"})
    assert OpenCodeClient().health() is True


@patch("httpx.get")
def test_health_returns_false_on_error(mock_get):
    mock_get.side_effect = httpx.ConnectError("refused")
    assert OpenCodeClient().health() is False


@patch("httpx.post")
def test_new_session_returns_id(mock_post):
    mock_post.return_value = _mock_response(200, {"id": "ses_test123"})
    sid = OpenCodeClient().new_session()
    assert sid == "ses_test123"


@patch("httpx.get")
@patch("httpx.post")
def test_ask_returns_completed_assistant_text(mock_post, mock_get):
    mock_post.return_value = _mock_response(200)
    mock_get.return_value = _mock_response(200, [
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "hi"}]},
        {
            "info": {"role": "assistant", "id": "msg_1", "time": {"completed": 123}, "tokens": {"output": 5}},
            "parts": [{"type": "text", "text": "hello back"}],
        },
    ])
    reply = OpenCodeClient().ask("ses_x", "hi", poll_interval=0.01, max_wait=2)
    assert "hello" in reply


@patch("httpx.get")
@patch("httpx.post")
def test_ask_timeout_raises(mock_post, mock_get):
    mock_post.return_value = _mock_response(200)
    mock_get.return_value = _mock_response(200, [])
    with pytest.raises(TimeoutError):
        OpenCodeClient().ask("ses_x", "hi", poll_interval=0.01, max_wait=0.1)
