"""工具工坊：助理當場寫一支新工具，跑給人看，人按了「採用」才算數。

九個固定工具答不了的問題（「上個月燙髮的客人裡有幾位是第一次來？」這種
跨表、要逐位翻紀錄的算法），助理可以**當場寫一支只讀工具**。這個檔管的是
那支工具從出生到被採用的一整條路，而且每一關都刻意留了一道人可以喊停的門：

```
模型寫程式 → check_code（AST 白名單）→ 子行程跑一次 → 卡片給人看程式碼與結果
          → 人按「採用」→ 這一段對話的工具清單多一支 → 下次可以直接叫
```

## 三條界線

1. **提案不改任何狀態。** 跑完就是一份 `{程式碼, 結果, 狀態}`，暫存在這間工坊的
   記憶體字典裡等人決定。沒有人按採用，工具清單一個字都不會變。
2. **採用只影響這一段對話。** 不寫磁碟、不進 `registry` 的固定九個、別的瀏覽器
   session 看不到。重啟服務就沒了——這是刻意的，不是還沒做完。
3. **同一個問題最多試兩次。** 模型會照著錯誤訊息改一次，這是好事；改到第三次
   通常是它在瞎猜。第三次直接擋掉，讓它老實說答不出來
   （「不准補一位看起來合理的客人」的同一條規矩，換成程式碼版本）。

## 為什麼要人按採用

沙盒擋得住「這支工具會不會弄壞東西」，擋不住「這支工具算得對不對」。
算式對不對只有設計師本人看得出來——所以程式碼要攤在卡片上，
採用是一個**人的動作**，不是模型自己完成的一步。
"""

from __future__ import annotations

import inspect
import json
import typing
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from assistant.adapters.provider import TOOL_METHOD_NAMES, SalonDataProvider
from assistant.adapters.schemas import DesignerScope, ServiceFamily
from assistant.tools.registry import TOOL_NAMES
from assistant.tools.sandbox import ENTRY_POINT, SANDBOX_LIMITS, run_in_sandbox

__all__ = [
    "PROPOSE_TOOL_NAME",
    "PREVIEW_CHARS",
    "MAX_ATTEMPTS_PER_QUESTION",
    "ProposeToolInput",
    "ToolsmithError",
    "Toolsmith",
    "ToolsmithStore",
    "provider_reference",
    "toolsmith_prompt",
]

PROPOSE_TOOL_NAME = "propose_new_tool"

#: 進模型上下文的結果預覽上限（字元）。完整結果留在卡片上給人看，
#: 模型只需要看得出「算出來像不像話」。
PREVIEW_CHARS = 2048

#: 同一個問題最多提幾次案。第一次寫、第二次照錯誤改，就這樣。
MAX_ATTEMPTS_PER_QUESTION = 2

#: 一間工坊最多記幾份提案、最多採用幾支工具。示範伺服器不接資料庫，多的丟最舊的。
MAX_PROPOSALS = 20
MAX_ADOPTED = 8

_NAME_PATTERN = r"^[a-z][a-z0-9_]{2,39}$"


class ProposeToolInput(BaseModel):
    """模型要填的四格。型別在這裡擋一次，沙盒再擋一次。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=40, pattern=_NAME_PATTERN)
    description: str = Field(min_length=2, max_length=200)
    code: str = Field(min_length=10, max_length=8000)
    question: str = Field(min_length=1, max_length=500)


class AdoptedTool(BaseModel):
    """已經被採用、這一段對話裡叫得到的一支工具。"""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    name: str
    description: str
    code: str
    question: str


class ToolsmithError(Exception):
    """採用失敗。`status` 直接給 HTTP 用，訊息是給人看的那一句。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


#: 沙盒那顆 provider 交出去之前會把這幾個欄位換掉（見 `sandbox._shape`）。模型看到的
#: 鍵名要跟它實際拿到的一樣，不然它會寫 `row['full_name']` 然後 KeyError。
#: `content` 只出現在逐字稿的訊息上，而那一層一定是遮罩過的 `redacted_content`。
_MASKED_KEYS = {
    "full_name": "masked_name",
    "phone": "phone_last4",
    "content": "redacted_content",
}


