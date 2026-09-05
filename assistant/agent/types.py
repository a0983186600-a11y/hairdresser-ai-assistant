"""agent 迴圈的資料型別。

第二階段有兩個人同時動工：一個寫 tool calling 迴圈，一個寫前端伺服器。
兩邊靠這幾個型別與 `run_chat` 的簽名對接，所以這裡先定好、不再改名。

`transcript` 刻意是 `list[dict]` 而不是強型別：它要原封不動地送進 OpenAI 相容端點，
也要能存成 REPLAY 逐字稿；中間多一層轉換只會在兩種格式之間掉東西。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ChatClient", "ChatSession", "ToolCallRecord", "ChatResult"]


@runtime_checkable
class ChatClient(Protocol):
    """對模型端點的最小介面：一次 chat completion。

    第二階段一個人寫迴圈、一個人寫伺服器，兩邊都要能塞一個 `client` 進 `run_chat`
    （正式版包真的金鑰、測試與 REPLAY_MODE 塞假的）。這個 Protocol 就是那個插座的
    形狀——只定義、不實作。

    - `messages`：OpenAI chat 格式的訊息陣列（system／user／assistant／tool），
      原封不動送進端點。
    - `tools`：OpenAI function-calling 格式的工具宣告陣列。
    - `model`（keyword-only）：要打哪個模型；呼叫端一定要指名，不從 `config` 偷猜。

    回傳單一 choice 的 assistant message，OpenAI chat completions 形狀：
    `{"role": "assistant", "content": str | None, "tool_calls": [...] | None}`。
    沒有工具呼叫時 `tool_calls` 是 `None`，`content` 是助理的話。
    """

    def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        model: str,
    ) -> dict: ...


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class ChatSession(BaseModel):
    """一輪多問的對話狀態。前端每次帶回來，agent 接著往下講。"""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    session_id: str
    history: list[dict[str, Any]] = Field(default_factory=list)


class ToolCallRecord(_Base):
    """一次工具呼叫。UI 會把這些攤在答案下面——「數字是哪來的」要看得見。"""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    #: 給人看的一行摘要（幾筆、最高分多少），不是完整結果。
    #: 完整結果不進 UI 也不進 log：逐字稿與客人資料只在遮罩後短暫存在於這一輪。
    result_summary: str = ""
    duration_ms: int = 0
    #: **只有提案工具會填**：一張「打算做什麼」的單子（欄位、還缺什麼、
    #: 按下確認要送的 payload）。它非帶到瀏覽器不可——確認卡上那幾格就是它。
    #: 裡面只有遮罩過的姓名與識別碼，跟查詢工具的完整結果不同，那個一樣不出門。
    proposal: dict[str, Any] | None = None
    #: **只有 `propose_new_tool` 會填**：模型當場寫的那支工具（名字、說明、原始碼、
    #: 沙盒跑出來的前幾列、狀態）。它非帶到瀏覽器不可——「採用」是人的動作，
    #: 而人要先看得到程式碼跟結果才決定得了。裡面的列已經過沙盒那層遮罩。
    tool_proposal: dict[str, Any] | None = None


class ChatResult(_Base):
    """`run_chat` 的回傳。`model` 是 None 代表這一輪沒有連模型（REPLAY_MODE）。"""

    reply: str
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    model: str | None = None
