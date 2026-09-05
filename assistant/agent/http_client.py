"""OpenAI 相容 chat completions 的最小 client。

刻意**不裝 openai 套件**：這一份要跟著 `assistant/` 匯出到公開 repo，多一個相依就多一個
「clone 下來跑不動」的理由，而我們只用到一個端點、一種形狀。Qwen、Workers AI、
自架的 vLLM 都吃同一條 `POST {base_url}/chat/completions`。

端點與金鑰**只從環境變數來**，變數名寫在 `config.model.*_env`。
所以這個檔案裡沒有網址、沒有金鑰，公開它不會外洩任何東西。
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from assistant.config.loader import Config

__all__ = ["HttpChatClient", "build_client_from_env", "MissingModelCredentials"]

DEFAULT_TIMEOUT_SECONDS = 60.0


class MissingModelCredentials(RuntimeError):
    """沒有金鑰／端點。訊息只講**變數名**，不講值。"""


class HttpChatClient:
    """`ChatClient` Protocol 的正式實作：一次 chat completion，回單一 assistant message。"""

    is_replay = False

    @staticmethod
    def should_retry(error: Exception) -> bool:
        """Already timed out, auth/rate-limit/invalid input: no immediate retry."""
        if isinstance(error, httpx.TimeoutException):
            return False
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code >= 500
        return isinstance(error, httpx.TransportError)

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def complete(self, messages: list[dict], tools: list[dict], *, model: str) -> dict:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            # tool_choice=auto：要不要查工具由模型決定，但「不查就不准講數字」
            # 是提示詞那邊的鐵律，不是這裡的事。
            "tools": tools,
            "tool_choice": "auto",
            # 這是查資料不是寫詩：同一個問題兩次要給一樣的答案。
            "temperature": 0,
        }
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            json=payload,
            headers={
                "Authorization": "Bearer " + self._api_key,
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("模型端點沒有回任何 choice")
        return choices[0]["message"]


def build_client_from_env(config: Config) -> HttpChatClient:
    """依 `config.model` 指到的環境變數建一個 client。缺了就講缺哪個變數。"""
    base_url = os.environ.get(config.model.base_url_env)
    api_key = os.environ.get(config.model.api_key_env)
    missing = [
        name
        for name, value in (
            (config.model.base_url_env, base_url),
            (config.model.api_key_env, api_key),
        )
        if not value
    ]
    if missing:
        raise MissingModelCredentials(
            f"缺少環境變數 {'、'.join(missing)}；"
            "沒有金鑰也可以用 REPLAY_MODE（assistant.agent.replay.ReplayClient）跑錄好的示範。"
        )
    assert base_url is not None and api_key is not None
    return HttpChatClient(base_url, api_key)