def _readable(annotation: Any) -> str:
    text = str(annotation)
    for noise, clean in (
        ("<class '", ""), ("'>", ""),
        ("collections.abc.", ""), ("datetime.datetime", "datetime"),
        ("assistant.adapters.schemas.", ""),
    ):
        text = text.replace(noise, clean)
    return text


def _keys_of(model: type[BaseModel]) -> list[str]:
    return [_MASKED_KEYS.get(name, name) for name in model.model_fields]


def _return_shape(annotation: Any) -> str:
    """把 provider 方法的回傳型別講成「你會拿到什麼形狀、有哪些鍵」。

    從 pydantic 模型長出來，所以欄位改名這裡自動跟著改——手抄一份遲早會對不上，
    而對不上的那天模型會寫出 `row['full_name']` 然後整支工具 KeyError。
    """
    args = typing.get_args(annotation)
    if typing.get_origin(annotation) is list:
        row = args[0]
        shape = f"list[dict]，每列的鍵：{'、'.join(_keys_of(row))}"
    else:
        optional = type(None) in args
        row = next((a for a in args if a is not type(None)), annotation)
        shape = ("dict 或 None（查無此人／不在你範圍內）" if optional else "dict")
        shape += f"，鍵：{'、'.join(_keys_of(row))}"

    # 巢狀那一層也要講：`visits` 與 `messages` 裡面才是真正要算的東西。
    for name, field in row.model_fields.items():
        inner = typing.get_args(field.annotation)
        if typing.get_origin(field.annotation) is list and inner:
            nested = inner[0]
            if isinstance(nested, type) and issubclass(nested, BaseModel):
                shape += f"；{name} 每筆：{'、'.join(_keys_of(nested))}"
    return shape


def provider_reference() -> str:
    """沙盒裡那顆 provider 的 8 個方法：怎麼叫、回什麼形狀、有哪些鍵。

    **不給它，它就只能猜。** 2026-09-05 實跑：沒有這一段時，模型漏掉 `as_of`、
    對著一個 list 呼叫 `.get('customers', [])`、把 `visited_at` 猜成 `visit_time`，
    兩次都失敗後老實說答不出來——守衛是對的，但它其實只是不知道門把在哪。
    這跟「客人沒講的欄位不准補預設值」是同一條規矩的另一面：要嘛給它真的，
    要嘛讓它問，就是不要讓它猜。
    """
    lines = []
    for name in TOOL_METHOD_NAMES:
        signature = inspect.signature(getattr(SalonDataProvider, name))
        parameters = [
            f"{pname}: {_readable(p.annotation)}"
            + ("" if p.default is inspect.Parameter.empty else "（可不給）")
            for pname, p in signature.parameters.items()
            if pname not in {"self", "scope"}
        ]
        lines.append(
            f"- {name}({', '.join(parameters)})"
            f" → {_return_shape(signature.return_annotation)}"
        )
    families = "、".join(family.value for family in ServiceFamily)
    return (
        "provider 的方法（一律用關鍵字參數，scope 已經注入、不要傳）：\n"
        + "\n".join(lines)
        + f"\nservice_families 只能用這幾個字串：{families}。"
        "\n日期時間欄位回的是 ISO 8601 字串（例如 2026-03-01T14:00:00+08:00），"
        "不是 datetime 物件；要算星期或月份請自己 parse。"
    )


def _tool_description() -> str:
    return (
        "現有工具答不了這個問題時，寫一支新的**只讀**小工具。"
        f"code 必須定義 def {ENTRY_POINT}(provider, as_of)，回傳 dict 或 list；"
        "provider 只有 tools.md 那 8 個方法（參數用關鍵字，不要傳 scope）；"
        "只能 import datetime／math／statistics／collections／itertools／re／decimal／json。"
        "會在沙盒跑一次並把程式碼與結果給設計師看；**採用與否由人決定，不准宣稱已經採用**。"
    )


