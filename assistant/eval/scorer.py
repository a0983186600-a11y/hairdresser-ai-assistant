"""評分器：一句回覆值幾分。

四個維度，跟 `docs/agent-bakeoff/exam.md` 的「通用評分鐵律」對得起來：

1. **工具序列**——集合要對，順序寬鬆。設計師不在乎模型先查誰再查誰，
   在乎的是「該查的都查了、不該查的沒亂查」。
2. **關鍵數字**——答案檔算出來的那幾個數字要出現在**最終回覆**裡。
   比的是**數值**不是字串：千分位、全形數字、單位、小數點後補的零（`85.10` 之於
   `85.1`）都算同一個數；但 `160500` 不算 `16050`，`88.2` 也不算 `88.15`。
3. **鐵律**——編客人、空結果卻報名單、把已知金額說成完整營收、自己算流失分數。
   這一層是整份評分的重點：模型講得漂不漂亮無所謂，這四件事一件都不能犯。
4. **秒數與 token**——只記錄、不扣分。快跟省是選型的依據，不是對錯。

## 為什麼「編客人」抓得到

`ChatResult.transcript` 裡有每一次工具回傳的完整 JSON。從那裡把所有
`masked_name` 撈出來，就是這一輪**唯一合法**的名單。回覆裡出現遮罩形狀
（`X○…Y`）卻不在名單上的，就是模型自己補的。

比對容許前後接字：單名遮罩只有兩個字（`潘○`），中文裡「潘○和…」黏成三個字很常見，
所以「找到的字串」與「合法名字」互為前綴就算數。寧可漏抓一個邊界案例，
也不要每一份報告都塞滿誤報——誤報三次之後就沒有人會再看評分結果。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from statistics import median
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from assistant.eval.answer_key import AnswerKeyItem
from assistant.eval.client import Usage

__all__ = [
    "VIOLATION_NO_TOOLS",
    "VIOLATION_FABRICATED_CUSTOMER",
    "VIOLATION_EMPTY_NOT_STATED",
    "VIOLATION_AMOUNT_WITHOUT_COVERAGE",
    "VIOLATION_INVENTED_RISK_SCORE",
    "VIOLATION_LABELS",
    "EXTRA_TOOL_PENALTY",
    "ItemScore",
    "ModelReport",
    "number_present",
    "collect_tool_payloads",
    "score_item",
    "summarise",
    "to_markdown",
    "bakeoff_table",
]

VIOLATION_NO_TOOLS = "answered_without_tools"
VIOLATION_FABRICATED_CUSTOMER = "fabricated_customer"
VIOLATION_EMPTY_NOT_STATED = "empty_result_not_stated"
VIOLATION_AMOUNT_WITHOUT_COVERAGE = "amount_without_coverage"
VIOLATION_INVENTED_RISK_SCORE = "invented_risk_score"

VIOLATION_LABELS: dict[str, str] = {
    VIOLATION_NO_TOOLS: "沒查工具就報數字或名字",
    VIOLATION_FABRICATED_CUSTOMER: "報了工具沒回過的客人",
    VIOLATION_EMPTY_NOT_STATED: "空結果沒有講「沒有符合的資料」",
    VIOLATION_AMOUNT_WITHOUT_COVERAGE: "有缺金額卻沒講「已知金額」",
    VIOLATION_INVENTED_RISK_SCORE: "流失分數不是工具回的那一個",
}

#: 多叫一個工具扣多少。扣到 0 為止，不會變負分。
EXTRA_TOOL_PENALTY = 0.25

#: 遮罩姓名的形狀：`王○明`、`潘○`、`歐○○軒`。
_MASKED_NAME = re.compile(r"[一-鿿]○+[一-鿿]?")

#: 一個數字，用來把「回覆裡出現過哪些數」整批撈出來比數值。
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

#: 「風險分數 88.15」「risk 88.15」這種寫法。
#:
#: 標籤與數字之間**只准隔標點**（或「是」「為」這兩個連接字），不准隔中文——
#: 隔中文就會把「照風險分數排：1. 葉○雅」的清單編號 1 當成模型自己算的分數，
#: 而那是真的跑 qwen 時抓到的誤報。誤報比漏報糟：報告裡塞滿假違規之後，
#: 就沒有人會再看評分結果。
_SCORE_NEAR = re.compile(
    r"(?:風險分數|流失分數|risk[_]?score|risk|分數)"
    r"(?:[^0-9\u4e00-\u9fff]|是|為){0,3}"
    r"(\d+(?:\.\d+)?)"
)

#: 「沒有符合的資料」的各種說法。任一句出現就算有講。
_EMPTY_PHRASES = ("沒有", "查無", "找不到", "無符合", "0 位", "零位")

#: 講到錢的形狀。
_MONEY = re.compile(r"\d[\d]*\s*元|金額|消費|營收")

#: 「已知金額」那條鐵律的合格說法。
_COVERAGE_PHRASES = ("已知", "沒有金額", "缺金額", "未記錄", "無金額", "不含")


#: 夾在**兩個數字中間**的千分位符號：ASCII 逗號，或一個空格（`16 050` 這種排版）。
#:
#: 只吃這一個字元，而且兩邊都必須是數字。原本是把整份回覆的空白全部拿掉，
#: 結果換行也一起沒了：`risk 88.15\n  289 天沒回來` 會黏成 `88.15289`，
#: 一個分數與一個天數變成同一個不存在的數。
_DIGIT_SEPARATOR = re.compile(r"(?<=\d)[,\u0020\u00a0\u202f](?=\d)")

#: 全形數字與全形小數點。**只折這幾個**，其餘留給 NFKC 在千分位處理完之後再做。
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９．", "0123456789.")


def _normalise(text: str | None) -> str:
    """全形數字折半形 → 拿掉千分位 → NFKC。`１６０５０`、`16,050`、`16 050` 收斂成同一串。

    **順序是這一段唯一重要的事。** NFKC 會把全形的「，」折成 ASCII 逗號，
    先折再拿千分位的話，「風險分數 88.15，289 天沒回來」中間那個句讀就被當成
    千分位吃掉，變成一個不存在的數 `88.15289`——一份完全正確的答案會同時被判成
    「數字沒講到」與「自己編了一個流失分數」。中文句子裡數字後面接「，」太常見了，
    所以千分位這一刀必須落在 NFKC **之前**，那時候「，」還是「，」。
    """
    digits_folded = (text or "").translate(_FULLWIDTH_DIGITS)
    return unicodedata.normalize("NFKC", _DIGIT_SEPARATOR.sub("", digits_folded))


def _numbers_in(text: str) -> set[float]:
    """回覆裡出現過的每一個數（已正規化）。比數值不比字串，`85.10` 才等於 `85.1`。"""
    return {float(token) for token in _NUMBER.findall(_normalise(text))}


def number_present(text: str, value: float) -> bool:
    """`value` 有沒有出現在 `text` 裡。

    容許千分位、全形、單位與小數點後補的零；不容許位數不同——`160500` 不是 `16050`，
    `88.2` 不是 `88.15`。少了後面這條，四捨五入過的錯答會被判對。
    """
    if isinstance(value, bool):  # pragma: no cover - 防呆
        return False
    return float(value) in _numbers_in(text)


def _walk(node: Any) -> Iterable[Any]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _collect(payloads: Sequence[dict[str, Any]], key: str) -> list[Any]:
    found: list[Any] = []
    for payload in payloads:
        for node in _walk(payload):
            if key in node:
                found.append(node[key])
    return found


def collect_tool_payloads(transcript: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """從逐字稿裡把工具回傳的完整 JSON 撈回來。解不開的就跳過，不要炸掉整份評分。"""
    payloads: list[dict[str, Any]] = []
    for entry in transcript:
        if entry.get("role") != "tool":
            continue
        content = entry.get("content")
        if not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


class ItemScore(BaseModel):
    """一題的成績單。`passed` 是三個維度全過才算過。"""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    id: str
    question: str
    reply: str = ""

    expected_tools: list[str] = Field(default_factory=list)
    called_tools: list[str] = Field(default_factory=list)
    missing_tools: list[str] = Field(default_factory=list)
    extra_tools: list[str] = Field(default_factory=list)
    tool_sequence_ok: bool = False
    tool_sequence_score: float = 0.0

    numbers_expected: list[int | float] = Field(default_factory=list)
    numbers_missing: list[int | float] = Field(default_factory=list)
    number_score: float = 0.0

    violations: list[str] = Field(default_factory=list)
    fabricated_names: list[str] = Field(default_factory=list)

    seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    rounds: int = 0

    passed: bool = False
    error: str | None = None


class ModelReport(BaseModel):
    """一個模型跑完整份考卷的結果。README 的對決表就是從這裡長出來的。"""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    schema_version: int = 1
    model: str
    mode: str
    as_of: str
    item_count: int = 0
    tool_sequence_correct: int = 0
    tool_sequence_rate: float = 0.0
    number_rate: float = 0.0
    violation_count: int = 0
    passed_count: int = 0
    avg_seconds: float = 0.0
    #: 中位數也記一份：端點偶爾會有一題卡住幾分鐘（實測遇過 269 秒），
    #: 那一題會把平均整個帶偏，光看平均會誤判成「這個模型很慢」。
    median_seconds: float = 0.0
    avg_total_tokens: float = 0.0
    errors: int = 0
    items: list[ItemScore] = Field(default_factory=list)


# --- 三個維度 -----------------------------------------------------------------


def _score_tools(expected: Sequence[str], called: Sequence[str]) -> tuple[float, list, list, bool]:
    wanted = list(dict.fromkeys(expected))
    used = list(dict.fromkeys(called))
    missing = [name for name in wanted if name not in used]
    extra = [name for name in used if name not in wanted]
    recall = (len(wanted) - len(missing)) / len(wanted) if wanted else 1.0
    score = max(0.0, recall - EXTRA_TOOL_PENALTY * len(extra))
    return score, missing, extra, (not missing and not extra)


def _score_numbers(expected: Sequence[float], reply: str) -> tuple[float, list]:
    if not expected:
        return 1.0, []
    missing = [value for value in expected if not number_present(reply, value)]
    return (len(expected) - len(missing)) / len(expected), missing


def _fabricated(reply: str, payloads: Sequence[dict[str, Any]]) -> list[str]:
    allowed = {str(name) for name in _collect(payloads, "masked_name")}
    found = list(dict.fromkeys(_MASKED_NAME.findall(reply)))
    return [
        token
        for token in found
        if not any(token.startswith(name) or name.startswith(token) for name in allowed)
    ]


def _violations(
    item: AnswerKeyItem,
    reply: str,
    called: Sequence[str],
    payloads: Sequence[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    found: list[str] = []
    fabricated = _fabricated(reply, payloads)

    if not called and (any(character.isdigit() for character in reply) or fabricated):
        found.append(VIOLATION_NO_TOOLS)
    if fabricated:
        found.append(VIOLATION_FABRICATED_CUSTOMER)

    row_counts = [value for value in _collect(payloads, "row_count") if isinstance(value, int)]
    came_back_empty = bool(row_counts) and all(count == 0 for count in row_counts)
    if (item.expects_empty or came_back_empty) and not any(
        phrase in reply for phrase in _EMPTY_PHRASES
    ):
        found.append(VIOLATION_EMPTY_NOT_STATED)

    unknown = sum(
        value
        for value in _collect(payloads, "unknown_amount_visits")
        if isinstance(value, int)
    )
    if unknown and _MONEY.search(reply) and not any(
        phrase in reply for phrase in _COVERAGE_PHRASES
    ):
        found.append(VIOLATION_AMOUNT_WITHOUT_COVERAGE)

    quoted = {
        float(value)
        for value in _collect(payloads, "risk_score")
        if isinstance(value, int | float) and not isinstance(value, bool)
    }
    claimed = {float(token) for token in _SCORE_NEAR.findall(_normalise(reply))}
    if claimed - quoted:
        found.append(VIOLATION_INVENTED_RISK_SCORE)

    return found, fabricated


# --- 進門 ---------------------------------------------------------------------


def score_item(
    item: AnswerKeyItem,
    *,
    reply: str,
    tool_calls: Sequence[str],
    tool_payloads: Sequence[dict[str, Any]],
    seconds: float = 0.0,
    usage: Usage | None = None,
    error: str | None = None,
) -> ItemScore:
    """一題打一次分。`error` 有值代表這一題根本沒跑完——記成 0 分，但不算鐵律違規。"""
    counted = usage or Usage()

    if error is not None:
        return ItemScore(
            id=item.id,
            question=item.question,
            reply=reply,
            expected_tools=list(item.expected_tools),
            called_tools=list(tool_calls),
            missing_tools=list(item.expected_tools),
            numbers_expected=list(item.key_numbers),
            numbers_missing=list(item.key_numbers),
            seconds=seconds,
            prompt_tokens=counted.prompt_tokens,
            completion_tokens=counted.completion_tokens,
            total_tokens=counted.total_tokens,
            rounds=counted.rounds,
            passed=False,
            error=error,
        )

    tool_score, missing, extra, tools_ok = _score_tools(item.expected_tools, tool_calls)
    number_score, numbers_missing = _score_numbers(item.key_numbers, reply)
    violations, fabricated = _violations(item, reply, tool_calls, tool_payloads)

    return ItemScore(
        id=item.id,
        question=item.question,
        reply=reply,
        expected_tools=list(item.expected_tools),
        called_tools=list(tool_calls),
        missing_tools=missing,
        extra_tools=extra,
        tool_sequence_ok=tools_ok,
        tool_sequence_score=tool_score,
        numbers_expected=list(item.key_numbers),
        numbers_missing=numbers_missing,
        number_score=number_score,
        violations=violations,
        fabricated_names=fabricated,
        seconds=seconds,
        prompt_tokens=counted.prompt_tokens,
        completion_tokens=counted.completion_tokens,
        total_tokens=counted.total_tokens,
        rounds=counted.rounds,
        passed=tools_ok and number_score == 1.0 and not violations,
    )


def summarise(model: str, mode: str, as_of: str, scores: Sequence[ItemScore]) -> ModelReport:
    count = len(scores)
    correct = sum(1 for score in scores if score.tool_sequence_ok)
    return ModelReport(
        model=model,
        mode=mode,
        as_of=as_of,
        item_count=count,
        tool_sequence_correct=correct,
        tool_sequence_rate=correct / count if count else 0.0,
        number_rate=(sum(score.number_score for score in scores) / count) if count else 0.0,
        violation_count=sum(len(score.violations) for score in scores),
        passed_count=sum(1 for score in scores if score.passed),
        avg_seconds=(sum(score.seconds for score in scores) / count) if count else 0.0,
        median_seconds=median(score.seconds for score in scores) if count else 0.0,
        avg_total_tokens=(sum(score.total_tokens for score in scores) / count) if count else 0.0,
        errors=sum(1 for score in scores if score.error),
        items=list(scores),
    )


# --- 印成表 -------------------------------------------------------------------


def _cell(text: str) -> str:
    return text.replace("|", "／").replace("\n", " ")


def to_markdown(report: ModelReport) -> str:
    """一個模型的逐題表。報告旁邊會存一份，方便直接貼進 issue 或交接板。"""
    lines = [
        f"## {report.model}（{report.mode}）",
        "",
        f"- 十題：工具序列對 {report.tool_sequence_correct}/{report.item_count}"
        f"、數字正確率 {report.number_rate:.0%}"
        f"、鐵律違規 {report.violation_count} 次"
        f"、全對 {report.passed_count} 題",
        f"- 平均 {report.avg_seconds:.1f} 秒（中位數 {report.median_seconds:.1f} 秒）、"
        f"{report.avg_total_tokens:.0f} token（as_of {report.as_of}）",
        "",
        "| 題號 | 工具序列 | 數字 | 鐵律 | 秒 | token |",
        "|---|---|---|---|---|---|",
    ]
    for item in report.items:
        if item.error:
            rules = f"（沒跑完：{_cell(item.error[:40])}）"
        elif item.violations:
            rules = "；".join(VIOLATION_LABELS.get(name, name) for name in item.violations)
        else:
            rules = "—"
        tools = "✓" if item.tool_sequence_ok else f"{item.tool_sequence_score:.2f}"
        lines.append(
            f"| {item.id} | {tools} | {item.number_score:.0%} | {rules} "
            f"| {item.seconds:.1f} | {item.total_tokens} |"
        )
    return "\n".join(lines) + "\n"


def bakeoff_table(reports: Sequence[ModelReport]) -> str:
    """README「模型對決」那一節要貼的表。"""
    lines = [
        "| 模型 | 工具序列正確率 | 數字正確率 | 鐵律違規數 | 平均秒數 | 中位秒數 | 平均 token |",
        "|---|---|---|---|---|---|---|",
    ]
    for report in reports:
        lines.append(
            f"| `{report.model}` "
            f"| {report.tool_sequence_rate:.0%}（{report.tool_sequence_correct}/"
            f"{report.item_count}） "
            f"| {report.number_rate:.0%} "
            f"| {report.violation_count} "
            f"| {report.avg_seconds:.1f} s "
            f"| {report.median_seconds:.1f} s "
            f"| {report.avg_total_tokens:,.0f} |"
        )
    return "\n".join(lines) + "\n"
