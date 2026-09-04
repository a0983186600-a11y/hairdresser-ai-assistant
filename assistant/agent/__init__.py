"""agent 編排的入口。

`run_chat` 的簽名是第二階段兩個人的共同依據（一個寫 tool calling 迴圈、
一個寫前端伺服器），由 `tests/test_assistant_agent_contract.py` 守著——
**不准改名、不准改參數**。本體在 `assistant.agent.loop`，這裡只負責再出口一次，
讓 `from assistant.agent import run_chat` 這條路徑永遠有效。

參數為什麼長這樣：

- `provider`：資料只從 `SalonDataProvider` 來。公開版注入 Mock，正式版注入唯讀的
  那一個，agent 這一層完全一樣——「公開的不是空殼」就靠這個。
- `scope`：由登入工作階段注入。模型看得到的 tool schema 裡沒有 designer_ref，
  所以它要不到別人的客人。
- `config`：門檻、權重、人設、模板都從設定來，不是寫死在提示詞裡。
- `as_of`：這一層唯一的「現在」，由呼叫端（server／測試）傳進來，必填且要帶時區。
  `assistant/` 全域禁止 `datetime.now()`（`tests/test_assistant_open_source_hygiene.py`
  用 AST 守著），provider 每個方法又都要 `as_of`——所以它必須從這裡一路傳下去，
  不能在中間某一層偷讀系統時鐘。
- `session`：多輪對話的狀態；`None` 就是新的一輪。
- `client`：OpenAI 相容的用戶端，型別是 `ChatClient` Protocol。`None` 時依
  `config.model` 指到的環境變數自己建一個（`agent.http_client`）；測試塞假的、
  REPLAY_MODE 塞 `agent.replay.ReplayClient`——那個洞是為了「沒有金鑰也能重現一輪」開的。
"""

from __future__ import annotations

from assistant.agent.loop import IRON_RULES, build_system_prompt, run_chat
from assistant.agent.types import ChatClient, ChatResult, ChatSession, ToolCallRecord

__all__ = [
    "run_chat",
    "build_system_prompt",
    "IRON_RULES",
    "ChatClient",
    "ChatResult",
    "ChatSession",
    "ToolCallRecord",
]