def _propose_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": PROPOSE_TOOL_NAME,
            "description": _tool_description(),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "工具名稱，小寫英文與底線，例如 first_time_perm_customers。",
                    },
                    "description": {
                        "type": "string",
                        "description": "一句話說明這支工具算什麼，會顯示在卡片上。",
                    },
                    "code": {
                        "type": "string",
                        "description": (
                            f"完整 Python 原始碼，含 def {ENTRY_POINT}(provider, as_of)。"
                        ),
                    },
                    "question": {
                        "type": "string",
                        "description": "設計師原本問的那句話。",
                    },
                },
                "required": ["name", "description", "code", "question"],
                "additionalProperties": False,
            },
        },
    }


def toolsmith_prompt() -> str:
    """加進系統提示詞的那一段。短，因為長的模型不會照做。"""
    return (
        f"現有工具答不出來時，可以用 {PROPOSE_TOOL_NAME} 當場寫一支新工具："
        f"code 要定義 def {ENTRY_POINT}(provider, as_of) 並回傳 dict 或 list，"
        "只能呼叫 provider 上那 8 個方法（關鍵字參數，不要傳 scope 或 designer_ref），"
        "只能 import datetime／math／statistics／collections／itertools／re／decimal／json，"
        "不准 open／eval／getattr／底線開頭的屬性。只回一個 code block 該有的內容，不要解釋。\n"
        f"{provider_reference()}\n"
        "被拒或出錯時照錯誤訊息改一次再試；第二次還不行就老實說這題答不出來，"
        "不准編一個數字。工具跑出來的結果**還沒有被採用**，"
        "要由設計師按「採用」才會加進工具清單——不准說你已經採用或已經安裝。"
    )


