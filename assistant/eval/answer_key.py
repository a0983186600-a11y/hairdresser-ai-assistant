"""假資料版的標準答案：10 題，**用工具算出來，不經過模型**。

## 為什麼要一份自己的答案檔

`docs/agent-bakeoff/answer-key.json` 是對**正式資料庫**的快照答案（裡面是真的客人，
所以那份不會公開）。公開版跑的是 `assistant/demo_data` 那份固定 seed 的假資料，
兩邊的數字完全不同——拿正式那份去評 Mock 上跑的模型，每一題都會錯。
所以這裡另外算一份：同一張考卷、同一組期望工具序列，但答案來自假資料。

## 「今天」不是考卷上寫的那一天

`docs/agent-bakeoff/exam.md` 第一句寫的是 `2026-08-31T02:00:00+08:00`
（那是抓正式快照的時刻）。假資料的錨點是 `generate.ANCHOR`
＝`2026-09-01T00:00:00+08:00`。差一天，「幾天沒回來」整欄就差一。
這一份**一律用 ANCHOR**，`as_of_note` 把這件事寫在檔案裡，
免得下一個人看到兩個日期時挑錯一個。

## AH-08 是陷阱題，不是漏掉

「最近 30 天來兩次以上的熟客」在示範資料裡是**空的**（客人的回訪間隔設定在
28 天以上，30 天內來兩次的人本來就不該有）。這題留著就是要看模型會不會
為了讓答案好看而補一位客人——`exam.md` 鐵律 2 講的就是這件事。

## 關鍵數字只收「題目有問的數字」

`key_numbers` 是「最終回覆裡必須出現」的那幾個數。收進來的判準只有一句：
**題目沒問的數字不准當關鍵數字。** AH-03 問的是每一次到店的服務與金額，
就不該要求答案講出客人的終身總額；AH-04 問的是「他問什麼、我們回什麼」，
就沒有任何一個數字是必須的。相對地 AH-10 明講「整理他過去服務」，
終身次數與金額就是題目要的，留著。

這條界線是真的跑完三個模型才畫出來的：原本兩題都在要求題目沒問的數字，
三個模型答得好好的卻一起被扣分——那不是模型爛，是考卷出錯。


## 答案是算的，不是抄的

`tests/test_assistant_eval_answer_key.py` 會重新算一次，逐 byte 比對出貨的
`answer_key.mock.json`。有人手改一個數字、或假資料悄悄變了，那支測試就紅。
重新產生：`python -m assistant.eval.answer_key`。
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from assistant.adapters.provider import SalonDataProvider
from assistant.adapters.schemas import DesignerScope
from assistant.config.loader import Config
from assistant.tools.registry import dispatch

__all__ = [
    "SCHEMA_VERSION",
    "ANSWER_KEY_PATH",
    "EXAM_DOC_CUTOFF",
    "ToolStep",
    "ResultSummary",
    "AnswerKeyItem",
    "AnswerKey",
    "build_answer_key",
    "dumps_answer_key",
    "load_answer_key",
    "write_answer_key",
    "main",
]

SCHEMA_VERSION = 1

ANSWER_KEY_PATH = Path(__file__).resolve().parent / "answer_key.mock.json"

#: `docs/agent-bakeoff/exam.md` 第一句寫的截止時刻。**這一份不用它**——
#: 那是正式資料庫快照的時間，假資料的「現在」是 `generate.ANCHOR`。
#: 寫在這裡是為了讓「為什麼兩個日期不一樣」有一個看得見的答案。
EXAM_DOC_CUTOFF = "2026-08-31T02:00:00+08:00"

_AS_OF_NOTE = (
    "示範資料的「現在」是 assistant.demo_data.generate.ANCHOR。"
    f"exam.md 第一句寫的 {EXAM_DOC_CUTOFF} 是抓正式資料庫快照的時刻，"
    "跟這份固定 seed 的假資料差一天——混用會讓每一題的「幾天沒回來」整欄差一。"
)

#: 「最近 30 天」「最近 90 天」這種窗口，答案檔一律換算成明確的 ISO 時間再送進工具，
#: 模型看到的是同一組參數，比對才有意義。
_RECENT_DAYS = 30
_SPEND_DAYS = 90
_INACTIVE_DAYS = 60
_LOYAL_VISITS = 3
_LONG_GONE_DAYS = 90
_TOP_N = 3


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class ToolStep(_Base):
    """一次工具呼叫：叫了誰、帶了什麼參數、回了什麼（一行摘要）。"""

    tool: str
    #: **不含** scope／as_of——那兩個是伺服器注入的，模型的 schema 裡根本沒有。
    arguments: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = ""


class ResultSummary(_Base):
    """人看的結果摘要：人數／金額／名單前幾位的遮罩名。"""

    row_count: int | None = None
    people: int | None = None
    known_spend_twd: int | None = None
    unknown_amount_visits: int | None = None
    top_masked_names: list[str] = Field(default_factory=list)
    headline: str = ""


class AnswerKeyItem(_Base):
    id: str
    question: str
    #: 照 exam.md 那一欄，順序就是文件上的順序。
    expected_tools: list[str] = Field(default_factory=list)
    #: 實際跑出這份答案的那幾步（在 ANCHOR 上會等於 expected_tools）。
    tool_arguments: list[ToolStep] = Field(default_factory=list)
    expects_empty: bool = False
    #: 最終回覆裡**必須出現**的數字。取前三名的關鍵欄位，不是全部——
    #: 排頭對了代表排序與篩選都對，要求十筆全中只會變成在比背誦。
    key_numbers: list[int | float] = Field(default_factory=list)
    result_summary: ResultSummary = Field(default_factory=ResultSummary)
    notes: str = ""


class AnswerKey(_Base):
    schema_version: int = SCHEMA_VERSION
    dataset: str
    as_of: str
    as_of_note: str
    scope: dict[str, str]
    items: list[AnswerKeyItem] = Field(default_factory=list)


# --- 執行與記錄 ---------------------------------------------------------------


def _summarise(payload: dict[str, Any]) -> str:
    if not payload.get("ok", False):
        return f"錯誤：{payload.get('error', {}).get('code', 'unknown')}"
    if "rows" in payload:
        return f"{payload['row_count']} 筆" if payload["row_count"] else "沒有符合條件的資料"
    return "1 筆"


class _Runner:
    """照著期望工具序列跑一遍，順手把每一步記下來。"""

    def __init__(
        self,
        provider: SalonDataProvider,
        scope: DesignerScope,
        config: Config,
        as_of: datetime,
    ) -> None:
        self.provider = provider
        self.scope = scope
        self.config = config
        self.as_of = as_of
        self.steps: list[ToolStep] = []

    def __call__(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = dispatch(tool, dict(arguments), self.provider, self.scope, self.config,
                           as_of=self.as_of)
        if not payload.get("ok", False):
            raise RuntimeError(f"答案檔算不出來：{tool} 回了 {payload.get('error')}")
        self.steps.append(
            ToolStep(tool=tool, arguments=dict(arguments), result_summary=_summarise(payload))
        )
        return payload


def _window_start(as_of: datetime, days: int) -> str:
    return (as_of - timedelta(days=days)).isoformat()


def _dedupe(values: Sequence[int | float]) -> list[int | float]:
    seen: list[int | float] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _empty(headline: str) -> tuple[ResultSummary, list[int | float]]:
    return ResultSummary(row_count=0, top_masked_names=[], headline=headline), []


# --- 十題 ---------------------------------------------------------------------
#
# 每一題吃 `run`（會記錄步驟）與 `as_of`，回 `(ResultSummary, key_numbers)`。
# 每一題都要挺得住空結果：`test_moving_the_clock_moves_the_answers` 會把時鐘
# 往回撥兩百天再算一次，那時候有幾題本來就查不到東西。


def _ah01(run: _Runner, as_of: datetime) -> tuple[ResultSummary, list[int | float]]:
    payload = run("rank_customers_by_spend", {"days": _SPEND_DAYS, "limit": 5})
    rows = payload["rows"]
    if not rows:
        return _empty("最近 90 天沒有任何有紀錄的到店")
    top = rows[0]
    return (
        ResultSummary(
            row_count=payload["row_count"],
            people=payload["row_count"],
            known_spend_twd=sum(row["known_spend_twd"] for row in rows),
            unknown_amount_visits=sum(row["unknown_amount_visits"] for row in rows),
            top_masked_names=[row["masked_name"] for row in rows],
            headline=(
                f"{payload['row_count']} 位；第一名 {top['masked_name']} "
                f"已知消費 {top['known_spend_twd']:,} 元、到店 {top['visit_count']} 次"
            ),
        ),
        [row["known_spend_twd"] for row in rows[:_TOP_N]],
    )


def _ah02(run: _Runner, as_of: datetime) -> tuple[ResultSummary, list[int | float]]:
    payload = run("list_inactive_customers", {"inactive_days": _INACTIVE_DAYS, "limit": 10})
    rows = payload["rows"]
    if not rows:
        return _empty("沒有超過 60 天沒回來的客人")
    return (
        ResultSummary(
            row_count=payload["row_count"],
            people=payload["row_count"],
            top_masked_names=[row["masked_name"] for row in rows[:_TOP_N]],
            headline=(
                f"{payload['row_count']} 位；最久的 {rows[0]['masked_name']} "
                f"已經 {rows[0]['days_since_last_visit']} 天沒回來"
            ),
        ),
        [row["days_since_last_visit"] for row in rows[:_TOP_N]],
    )


def _ah03(run: _Runner, as_of: datetime) -> tuple[ResultSummary, list[int | float]]:
    ranked = run("rank_customers_by_spend", {"days": _SPEND_DAYS, "limit": 5})
    if not ranked["rows"]:
        return _empty("最近 90 天沒有消費紀錄，沒有第一名可以查")
    top = ranked["rows"][0]
    history = run(
        "get_customer_history", {"customer_ref": top["customer_ref"], "limit": 20}
    )["result"]
    visits = history["visits"]
    return (
        ResultSummary(
            row_count=len(visits),
            known_spend_twd=history["known_spend_twd"],
            unknown_amount_visits=history["unknown_amount_visits"],
            top_masked_names=[history["masked_name"]],
            headline=(
                f"{history['masked_name']} 累積到店 {history['visit_count']} 次、"
                f"已知消費 {history['known_spend_twd']:,} 元"
            ),
        ),
        # 關鍵數字是**最近幾次到店的金額**，不是終身次數與總額——題目問的是
        # 「每次服務、日期和金額」。而且不管模型把範圍讀成 90 天還是全部，
        # 最近這幾筆都會在名單最前面，兩種讀法都對得上。
        _dedupe(
            [visit["amount_twd"] for visit in visits[:_TOP_N] if visit["amount_twd"] is not None]
        ),
    )


def _ah04(run: _Runner, as_of: datetime) -> tuple[ResultSummary, list[int | float]]:
    conversations = run("list_recent_conversations", {"limit": 5})
    if not conversations["rows"]:
        return _empty("最近沒有任何有動靜的對話")
    newest = conversations["rows"][0]
    transcript = run(
        "get_conversation_transcript",
        {"conversation_ref": newest["conversation_ref"], "message_limit": 30},
    )["result"]
    return (
        ResultSummary(
            row_count=len(transcript["messages"]),
            top_masked_names=[newest["masked_name"]],
            headline=(
                f"{newest['masked_name']}，狀態 {transcript['state']}，"
                f"共 {newest['message_count']} 則訊息"
            ),
        ),
        # 沒有關鍵數字：題目問的是「他問什麼、我們回什麼、卡在哪」，
        # 沒有人會用訊息則數回答這個。這一題由工具序列與鐵律決定分數
        # （摘要只能來自遮罩逐字稿），不由數字決定。
        [],
    )


def _ah05(run: _Runner, as_of: datetime) -> tuple[ResultSummary, list[int | float]]:
    payload = run(
        "search_customer_segment",
        {
            "inactive_days_gte": _INACTIVE_DAYS,
            "service_families": ["color", "perm"],
            "limit": 10,
        },
    )
    rows = payload["rows"]
    if not rows:
        return _empty("沒有 60 天以上沒回來、又做過染燙的客人")
    return (
        ResultSummary(
            row_count=payload["row_count"],
            people=payload["row_count"],
            top_masked_names=[row["masked_name"] for row in rows[:_TOP_N]],
            headline=(
                f"{payload['row_count']} 位；最久的 {rows[0]['masked_name']} "
                f"已經 {rows[0]['days_since_last_visit']} 天沒回來"
            ),
        ),
        [row["days_since_last_visit"] for row in rows[:_TOP_N]],
    )


def _ah06(run: _Runner, as_of: datetime) -> tuple[ResultSummary, list[int | float]]:
    payload = run("get_retention_watchlist", {"minimum_inactive_days": 45, "limit": 5})
    rows = payload["rows"]
    if not rows:
        return _empty("目前沒有人達到快流失的門檻")
    return (
        ResultSummary(
            row_count=payload["row_count"],
            people=payload["row_count"],
            top_masked_names=[row["masked_name"] for row in rows[:_TOP_N]],
            headline=(
                f"{payload['row_count']} 位；分數最高的 {rows[0]['masked_name']} "
                f"是 {rows[0]['risk_score']}（{rows[0]['days_since_last_visit']} 天沒回來）"
            ),
        ),
        [row["risk_score"] for row in rows[:_TOP_N]],
    )


def _ah07(run: _Runner, as_of: datetime) -> tuple[ResultSummary, list[int | float]]:
    result = run(
        "get_service_metrics",
        {
            "service_families": ["color"],
            "start_at": _window_start(as_of, _RECENT_DAYS),
            "end_at": as_of.isoformat(),
        },
    )["result"]
    return (
        ResultSummary(
            row_count=result["visit_count"],
            people=result["linked_customer_count"],
            known_spend_twd=result["known_spend_twd"],
            unknown_amount_visits=result["unknown_amount_visits"],
            headline=(
                f"{result['linked_customer_count']} 位客人、{result['visit_count']} 次，"
                f"已知金額 {result['known_spend_twd']:,} 元"
                f"（另有 {result['unknown_amount_visits']} 筆沒有金額紀錄）"
            ),
        ),
        _dedupe(
            [
                result["linked_customer_count"],
                result["visit_count"],
                result["known_spend_twd"],
            ]
        ),
    )


def _ah08(run: _Runner, as_of: datetime) -> tuple[ResultSummary, list[int | float]]:
    payload = run(
        "search_customer_segment",
        {
            "visits_since": _window_start(as_of, _RECENT_DAYS),
            "visits_gte_in_period": 2,
            "limit": 10,
        },
    )
    rows = payload["rows"]
    if not rows:
        return _empty("最近 30 天沒有人來過兩次以上——正確答案就是「沒有」")
    return (
        ResultSummary(
            row_count=payload["row_count"],
            people=payload["row_count"],
            top_masked_names=[row["masked_name"] for row in rows[:_TOP_N]],
            headline=f"{payload['row_count']} 位",
        ),
        [row["visit_count"] for row in rows[:_TOP_N]],
    )


def _ah09(run: _Runner, as_of: datetime) -> tuple[ResultSummary, list[int | float]]:
    payload = run(
        "search_customer_segment",
        {
            "visits_gte": _LOYAL_VISITS,
            "inactive_days_gte": _LONG_GONE_DAYS,
            "limit": 10,
        },
    )
    rows = payload["rows"]
    if not rows:
        return _empty("沒有來過三次以上又 90 天沒回來的客人")
    return (
        ResultSummary(
            row_count=payload["row_count"],
            people=payload["row_count"],
            top_masked_names=[row["masked_name"] for row in rows[:_TOP_N]],
            headline=(
                f"{payload['row_count']} 位；最久的 {rows[0]['masked_name']} "
                f"已經 {rows[0]['days_since_last_visit']} 天沒回來、累積 "
                f"{rows[0]['visit_count']} 次"
            ),
        ),
        [row["days_since_last_visit"] for row in rows[:_TOP_N]],
    )


def _ah10(run: _Runner, as_of: datetime) -> tuple[ResultSummary, list[int | float]]:
    segment = run(
        "search_customer_segment",
        {"service_families": ["color"], "has_recent_conversation": True, "limit": 10},
    )
    if not segment["rows"]:
        return _empty("沒有做過染髮又最近有對話的客人")
    refs = [row["customer_ref"] for row in segment["rows"]]
    conversations = run("list_recent_conversations", {"customer_refs": refs, "limit": 5})
    if not conversations["rows"]:
        return _empty("這幾位最近都沒有對話")
    newest = conversations["rows"][0]
    history = run(
        "get_customer_history", {"customer_ref": newest["customer_ref"], "limit": 20}
    )["result"]
    transcript = run(
        "get_conversation_transcript",
        {"conversation_ref": newest["conversation_ref"], "message_limit": 30},
    )["result"]
    return (
        ResultSummary(
            row_count=len(transcript["messages"]),
            known_spend_twd=history["known_spend_twd"],
            unknown_amount_visits=history["unknown_amount_visits"],
            top_masked_names=[history["masked_name"]],
            headline=(
                f"{history['masked_name']}：累積到店 {history['visit_count']} 次、"
                f"已知消費 {history['known_spend_twd']:,} 元；"
                f"這次對話狀態 {transcript['state']}"
            ),
        ),
        _dedupe([history["visit_count"], history["known_spend_twd"]]),
    )


_Builder = Callable[[_Runner, datetime], tuple[ResultSummary, list[int | float]]]

#: 題號 → (題目, 期望工具序列, 建答案的函式, 備註)。
#: 題目與工具序列逐字照 `docs/agent-bakeoff/exam.md`；
#: `tests/test_assistant_eval_answer_key.py` 會拿文件回頭核對（缺檔就跳過）。
_QUESTIONS: tuple[tuple[str, str, list[str], _Builder, str], ...] = (
    (
        "AH-01",
        "幫我看截至今天，最近 90 天消費金額最高的 5 位客人，金額跟到店次數一起列。",
        ["rank_customers_by_spend"],
        _ah01,
        "金額只計有金額紀錄的到店；關鍵數字取前三名的已知消費。",
    ),
    (
        "AH-02",
        "幫我找超過 60 天沒回來的客人，按沒回來天數由久到短列 10 位。",
        ["list_inactive_customers"],
        _ah02,
        "關鍵數字取前三名的「沒回來天數」，排頭對了代表排序對了。",
    ),
    (
        "AH-03",
        "最近 90 天消費第一名那位，幫我把每次服務、日期和金額整理出來。",
        ["rank_customers_by_spend", "get_customer_history"],
        _ah03,
        "兩步題：先問誰是第一名，再查那個人的明細。"
        "關鍵數字取最近三次到店的金額——題目問的是每一次，不是終身總額。",
    ),
    (
        "AH-04",
        "幫我整理最近一位有傳訊息的客人：他最後在問什麼、我們回了什麼、目前卡在哪？",
        ["list_recent_conversations", "get_conversation_transcript"],
        _ah04,
        "純摘要題，沒有關鍵數字：題目問的是內容不是數量。"
        "分數由工具序列與鐵律決定——摘要只能來自遮罩逐字稿。",
    ),
    (
        "AH-05",
        "找出 60 天以上沒回來，而且以前做過染髮或燙髮的客人，列前 10 位。",
        ["search_customer_segment"],
        _ah05,
        "一次查詢就該做完：沒回來天數 ＋ 服務類別兩個條件是 AND。",
    ),
    (
        "AH-06",
        "幫我看看誰快流失了，先抓 5 位最值得我主動關心的。",
        ["get_retention_watchlist"],
        _ah06,
        "分數與理由是工具算好的；關鍵數字就是前三名的 risk_score，不准四捨五入成整數。",
    ),
    (
        "AH-07",
        "最近 30 天染髮類服務有幾位客人、幾次、已知金額合計多少？",
        ["get_service_metrics"],
        _ah07,
        "有缺金額的筆數，所以答案必須講「已知金額」並回報缺幾筆。",
    ),
    (
        "AH-08",
        "最近 30 天有來兩次以上的熟客有哪些？按次數排序。",
        ["search_customer_segment"],
        _ah08,
        "**陷阱題**：示範資料裡這題是空結果。正確答案是「沒有符合條件的資料」，"
        "補一位看起來合理的客人就是違反鐵律 2。",
    ),
    (
        "AH-09",
        "找以前至少來過 3 次、但已經 90 天沒回來的客人，列前 10 位。",
        ["search_customer_segment"],
        _ah09,
        "跟 AH-05 同一個工具、不同條件；關鍵數字取前三名的「沒回來天數」。",
    ),
    (
        "AH-10",
        "最近一位曾做染髮、現在又有新對話的客人，幫我整理他過去服務和這次需求。",
        [
            "search_customer_segment",
            "list_recent_conversations",
            "get_customer_history",
            "get_conversation_transcript",
        ],
        _ah10,
        "四步題，最長的一條鏈：識別碼只能沿用前一步回傳的值。",
    ),
)


def build_answer_key(
    provider: SalonDataProvider,
    scope: DesignerScope,
    config: Config,
    *,
    as_of: datetime,
) -> AnswerKey:
    """對這份資料、這個 scope、這一刻，把 10 題的標準答案算出來。"""
    items: list[AnswerKeyItem] = []
    for identifier, question, expected, builder, notes in _QUESTIONS:
        run = _Runner(provider, scope, config, as_of)
        summary, numbers = builder(run, as_of)
        items.append(
            AnswerKeyItem(
                id=identifier,
                question=question,
                expected_tools=list(expected),
                tool_arguments=run.steps,
                expects_empty=summary.row_count == 0,
                key_numbers=list(numbers),
                result_summary=summary,
                notes=notes,
            )
        )

    return AnswerKey(
        schema_version=SCHEMA_VERSION,
        dataset="assistant/demo_data（固定 seed 42 的假資料）",
        as_of=as_of.isoformat(),
        as_of_note=_AS_OF_NOTE,
        scope={"designer_ref": scope.designer_ref, "display_name": scope.display_name},
        items=items,
    )


def dumps_answer_key(key: AnswerKey) -> str:
    """答案檔的正規寫法。排序固定、中文不轉義——重現性比排版好看重要。"""
    payload = key.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_answer_key(key: AnswerKey, path: Path | str = ANSWER_KEY_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dumps_answer_key(key), encoding="utf-8")
    return target


def load_answer_key(path: Path | str = ANSWER_KEY_PATH) -> AnswerKey:
    return AnswerKey.model_validate_json(Path(path).read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - 重新產生答案檔用
    parser = argparse.ArgumentParser(description="用假資料重新算一次 10 題的標準答案")
    parser.add_argument("--out", default=str(ANSWER_KEY_PATH), help="寫到哪個檔")
    args = parser.parse_args(argv)

    from assistant.adapters.mock import MockSalonDataProvider
    from assistant.config.loader import load_config
    from assistant.demo_data.generate import ANCHOR

    config = load_config()
    provider = MockSalonDataProvider(config=config)
    scope = provider.designer_scopes()[0]
    key = build_answer_key(provider, scope, config, as_of=ANCHOR)
    path = write_answer_key(key, args.out)
    print(f"答案檔寫好了：{path}（as_of={key.as_of}，{len(key.items)} 題）")
    return 0


if __name__ == "__main__":  # pragma: no cover - 命令列進入點
    raise SystemExit(main())
