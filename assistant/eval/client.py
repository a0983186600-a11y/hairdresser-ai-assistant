"""評分用的模型端點 client：跟正式那支一樣打，但**多記帳**。

`assistant/agent/http_client.py` 只回 `choices[0].message`——那是對的，agent 迴圈
不該關心計費。可是對決要比「哪個模型比較省」，就得拿到端點回的 `usage`，
所以這裡另外有一支，多做兩件事：把 `usage` 留下來、把每一次呼叫的秒數量出來。

兩個刻意的選擇：

- **用 `urllib.request`，不用 httpx。** `assistant/` 的相依只有 pydantic 與 pyyaml，
  只有伺服器與 agent 的 http client 兩個檔被開了 httpx 的例外
  （`tests/test_assistant_open_source_hygiene.py` 逐檔盯著）。評分器不值得再開一個洞，
  而一個 POST 用標準函式庫就寫得完。
- **秒數用 `time.monotonic()`，不是牆上時鐘。** `assistant/` 全域禁止讀系統時鐘；
  量「經過多久」本來也不該用會被 NTP 校正的那一支。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel, ConfigDict

from assistant.config.loader import Config

__all__ = [
    "Usage",
    "MissingModelCredentials",
    "OpenAICompatibleClient",
    "MeteredClient",
    "build_client",
    "DEFAULT_TIMEOUT_SECONDS",
    "REDACTED_KEY",
]

DEFAULT_TIMEOUT_SECONDS = 120.0

#: 金鑰被洗掉之後留在原地的字。留一個記號比整段刪掉好——
#: 看報告的人要看得出「這裡本來有東西，是我們拿掉的」。
REDACTED_KEY = "[已遮罩金鑰]"


class Usage(BaseModel):
    """一題用掉的 token 與輪數。端點沒回 usage 的話全部是 0（不是猜一個）。"""

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    rounds: int = 0


class MissingModelCredentials(RuntimeError):
    """沒有金鑰／端點。訊息只講**變數名**，不講值。"""


class OpenAICompatibleClient:
    """`ChatClient` Protocol 的實作，外加 `last_usage`。

    形狀跟 `assistant.agent.http_client.HttpChatClient` 一樣：
    `POST {base_url}/chat/completions`、`temperature=0`、`tool_choice=auto`。
    差別只有一個——回來的 `usage` 會留在 `last_usage` 上給評分器抄走。
    """

    is_replay = False

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
        self.last_usage: dict[str, Any] | None = None

    def _scrub(self, text: str) -> str:
        """把金鑰從任何要外流的字串裡拿掉。

        端點的錯誤訊息**有前例會把整個請求回吐**（連同原本送出的金鑰）。
        報告會存進 repo，所以洗掉這件事要發生在丟例外之前，不是存檔前——
        存檔前才洗的話，中間任何一次 print 或 traceback 就已經漏出去了。
        """
        if not self._api_key:
            return text
        return text.replace(self._api_key, REDACTED_KEY)

    def complete(self, messages: list[dict], tools: list[dict], *, model: str) -> dict:
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            # 評分要能重跑：同一題兩次必須是同一個答案。
            "temperature": 0,
        }
        request = urllib.request.Request(  # noqa: S310 - 端點來自環境變數，不是使用者輸入
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self._api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:400]
            # 有些端點會把你送的東西原樣回吐，所以 body 先過一次 _scrub 再進錯誤訊息。
            raise RuntimeError(self._scrub(f"端點回 HTTP {exc.code}：{body}")) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(self._scrub(f"連不上端點：{exc.reason}")) from exc

        self.last_usage = data.get("usage") or None
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("模型端點沒有回任何 choice")
        return choices[0]["message"]


class MeteredClient:
    """包住任何 `ChatClient`，量秒數、累加 usage。一題一個計數區間。

    `model` 有給就**蓋掉**呼叫端傳進來的模型名。對決一定要有這一格：
    `run_chat` 的模型名是從 `config.model.model_env` 讀的，跑對決時
    `--model qwen-turbo` 會被環境變數裡的 `QWEN_MODEL` 蓋過去——
    整張比較表就變成同一個模型跑了三次，而且看不出來。
    """

    def __init__(self, inner: Any, *, model: str | None = None) -> None:
        self.inner = inner
        self.model = model
        self._seconds = 0.0
        self._usage = Usage()

    @property
    def is_replay(self) -> bool:
        return bool(getattr(self.inner, "is_replay", False))

    def reset(self) -> None:
        self._seconds = 0.0
        self._usage = Usage()

    def seconds(self) -> float:
        return self._seconds

    def usage(self) -> Usage:
        return self._usage.model_copy()

    def complete(self, messages: list[dict], tools: list[dict], *, model: str) -> dict:
        started = time.monotonic()
        try:
            message = self.inner.complete(messages, tools, model=self.model or model)
        finally:
            self._seconds += time.monotonic() - started
        reported = getattr(self.inner, "last_usage", None) or {}
        self._usage = Usage(
            prompt_tokens=self._usage.prompt_tokens + int(reported.get("prompt_tokens", 0) or 0),
            completion_tokens=(
                self._usage.completion_tokens + int(reported.get("completion_tokens", 0) or 0)
            ),
            total_tokens=self._usage.total_tokens + int(reported.get("total_tokens", 0) or 0),
            rounds=self._usage.rounds + 1,
        )
        return message


def build_client(
    config: Config,
    *,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> MeteredClient:
    """依 `config.model` 指到的環境變數建一個會記帳的 client。缺了就講缺哪個變數。

    `model` 有給就釘死打哪個模型，不再讓 `QWEN_MODEL` 有機會插隊（見 `MeteredClient`）。
    """
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
            "沒有金鑰可以改跑 --replay（用錄好的逐字稿當 smoke test）。"
        )
    assert base_url is not None and api_key is not None
    return MeteredClient(
        OpenAICompatibleClient(base_url, api_key, timeout=timeout), model=model
    )