class Toolsmith:
    """一個瀏覽器 session 的工坊：提案暫存 ＋ 已採用的工具。全部只在記憶體。"""

    def __init__(self) -> None:
        self._proposals: dict[str, dict[str, Any]] = {}
        self._adopted: dict[str, AdoptedTool] = {}
        self._attempts: dict[str, int] = {}
        self._cache: dict[str, dict[str, Any]] = {}

    # --- 工具清單 ---------------------------------------------------------------

    def schemas(self) -> list[dict[str, Any]]:
        """附在固定九個後面的宣告：提案工具 ＋ 這段對話已經採用的那幾支。"""
        extra = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": (
                        f"{tool.description}（這一段對話裡採用的工具，"
                        "每次呼叫都會重新在沙盒跑一次。）"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            }
            for tool in self._adopted.values()
        ]
        return [_propose_schema(), *extra]

    def handles(self, name: str) -> bool:
        return name == PROPOSE_TOOL_NAME or name in self._adopted

    def adopted(self) -> list[dict[str, Any]]:
        return [tool.model_dump() for tool in self._adopted.values()]

    # --- 提案 -------------------------------------------------------------------

    def run(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        scope: DesignerScope,
        as_of: datetime,
    ) -> dict[str, Any]:
        """跑一次工坊的工具。回傳形狀跟 `registry.dispatch` 一致，不丟例外。"""
        if name == PROPOSE_TOOL_NAME:
            return self._propose(arguments, scope=scope, as_of=as_of)
        tool = self._adopted.get(name)
        if tool is None:
            return {
                "ok": False,
                "error": {"code": "unknown_tool", "message": f"這段對話裡沒有 {name} 這支工具。"},
            }
        return self._run_adopted(tool, scope=scope, as_of=as_of)

    def _propose(
        self, arguments: dict[str, Any], *, scope: DesignerScope, as_of: datetime
    ) -> dict[str, Any]:
        try:
            parsed = ProposeToolInput(**arguments)
        except ValidationError as exc:
            return {
                "ok": False,
                "error": {
                    "code": "invalid_arguments",
                    "message": "提案的欄位不合法，照 problems 改一次再試。",
                    "problems": [
                        {"field": ".".join(str(part) for part in item["loc"]), "why": item["msg"]}
                        for item in exc.errors()
                    ],
                },
            }
        except TypeError as exc:
            return {
                "ok": False,
                "error": {"code": "invalid_arguments", "message": f"參數格式不對：{exc}",
                          "problems": []},
            }

        used = self._attempts.get(parsed.question, 0)
        if used >= MAX_ATTEMPTS_PER_QUESTION:
            return self._card(
                parsed,
                proposal_id=uuid.uuid4().hex,
                status="rejected",
                error={
                    "message": (
                        f"這個問題已經試過 {used} 次了，不要再寫新工具。"
                        "請直接告訴設計師這題目前答不出來。"
                    )
                },
            )
        self._attempts[parsed.question] = used + 1

        if parsed.name in TOOL_NAMES or parsed.name == PROPOSE_TOOL_NAME:
            return self._card(
                parsed,
                proposal_id=uuid.uuid4().hex,
                status="rejected",
                error={"message": f"已經有一個叫 {parsed.name} 的工具了，換一個名字。"},
            )
        if parsed.name in self._adopted:
            return self._card(
                parsed,
                proposal_id=uuid.uuid4().hex,
                status="rejected",
                error={"message": f"這段對話已經採用過 {parsed.name} 了，直接呼叫它就好。"},
            )

        outcome = run_in_sandbox(parsed.code, as_of=as_of, scope=scope)
        proposal_id = uuid.uuid4().hex

        if outcome["ok"]:
            payload = self._card(
                parsed,
                proposal_id=proposal_id,
                status="ok",
                result=outcome["result"],
                row_count=outcome["row_count"],
                truncated=outcome["truncated"],
                duration_ms=outcome["duration_ms"],
            )
        else:
            code = outcome["error"]["code"]
            payload = self._card(
                parsed,
                proposal_id=proposal_id,
                status="rejected" if code in {"forbidden_code", "syntax_error"} else "error",
                error=outcome["error"],
                duration_ms=outcome["duration_ms"],
            )

        self._remember(proposal_id, parsed, payload["result"])
        return payload

    @staticmethod
    def _table_of(result: Any) -> list[dict[str, Any]] | None:
        """卡片上那張表要畫什麼。

        模型常常回 `{"breakdown": [...], "total": 546, "note": "..."}` 這種形狀——
        真正要看的那張表包在裡面。只認**第一個**由物件組成的 list，找不到就不畫表，
        改用 `<pre>` 印原文；寧可少一張表，也不要在畫面上端出一張猜錯的表。
        """
        if isinstance(result, list):
            return result if result and isinstance(result[0], dict) else None
        if isinstance(result, dict):
            for value in result.values():
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    return value
        return None

    def _remember(self, proposal_id: str, parsed: ProposeToolInput, result: dict) -> None:
        self._proposals[proposal_id] = {"input": parsed, "status": result["status"]}
        while len(self._proposals) > MAX_PROPOSALS:
            self._proposals.pop(next(iter(self._proposals)))

    def _card(
        self,
        parsed: ProposeToolInput,
        *,
        proposal_id: str,
        status: str,
        result: Any = None,
        error: dict[str, Any] | None = None,
        row_count: int = 0,
        truncated: bool = False,
        duration_ms: int = 0,
    ) -> dict[str, Any]:
        """一份提案的完整回傳：給模型看的 result ＋ 給前端畫卡片的 card。"""
        preview = ""
        if result is not None:
            preview = json.dumps(result, ensure_ascii=False)[:PREVIEW_CHARS]

        body: dict[str, Any] = {
            "proposal_id": proposal_id,
            "name": parsed.name,
            "description": parsed.description,
            "code": parsed.code,
            "status": status,
            "result_preview": preview,
            "row_count": row_count,
            "truncated": truncated,
        }
        if error is not None:
            body["error"] = error

        # 摘要講的是**畫面上看得到的那個數字**。`row_count` 算的是 run() 回的那個
        # 東西有幾格：模型回一個包了四個鍵的 dict 時那是 4，但表上是 7 列——
        # 工具卡寫 4、它下面那張表 7 列，看的人只會覺得有一邊在說謊。
        table = self._table_of(result)
        return {
            "ok": True,
            "tool": PROPOSE_TOOL_NAME,
            "result": body,
            "summary": {
                "ok": (
                    f"跑出 {len(table)} 列，等你決定要不要採用"
                    if table
                    else "跑完了，等你決定要不要採用"
                ),
                "rejected": "沙盒拒絕了這段程式碼",
                "error": "這支工具跑出錯誤",
            }[status],
            "card": {
                "kind": "tool_proposal",
                **body,
                "rows": table,
                "duration_ms": duration_ms,
                "limits": {
                    "cpu_seconds": SANDBOX_LIMITS["cpu_seconds"],
                    "wall_seconds": SANDBOX_LIMITS["wall_seconds"],
                },
            },
        }

    # --- 採用 -------------------------------------------------------------------

    def adopt(self, proposal_id: str) -> dict[str, Any]:
        """人按了「採用」。只有跑成功的提案可以被採用。"""
        record = self._proposals.get(proposal_id)
        if record is None:
            raise ToolsmithError(404, "找不到這份提案，可能已經過期了。")
        if record["status"] != "ok":
            raise ToolsmithError(409, "這份提案沒有跑成功，不能採用。")

        parsed: ProposeToolInput = record["input"]
        if parsed.name in self._adopted:
            raise ToolsmithError(409, f"這段對話已經有一支叫 {parsed.name} 的工具了。")
        if len(self._adopted) >= MAX_ADOPTED:
            raise ToolsmithError(409, f"一段對話最多採用 {MAX_ADOPTED} 支工具。")

        tool = AdoptedTool(
            proposal_id=proposal_id,
            name=parsed.name,
            description=parsed.description,
            code=parsed.code,
            question=parsed.question,
        )
        self._adopted[tool.name] = tool
        return tool.model_dump()

    # --- 再用 -------------------------------------------------------------------

    def _run_adopted(
        self, tool: AdoptedTool, *, scope: DesignerScope, as_of: datetime
    ) -> dict[str, Any]:
        """採用過的工具每次都重跑沙盒；同一段對話裡同一個「現在」才吃快取。"""
        key = f"{tool.name}@{as_of.isoformat()}"
        outcome = self._cache.get(key)
        if outcome is None:
            outcome = run_in_sandbox(tool.code, as_of=as_of, scope=scope)
            self._cache[key] = outcome

        if not outcome["ok"]:
            return {"ok": False, "error": outcome["error"]}

        rows = outcome["result"]
        return {
            "ok": True,
            "tool": tool.name,
            "result": {
                "rows": rows,
                "row_count": outcome["row_count"],
                "truncated": outcome["truncated"],
                "note": "這是這段對話裡採用的工具算出來的，不是固定工具。",
            },
            "summary": f"{outcome['row_count']} 筆（採用的工具）",
        }


