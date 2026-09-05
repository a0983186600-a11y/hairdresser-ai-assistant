"""模型與真資料之間唯一的門。

上面是模型（會亂填、會編、會要求看不該看的東西），下面是 `SalonDataProvider`
（正式或 Mock，同一份語意）。這一層負責四件事，缺一個公開這份程式碼都會出事：

1. **注入**：`scope`（授權）與 `as_of`（現在）由伺服器給，schema 裡根本沒有這兩格，
   模型填了也會在進門前被丟掉。
2. **夾**：`limit` 這種上下限，超界一律夾到邊界，不為這個中斷一輪
   （模型寫 999 是常態，不是攻擊）。
3. **遮**：`full_name` / `phone` 不准離開這一層，換成 `masked_name` / `phone_last4`。
   遮罩只有 `assistant.privacy` 那一份，正式與 Mock 共用。
4. **講清楚**：空結果要長得像空結果（`rows: []` ＋一句「沒有符合」），
   錯誤要附合法值讓模型改得動——否則模型會「幫忙」編一位客人（exam.md 鐵律 2）。

第 9 個工具 `draft_follow_up_message` 是**確定性**的：套 config 的模板，不呼叫模型。
回訪訊息要能被設計師預期，不是每次都換一種寫法。
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from assistant.adapters.provider import TOOL_METHOD_NAMES, SalonDataProvider
from assistant.adapters.schemas import (
    CustomerHistoryInput,
    DesignerScope,
    InactiveCustomersInput,
    RankBySpendInput,
    RecentConversationsInput,
    RetentionWatchlistInput,
    SegmentSearchInput,
    ServiceFamily,
    ServiceMetricsInput,
    TranscriptInput,
)
from assistant.config.loader import Config
from assistant.privacy import mask_name, phone_last4
from assistant.tools.proposals import (
    PROPOSAL_FIELD_DESCRIPTIONS,
    PROPOSAL_INPUTS,
    PROPOSAL_TOOL_DESCRIPTIONS,
    PROPOSAL_TOOL_NAMES,
    build_proposal,
)
from assistant.workbench import service_catalog

__all__ = [
    "PROVIDER_TOOL_NAMES",
    "DRAFT_TOOL_NAME",
    "PROPOSAL_TOOL_NAMES",
    "TOOL_NAMES",
    "INJECTED_ARGUMENTS",
    "tool_schemas",
    "dispatch",
]

PROVIDER_TOOL_NAMES: tuple[str, ...] = TOOL_METHOD_NAMES
DRAFT_TOOL_NAME = "draft_follow_up_message"
TOOL_NAMES: tuple[str, ...] = (*PROVIDER_TOOL_NAMES, DRAFT_TOOL_NAME, *PROPOSAL_TOOL_NAMES)

#: 模型永遠填不到的欄位。schema 裡沒有它們，收到了也直接丟——
#: `designer_ref` 是授權（給了就能看別人的客人），`as_of` 是「現在」
#: （給了就會用模型訓練資料裡的今天去查，考卷第一句就在講這件事）。
INJECTED_ARGUMENTS = frozenset({"scope", "designer_scope", "designer_ref", "as_of"})

_EMPTY_NOTE = "沒有符合條件的資料。這代表真的沒有，不要補一位看起來合理的客人。"

_TOOL_DESCRIPTIONS = {
    "rank_customers_by_spend": (
        "最近 N 天的消費金額排行。金額只計有金額紀錄的到店，缺金額的筆數另外回在 "
        "unknown_amount_visits，不要把 known_spend_twd 當成完整營收。"
    ),
    "list_inactive_customers": "至少 N 天沒回來的客人，沒回來越久排越前面。",
    "search_customer_segment": (
        "條件組合查詢：沒回來多久、總共來過幾次、某段期間內來幾次、做過哪些服務、"
        "最近有沒有對話。多個條件是 AND。"
    ),
    "get_customer_history": (
        "單一客人的到店明細（最近的排前面）。visit_count 與金額算的是全部，"
        "visits 只列最近 limit 筆。"
    ),
    "list_recent_conversations": "最近有動靜的 LINE 對話清單，新的排前面。要看內容再叫逐字稿。",
    "get_conversation_transcript": "單一對話的遮罩逐字稿（時間順）。摘要只能根據這裡的內容。",
    "get_retention_watchlist": (
        "快流失名單。risk_score 與 reasons 是系統用固定算法算好的，直接引用，"
        "不要自己另外算一套分數或標準。"
    ),
    "get_service_metrics": "某幾種服務在一段期間的人數／次數／已知金額，附金額涵蓋範圍說明。",
    DRAFT_TOOL_NAME: (
        "用設定好的模板幫某位客人擬一則回訪訊息（確定性，不經過模型）。"
        "回傳的 text 是可以直接送出的草稿，請原樣轉述給設計師，不要改寫。"
    ),
    **PROPOSAL_TOOL_DESCRIPTIONS,
}

_FIELD_DESCRIPTIONS = {
    "days": "往回看幾天。",
    "limit": "最多回幾筆。超過上限會自動夾到上限。",
    "inactive_days": "至少幾天沒回來。",
    "inactive_days_gte": "至少幾天沒回來。",
    "visits_gte": "累積到店次數至少幾次。",
    "visits_since": "期間起點（ISO 8601，含時區）。要用 visits_gte_in_period 時必填。",
    "visits_gte_in_period": "在 visits_since 之後至少來幾次。必須同時給 visits_since。",
    "service_families": "服務類別，只能從這個清單挑。",
    "has_recent_conversation": "只要最近有對話的客人（true）或只要沒有的（false）。",
    "customer_ref": "客人識別碼，只能用其他工具回傳過的值。",
    "customer_refs": "只看這幾位客人的對話；識別碼只能用其他工具回傳過的值。",
    "conversation_ref": "對話識別碼，只能用 list_recent_conversations 回傳過的值。",
    "message_limit": "最多回最後幾則訊息。",
    "minimum_inactive_days": "至少幾天沒回來才進名單（比系統門檻低時以系統門檻為準）。",
    "start_at": "期間起點（ISO 8601，含時區）。",
    "end_at": "期間終點（ISO 8601，含時區）。",
    **PROPOSAL_FIELD_DESCRIPTIONS,
}


class _Tool:
    """一個工具：輸入模型 ＋ provider 上的方法名。兩者的欄位名刻意一模一樣。"""

    __slots__ = ("name", "model")

    def __init__(self, name: str, model: type[BaseModel]) -> None:
        self.name = name
        self.model = model


_TOOLS: dict[str, _Tool] = {
    "rank_customers_by_spend": _Tool("rank_customers_by_spend", RankBySpendInput),
    "list_inactive_customers": _Tool("list_inactive_customers", InactiveCustomersInput),
    "search_customer_segment": _Tool("search_customer_segment", SegmentSearchInput),
    "get_customer_history": _Tool("get_customer_history", CustomerHistoryInput),
    "list_recent_conversations": _Tool("list_recent_conversations", RecentConversationsInput),
    "get_conversation_transcript": _Tool("get_conversation_transcript", TranscriptInput),
    "get_retention_watchlist": _Tool("get_retention_watchlist", RetentionWatchlistInput),
    "get_service_metrics": _Tool("get_service_metrics", ServiceMetricsInput),
}

assert tuple(_TOOLS) == PROVIDER_TOOL_NAMES, "工具順序要跟 tools.md 一致"


# --- JSON schema ---------------------------------------------------------------


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    """把 pydantic 產的 `$ref`/`$defs` 攤平，並拿掉 `title` 與 null 分支。

    OpenAI 相容端點對 `$ref` 的支援程度各家不一；攤平最不會出事，
    順便把 `anyOf: [X, null]` 收成 X（欄位可不填是靠 required 表達，不是靠 null）。
    """
    if isinstance(node, list):
        return [_inline(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        target = defs[node["$ref"].rsplit("/", 1)[-1]]
        merged = {**_inline(deepcopy(target), defs)}
        for key, value in node.items():
            if key != "$ref":
                merged[key] = _inline(value, defs)
        node = merged

    if "anyOf" in node:
        branches = [b for b in node["anyOf"] if b.get("type") != "null"]
        if len(branches) == 1:
            rest = {k: v for k, v in node.items() if k != "anyOf"}
            node = {**_inline(branches[0], defs), **rest}

    return {
        key: _inline(value, defs)
        for key, value in node.items()
        if key not in {"title", "default", "$defs"}
    }


def _parameters_for(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema()
    defs = raw.get("$defs", {})
    properties = {
        name: _inline(schema, defs)
        for name, schema in raw.get("properties", {}).items()
        if name not in INJECTED_ARGUMENTS
    }
    for name, schema in properties.items():
        if name in _FIELD_DESCRIPTIONS:
            schema["description"] = _FIELD_DESCRIPTIONS[name]
    required = [n for n in raw.get("required", []) if n not in INJECTED_ARGUMENTS]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _draft_parameters(config: Config) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "customer_ref": {
                "type": "string",
                "description": _FIELD_DESCRIPTIONS["customer_ref"],
            },
            "reason": {
                "type": "string",
                "enum": [template.id for template in config.follow_up_templates],
                "description": "要用哪一種語氣的模板。",
            },
        },
        "required": ["customer_ref", "reason"],
        "additionalProperties": False,
    }


def tool_schemas(config: Config, *, toolsmith: Any = None) -> list[dict[str, Any]]:
    """11 個工具的 OpenAI function-calling 宣告，原封不動送進端點。

    八個查詢 ＋ 一個確定性草稿 ＋ 兩個提案。提案工具跟其他九個一樣是**只讀**的：
    它們回一張待確認的單子，寫入要等設計師在卡片上按下確認。

    `toolsmith` 是**這一段對話**的工具工坊（`assistant.agent.toolsmith.Toolsmith`）。
    給了就在固定十一個後面附上「提案新工具」以及這段對話已經採用的那幾支。
    不給就是原本的十一個——所以固定清單永遠是固定的，長出來的那些只活在某一段對話裡。

    這裡刻意用鴨子型別而不是 import 那個類別：`toolsmith` 反過來要用這個模組的
    `TOOL_NAMES`（擋撞名），互相 import 會繞成一個圈。
    """
    schemas = [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": _TOOL_DESCRIPTIONS[tool.name],
                "parameters": _parameters_for(tool.model),
            },
        }
        for tool in _TOOLS.values()
    ]
    schemas.append(
        {
            "type": "function",
            "function": {
                "name": DRAFT_TOOL_NAME,
                "description": _TOOL_DESCRIPTIONS[DRAFT_TOOL_NAME],
                "parameters": _draft_parameters(config),
            },
        }
    )
    schemas.extend(
        {
            "type": "function",
            "function": {
                "name": name,
                "description": _TOOL_DESCRIPTIONS[name],
                "parameters": _parameters_for(PROPOSAL_INPUTS[name]),
            },
        }
        for name in PROPOSAL_TOOL_NAMES
    )
    if toolsmith is not None:
        schemas.extend(toolsmith.schemas())
    return schemas


# --- 遮罩與整形 ---------------------------------------------------------------

#: 電話形狀的數字串。示範資料裡的逐字稿沒有電話，但正式資料會有——
#: 遮罩要擋在同一個地方，不然「Mock 沒事」會變成「正式漏了」。
_PHONE_SHAPED = re.compile(r"\d[\d\-\s]{6,}\d")


def _redact_text(text: str) -> str:
    return _PHONE_SHAPED.sub("[已遮罩號碼]", text)


def _mask_row(row: dict[str, Any]) -> dict[str, Any]:
    """`full_name` → `masked_name`、`phone` → `phone_last4`，位置保持不變。"""
    masked: dict[str, Any] = {}
    for key, value in row.items():
        if key == "full_name":
            masked["masked_name"] = mask_name(value)
        elif key == "phone":
            masked["phone_last4"] = phone_last4(value)
        else:
            masked[key] = value
    return masked


def _rows_payload(
    name: str, rows: list[BaseModel], config: Config, clamped: dict[str, int]
) -> dict[str, Any]:
    limit = config.agent.tool_result_limit
    shown = [_mask_row(row.model_dump(mode="json")) for row in rows[:limit]]
    return {
        "ok": True,
        "tool": name,
        "row_count": len(rows),
        "rows": shown,
        "truncated": len(rows) > limit,
        "note": _EMPTY_NOTE if not rows else None,
        "clamped": clamped,
    }


def _object_payload(name: str, result: dict[str, Any], clamped: dict[str, int]) -> dict[str, Any]:
    return {"ok": True, "tool": name, "result": result, "clamped": clamped}


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **extra}}


def _not_found(what: str) -> dict[str, Any]:
    # 訊息裡不放對方傳進來的 ref：回一個「存在但不給你」的差別，等於承認它存在。
    return _error("not_found", f"找不到這{what}，可能不在你的範圍內。")


# --- 夾住超界的數字 -----------------------------------------------------------


def _clamp(arguments: dict[str, Any], parameters: dict[str, Any]) -> dict[str, int]:
    """把有上下限的整數夾回邊界，回報夾了哪些。

    模型寫 `limit: 999` 是家常便飯。為這個回一個錯誤、讓它再猜一輪，
    對設計師來說就是多等幾秒還可能猜錯——夾住就好。
    """
    clamped: dict[str, int] = {}
    for name, schema in parameters["properties"].items():
        if schema.get("type") != "integer" or name not in arguments:
            continue
        value = arguments[name]
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        low, high = schema.get("minimum"), schema.get("maximum")
        fixed = value
        if low is not None:
            fixed = max(fixed, low)
        if high is not None:
            fixed = min(fixed, high)
        if fixed != value:
            arguments[name] = fixed
            clamped[name] = fixed
    return clamped


def _validation_error(exc: ValidationError, model: type[BaseModel]) -> dict[str, Any]:
    """報錯要讓模型**改得動**：講哪個欄位、為什麼、合法值是什麼。"""
    problems = [
        {"field": ".".join(str(part) for part in item["loc"]), "why": item["msg"]}
        for item in exc.errors()
    ]
    allowed: dict[str, Any] = {}
    if "service_families" in model.model_fields:
        allowed["service_families"] = [family.value for family in ServiceFamily]
    if "service" in model.model_fields:
        allowed["service"] = [item.id for item in service_catalog()]
    return _error(
        "invalid_arguments",
        "參數不合法，請照 allowed 與 problems 改一次再呼叫。",
        problems=problems,
        allowed=allowed,
    )


# --- 第 9 個工具：確定性草稿 --------------------------------------------------


def _draft_follow_up(
    arguments: dict[str, Any],
    provider: SalonDataProvider,
    scope: DesignerScope,
    config: Config,
    as_of: datetime,
) -> dict[str, Any]:
    templates = {template.id: template for template in config.follow_up_templates}
    reason = arguments.get("reason")
    customer_ref = arguments.get("customer_ref")

    if reason not in templates:
        return _error(
            "invalid_arguments",
            "reason 必須是設定裡的模板 id。",
            problems=[{"field": "reason", "why": "不是可用的模板"}],
            allowed={"reason": list(templates)},
        )
    if not isinstance(customer_ref, str) or not customer_ref.strip():
        return _error(
            "invalid_arguments",
            "customer_ref 必須是其他工具回傳過的客人識別碼。",
            problems=[{"field": "customer_ref", "why": "缺少或不是字串"}],
            allowed={},
        )

    history = provider.get_customer_history(
        scope, customer_ref=customer_ref, as_of=as_of, limit=1
    )
    if history is None or not history.visits:
        return _not_found("位客人的到店紀錄")

    # 模板要填「上次做的是什麼」，所以只有叫得出服務名的那一筆用得上。
    # POS 消費紀錄裡有些筆看不出是哪一種服務（`service` 是空的）——
    # 那種筆不准拿來套模板：填一個看起來合理的服務進去，草稿就會替設計師
    # 對客人說一件沒發生過的事。
    last = next((visit for visit in history.visits if visit.service is not None), None)
    if last is None:
        return _error(
            "not_found",
            "這位客人最近一次消費看不出是哪一種服務，模板需要服務名稱才填得出來。",
        )

    days = (as_of - last.visited_at).days
    service = config.service_family_labels.get(last.service.value, last.service.value)
    masked = mask_name(history.full_name)
    template = templates[reason]

    return _object_payload(
        DRAFT_TOOL_NAME,
        {
            "customer_ref": history.customer_ref,
            "masked_name": masked,
            "template_id": template.id,
            "template_label": template.label,
            "service": service,
            "days_since_last_visit": days,
            "text": template.text.format(name=masked, service=service, days=days),
        },
        {},
    )


# --- 進門 ---------------------------------------------------------------------


def dispatch(
    name: str,
    arguments: dict[str, Any],
    provider: SalonDataProvider,
    scope: DesignerScope,
    config: Config,
    *,
    as_of: datetime,
) -> dict[str, Any]:
    """執行一次工具呼叫，回一個一定能 JSON 序列化的結果。

    `scope` 與 `as_of` 是伺服器注入的，不看 `arguments` 裡有沒有同名的東西——
    模型填了就丟掉。這一層不丟例外：任何錯誤都變成結構化訊息回給模型，
    讓它自己改一次參數再試（迴圈的上限在 `agent.loop` 那邊管）。
    """
    if name == DRAFT_TOOL_NAME:
        clean = {k: v for k, v in dict(arguments).items() if k not in INJECTED_ARGUMENTS}
        return _draft_follow_up(clean, provider, scope, config, as_of)

    if name in PROPOSAL_TOOL_NAMES:
        # 提案工具也走這扇門：注入照丟、參數照驗、錯誤照樣回成模型改得動的形狀。
        # 差別只有一個——它們回的是「打算做什麼」，不是「做完了」。
        clean = {k: v for k, v in dict(arguments).items() if k not in INJECTED_ARGUMENTS}
        model = PROPOSAL_INPUTS[name]
        try:
            parsed = model(**clean)
        except ValidationError as exc:
            # 這裡刻意不夾（`_clamp`）：把 999999 元靜默夾成 100000 元，
            # 就是替設計師決定了一個他沒講過的價格。
            return _validation_error(exc, model)
        except TypeError as exc:
            return _error("invalid_arguments", f"參數格式不對：{exc}", problems=[], allowed={})
        return _object_payload(
            name,
            build_proposal(name, parsed, provider=provider, scope=scope, as_of=as_of),
            {},
        )

    tool = _TOOLS.get(name)
    if tool is None:
        return _error(
            "unknown_tool",
            f"沒有 {name} 這個工具。",
            allowed={"tool": list(TOOL_NAMES)},
        )

    clean = {k: v for k, v in dict(arguments).items() if k not in INJECTED_ARGUMENTS}
    parameters = _parameters_for(tool.model)
    clamped = _clamp(clean, parameters)

    if "as_of" in tool.model.model_fields:
        clean["as_of"] = as_of

    try:
        parsed = tool.model(**clean)
    except ValidationError as exc:
        return _validation_error(exc, tool.model)
    except TypeError as exc:
        return _error("invalid_arguments", f"參數格式不對：{exc}", problems=[], allowed={})

    result = getattr(provider, name)(scope, **parsed.model_dump())

    if isinstance(result, list):
        return _rows_payload(name, result, config, clamped)
    if result is None:
        return _not_found("筆資料")

    payload = result.model_dump(mode="json")
    if name == "get_conversation_transcript":
        payload["messages"] = [
            {
                "role": message["role"],
                "created_at": message["created_at"],
                "redacted_content": _redact_text(message["content"]),
            }
            for message in payload["messages"][: config.agent.tool_result_limit]
        ]
    elif name == "get_customer_history":
        payload = _mask_row(payload)
        payload["visits"] = payload["visits"][: config.agent.tool_result_limit]

    return _object_payload(name, payload, clamped)
