"""REPLAY_MODE：沒有金鑰也能跑完一輪 demo。

**工具會重跑，但最終回覆也是錄音。** 錄下工具呼叫和模型最終文字，沒有存工具
結果。重播時工具摘要是本次查詢結果，最終文字卻不會隨資料改變；所以出貨錄音
只用於固定 seed、固定設計師和固定錨點的示範。換資料／時間／設計師或接實際資料
時必須關閉 REPLAY_MODE 並使用即時模型，不能把錄音當成即時查詢答案。

代價很誠實：回放只認得**錄過的那幾句話**。沒錄過的問題會拿到一句
「這句話沒有錄音」，不會裝作答得出來。

（`record()` 不讀系統時鐘——`assistant/` 全域禁止。`recorded_at` 由錄音腳本從外面傳。）
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from assistant.agent.types import ChatResult

__all__ = [
    "REPLAY_DIR",
    "NO_RECORDING_REPLY",
    "normalize_message",
    "record",
    "load_recordings",
    "resolve_replay_dir",
    "ReplayClient",
]

#: 出貨的錄音放這裡（`assistant/replay/`），跟著公開 repo 一起走。
REPLAY_DIR = Path(__file__).resolve().parents[1] / "replay"

NO_RECORDING_REPLY = "這句話沒有錄音，設定模型金鑰後可即時回答"

_WHITESPACE = re.compile(r"\s+")


def normalize_message(text: str | None) -> str:
    """比對用的鍵：NFKC 正規化、去掉所有空白、casefold。

    設計師打字時全形半形、有沒有空格都不該影響到「這句有沒有錄音」。
    """
    folded = unicodedata.normalize("NFKC", text or "")
    return _WHITESPACE.sub("", folded).casefold()


def record(
    result: ChatResult,
    directory: Path | str,
    slug: str,
    *,
    recorded_at: datetime | None = None,
) -> Path:
    """存工具呼叫與模型文字，不存工具結果；文字仍可能含資料，只能錄假資料。"""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)

    user_message = next(
        (entry.get("content", "") for entry in result.transcript if entry.get("role") == "user"),
        "",
    )
    meta = next((entry for entry in result.transcript if entry.get("role") == "meta"), {})
    rounds = [
        {"content": entry.get("content"), "tool_calls": entry.get("tool_calls")}
        for entry in result.transcript
        if entry.get("role") == "assistant"
    ]

    payload = {
        "slug": slug,
        "user_message": user_message,
        "normalized": normalize_message(user_message),
        "model": result.model,
        # 回放時要用同一個「今天」，工具算出來的數字才跟錄音當時講的話對得上。
        "as_of": meta.get("as_of"),
        "recorded_at": recorded_at.isoformat() if recorded_at is not None else None,
        "rounds": rounds,
    }

    path = target / f"{slug}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_recordings(directory: Path | str) -> dict[str, dict[str, Any]]:
    """讀一整個資料夾的錄音，鍵是正規化後的問句。資料夾不存在就是沒有錄音。"""
    target = resolve_replay_dir(directory)
    if not target.is_dir():
        return {}
    recordings: dict[str, dict[str, Any]] = {}
    for path in sorted(target.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        key = payload.get("normalized") or normalize_message(payload.get("user_message"))
        recordings[key] = payload
    return recordings


def resolve_replay_dir(directory: Path | str) -> Path:
    """相對錄音路徑從套件找；保留既有 assistant/replay 設定，絕對路徑照用。

    不能用 cwd：pip 安裝後，評審可能在任何目錄啟動。自訂相對路徑也以
    assistant 套件為基準；不再因 shell 所在位置而靜默變成另一份錄音。
    """
    path = Path(directory)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "assistant":
        path = Path(*path.parts[1:])
    return Path(__file__).resolve().parents[1] / path


def _last_user_message(messages: list[dict]) -> str:
    for entry in reversed(messages):
        if entry.get("role") == "user":
            return entry.get("content") or ""
    return ""


def _rounds_already_played(messages: list[dict]) -> int:
    """這一輪問答裡，模型已經講過幾次話（決定要放錄音的第幾段）。"""
    last_user = 0
    for index, entry in enumerate(messages):
        if entry.get("role") == "user":
            last_user = index
    return sum(1 for entry in messages[last_user:] if entry.get("role") == "assistant")


class ReplayClient:
    """`ChatClient` Protocol 的錄音實作。`run_chat(client=ReplayClient(...))` 就是 REPLAY_MODE。"""

    #: 迴圈看到這個就把 `ChatResult.model` 記成 None——這一輪沒有連模型。
    is_replay = True

    def __init__(self, directory: Path | str = REPLAY_DIR) -> None:
        self.directory = resolve_replay_dir(directory)
        self._recordings = load_recordings(self.directory)

    @property
    def recording_count(self) -> int:
        return len(self._recordings)

    def complete(self, messages: list[dict], tools: list[dict], *, model: str) -> dict:
        recording = self._recordings.get(normalize_message(_last_user_message(messages)))
        if recording is None:
            return {"role": "assistant", "content": NO_RECORDING_REPLY, "tool_calls": None}

        rounds = recording.get("rounds") or []
        index = _rounds_already_played(messages)
        if index >= len(rounds):
            # 錄音放完了還被問一次（工具比錄音當時多回了一輪之類）：
            # 用最後一句收尾，不要讓迴圈空轉到上限。
            tail = rounds[-1].get("content") if rounds else None
            return {"role": "assistant", "content": tail or NO_RECORDING_REPLY, "tool_calls": None}

        step = rounds[index]
        return {
            "role": "assistant",
            "content": step.get("content"),
            "tool_calls": step.get("tool_calls"),
        }