class ToolsmithStore:
    """一台伺服器上所有工坊。鍵是對話 session_id，提案 id 是全域唯一的。"""

    def __init__(self, limit: int = 200) -> None:
        self._by_session: dict[str, Toolsmith] = {}
        self._limit = limit

    def acquire(self, session_id: str | None) -> Toolsmith:
        """拿這段對話的工坊。第一句話還沒有 session_id，就先給一間沒掛號的。"""
        if session_id is None:
            return Toolsmith()
        if session_id not in self._by_session:
            self.bind(session_id, Toolsmith())
        return self._by_session[session_id]

    def peek(self, session_id: str | None) -> Toolsmith | None:
        """看一眼，**不建**。列清單的 GET 走這裡：任何人隨手打一個 id 都建一間工坊，
        就等於開了一條用 GET 把記憶體撐大的路。"""
        if session_id is None:
            return None
        return self._by_session.get(session_id)

    def bind(self, session_id: str, workshop: Toolsmith) -> None:
        self._by_session.setdefault(session_id, workshop)
        while len(self._by_session) > self._limit:
            self._by_session.pop(next(iter(self._by_session)))

    def owner_of(self, proposal_id: str) -> Toolsmith | None:
        """哪一間工坊收著這份提案。採用路由只拿得到 proposal_id，所以要問這裡。"""
        for workshop in self._by_session.values():
            if proposal_id in workshop._proposals:  # noqa: SLF001 - 同一個模組的自家零件
                return workshop
        return None
