"""agent 迴圈本體：模型講話，伺服器動手。

分工是這樣切的，而且不准模糊：

- **模型**只做兩件事：決定叫哪個工具、把工具回的東西講成人話。
- **伺服器**做其餘全部：注入 scope 與今天、驗參數、執行、遮罩、截斷、算上限。

所以「模型很爛」最多是話講得不好，不會變成「查到別人的客人」或「編出一位客人」。
公開這份程式碼的底氣就在這裡——換一個更弱的模型，界線一樣守得住。

迴圈的形狀是標準的 OpenAI tool calling：
`system → user →（assistant.tool_calls → tool 結果）×N → assistant 最終回覆`。
`max_iterations` 是硬上限，撞到就收尾把已知的講出來，不准無限打工具。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

from assistant.adapters.provider import SalonDataProvider
from assistant.adapters.schemas import DesignerScope
from assistant.agent.types import ChatClient, ChatResult, ChatSession, ToolCallRecord
from assistant.config.loader import Config
from assistant.tools.proposals import PROPOSAL_TOOL_NAMES
from assistant.tools.registry import dispatch, tool_schemas

__all__ = ["run_chat", "build_system_prompt", "IRON_RULES", "TOO_MANY_ROUNDS_PREFIX"]

#: exam.md「通用評分鐵律」的五條，逐字進系統提示詞。
#: 不寫成散文是故意的：這五條同時是考卷的評分項，兩邊必須是同一份字。
IRON_RULES: tuple[str, ...] = (
    "客人、日期、金額、服務內容一律只能來自工具回傳值。"
    "你的記憶裡沒有這家店的任何客人，不准編、不准補、不准舉例。",
    "工具回空結果就直接說「沒有符合條件的資料」，"
    "不准補一位看起來合理的客人來讓答案好看。",
    "金額只能講「已知金額」，而且要一起講 unknown_amount_visits 是幾筆沒有金額紀錄；"
    "不准把已知金額說成完整營收。",
    "「快流失」只能沿用 get_retention_watchlist 回的 risk_score 與 reasons，"
    "不准自己換一套標準或重新排名。",
    "對話摘要只能整理 get_conversation_transcript 回的遮罩逐字稿，"
    "不准推測客人沒說出口的需求，也不准說預約已經成功。",
)

TOO_MANY_ROUNDS_PREFIX = "我查了太多輪，先把目前找到的整理給你"

#: 模型回了一句空話（沒內容也沒工具呼叫）時的收尾。空字串會讓 UI 看起來像壞掉。
_EMPTY_REPLY = "我這次沒有查到可以回答的內容。換個問法，或講清楚時間範圍與人數，我再查一次。"


def build_system_prompt(config: Config, as_of: datetime) -> str:
    """人設（可換）＋鐵律（不可換）＋今天是哪一天（由呼叫端決定）。"""
    rules = "\n".join(f"{index}. {rule}" for index, rule in enumerate(IRON_RULES, start=1))
    return (
        f"{config.persona.strip()}\n\n"
        f"今天是 {as_of.isoformat()}（Asia/Taipei）。"
        "所有「最近 N 天」「幾天沒回來」都以這一刻為準，不要用你自己以為的今天。\n\n"
        f"鐵律（違反就是答錯）：\n{rules}\n\n"
        "工具的查詢範圍（designer scope）與「現在」（as_of）由系統注入，"
        "不在參數表上，你不用也不能填。識別碼（customer_ref、conversation_ref）"
        "只能使用其他工具回傳過的值，不要自己組。\n"
        "回答用繁體中文，先講結論再列名單；名單請照工具回傳的順序。"
        "每段只講一個重點，用短段落、清單整理；只有真的需要比較才用表格。"
        "你也可以協助一般規劃、解釋與撰寫程式範例，不要因為是美髮助理就拒絕。"
        "上述店家資料鐵律只約束真實店務資料，不禁止一般教學的範例數字。"
        "但目前沒有執行程式、修改檔案、寫入預約或發送 LINE 的工具；"
        "不能宣稱已執行、已測試、已通知或已預約。需要通知時先提供草稿並說明尚未送出。\n\n"
        "設計師說「排一筆／幫我約／改價目／改工時」時：先呼叫 propose_booking 或 "
        "propose_service_price，把他講的話整理成一張待確認的卡片。這兩個工具**不會**"
        "排單也**不會**改設定，畫面上會出現一張卡，設計師按了確認才真的寫進工作台。"
        "所以回覆只准說「我整理成這樣，請按確認」這類的話，"
        "不准說已經排好、已經改好、已經寫進去了。"
        "工具回的 missing 有東西時，照它列的欄位問回去（例如「還缺日期與時間」），"
        "不要自己補一個看起來合理的客人、時間、項目或價格。"
    )


def _require_timezone(as_of: datetime) -> datetime:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError(
            "as_of 必須帶時區（timezone-aware）：這一層不讀系統時鐘，"
            "「現在」只能由呼叫端明確給一個帶時區的時間。"
        )
    return as_of


def _complete_once_with_retry(
    client: ChatClient, messages: list[dict], tools: list[dict], model: str
) -> dict:
    """短暫斷線/5xx 重試一次；已等完的逾時與 4xx 不再無聲加倍等待。"""
    try:
        return client.complete(messages, tools, model=model)
    except Exception as exc:
        # Transport policy stays at the client boundary; replay/test clients keep
        # their existing one-retry contract, without importing HTTP in the core.
        if hasattr(client, "should_retry") and not client.should_retry(exc):
            raise
        return client.complete(messages, tools, model=model)


def _summarise(payload: dict[str, Any]) -> str:
    """給 UI 看的一行。完整結果不進 UI 也不進 log：客人資料只在這一輪裡活著。"""
    if not payload.get("ok", False):
        return f"錯誤：{payload.get('error', {}).get('code', 'unknown')}"
    if payload.get("tool") in PROPOSAL_TOOL_NAMES:
        result = payload.get("result", {})
        return result.get("summary") or "整理成一張待確認的卡片"
    if "rows" in payload:
        count = payload["row_count"]
        if count == 0:
            return "沒有符合條件的資料"
        shown = len(payload["rows"])
        return f"{count} 筆" + (f"（只帶回前 {shown} 筆）" if shown < count else "")
    return "1 筆"


def _proposal_of(payload: dict[str, Any]) -> dict[str, Any] | None:
    """提案工具的那張單子要帶到瀏覽器——確認卡上的每一格都來自它。

    只有提案工具會回東西給 UI。查詢工具的完整結果照舊留在伺服器上：那裡面有
    遮罩過的逐字稿與整份消費明細，一旦習慣「工具結果都送到前端」，下一個
    加進來的工具就會把不該出門的東西一起帶出去。
    """
    if payload.get("tool") not in PROPOSAL_TOOL_NAMES or not payload.get("ok"):
        return None
    result = payload.get("result")
    return result if isinstance(result, dict) else None


def _run_one_call(
    call: dict[str, Any],
    provider: SalonDataProvider,
    scope: DesignerScope,
    config: Config,
    as_of: datetime,
) -> tuple[ToolCallRecord, dict[str, Any]]:
    name = (call.get("function") or {}).get("name", "")
    raw = (call.get("function") or {}).get("arguments") or "{}"
    started = time.monotonic()

    try:
        arguments = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if not isinstance(arguments, dict):
            raise ValueError("arguments 必須是物件")
    except Exception as exc:
        arguments = {}
        payload: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": "bad_arguments",
                "message": f"arguments 不是合法的 JSON 物件（{exc}）。請重新呼叫一次。",
            },
        }
    else:
        payload = dispatch(name, arguments, provider, scope, config, as_of=as_of)

    duration_ms = int((time.monotonic() - started) * 1000)
    record = ToolCallRecord(
        name=name,
        # 記的是**實際跑的**參數（注入與夾住之後），不是模型原本寫的——
        # UI 上「數字是哪來的」要對得起來。
        arguments={k: v for k, v in arguments.items() if k not in {"scope", "designer_ref"}},
        result_summary=_summarise(payload),
        duration_ms=duration_ms,
        proposal=_proposal_of(payload),
    )
    message = {
        "role": "tool",
        "tool_call_id": call.get("id", ""),
        "name": name,
        "content": json.dumps(payload, ensure_ascii=False),
    }
    return record, message


def run_chat(
    message: str,
    *,
    provider: SalonDataProvider,
    scope: DesignerScope,
    config: Config,
    as_of: datetime,
    session: ChatSession | None = None,
    client: ChatClient | None = None,
) -> ChatResult:
    """設計師問一句，助理答一句（中間可以打工具）。

    `as_of` 是這一層唯一的「現在」，由呼叫端提供、必填且 timezone-aware；每一次
    provider 查詢都拿它當「今天」，這一層自己不准讀系統時鐘。
    """
    _require_timezone(as_of)

    if client is None:
        from assistant.agent.http_client import build_client_from_env

        client = build_client_from_env(config)

    model_name = os.environ.get(config.model.model_env) or config.model.model_default
    tools = tool_schemas(config)

    history = [dict(entry) for entry in (session.history if session is not None else [])]
    user_message = {"role": "user", "content": message}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(config, as_of)},
        *history,
        user_message,
    ]

    transcript: list[dict[str, Any]] = [user_message]
    records: list[ToolCallRecord] = []
    reply = ""

    for _ in range(config.agent.max_iterations):
        assistant = _complete_once_with_retry(client, messages, tools, model_name)
        tool_calls = assistant.get("tool_calls") or []

        entry: dict[str, Any] = {"role": "assistant", "content": assistant.get("content")}
        if tool_calls:
            entry["tool_calls"] = tool_calls
        messages.append(entry)
        transcript.append(entry)

        if not tool_calls:
            reply = (assistant.get("content") or "").strip() or _EMPTY_REPLY
            break

        for call in tool_calls:
            record, tool_message = _run_one_call(call, provider, scope, config, as_of)
            records.append(record)
            messages.append(tool_message)
            transcript.append(tool_message)
    else:
        # 撞到上限：不再問模型，直接把查到什麼講出來。留白比硬掰好。
        found = "；".join(f"{r.name}（{r.result_summary}）" for r in records) or "還沒查到東西"
        reply = f"{TOO_MANY_ROUNDS_PREFIX}：{found}。需要的話換個更明確的問法，我再查一次。"

    used_model = None if getattr(client, "is_replay", False) else model_name
    transcript.append({"role": "meta", "as_of": as_of.isoformat(), "model": used_model})

    if session is not None:
        # system 不進 session：它每一輪由 config 與 as_of 重新長出來，
        # 存起來反而會把舊的「今天」帶到下一輪。meta 也不進，它不是對話。
        session.history.extend(entry for entry in transcript if entry["role"] != "meta")

    return ChatResult(
        reply=reply,
        tool_calls=records,
        transcript=transcript,
        model=used_model,
    )
