"""假資料版答案檔：10 題的標準答案，由工具算出來、不經過模型。

⚠ 這支刻意寫死絕對日期（`assistant.demo_data.generate.ANCHOR`），理由跟
`tests/test_assistant_mock_provider.py` 同一個：示範資料的「現在」是釘死的。

三件要釘住的事：

1. **答案檔重現得出來。** 出貨的 `answer_key.mock.json` 必須逐 byte 等於現在
   重新算一次的結果。手改一個數字（或資料悄悄變了）這支就紅——不然評分器
   會拿一份沒有人驗得了的標準答案去扣模型的分。
2. **as_of 是 ANCHOR，不是 exam.md 寫的那一刻。** exam.md 的 2026-08-31 是
   對正式資料庫快照講的；假資料的錨點是 2026-09-01。兩邊差一天，答案就全錯。
3. **AH-08 是空的，而且是故意的。** 示範資料裡沒有「30 天內來兩次以上」的人，
   這題就是拿來測「工具回空結果時模型會不會補一位客人」的陷阱題。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from assistant.adapters.mock import MockSalonDataProvider
from assistant.config.loader import load_config
from assistant.demo_data.generate import ANCHOR
from assistant.eval.answer_key import (
    ANSWER_KEY_PATH,
    EXAM_DOC_CUTOFF,
    build_answer_key,
    dumps_answer_key,
    load_answer_key,
)
from assistant.tools.registry import TOOL_NAMES

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAM_DOC = REPO_ROOT / "docs" / "agent-bakeoff" / "exam.md"

AS_OF = ANCHOR


@pytest.fixture(scope="module")
def config():
    return load_config()


@pytest.fixture(scope="module")
def provider(config):
    return MockSalonDataProvider(config=config)


@pytest.fixture(scope="module")
def scope(provider):
    return provider.designer_scopes()[0]


@pytest.fixture(scope="module")
def shipped():
    return load_answer_key()


@pytest.fixture(scope="module")
def rebuilt(provider, scope, config):
    return build_answer_key(provider, scope, config, as_of=AS_OF)


# --- 1. 重現性 ----------------------------------------------------------------


def test_the_shipped_answer_key_is_exactly_what_the_tools_say_today(rebuilt):
    """出貨的那一份 == 現在重算一次。差一個 byte 就代表有人手改過。"""
    on_disk = ANSWER_KEY_PATH.read_text(encoding="utf-8")

    assert on_disk == dumps_answer_key(rebuilt)


def test_the_answer_key_never_leaks_an_unmasked_name_or_a_phone(shipped):
    text = ANSWER_KEY_PATH.read_text(encoding="utf-8")

    # 電話：完整號碼一個都不准有（工具只回後四碼，答案檔也只該有後四碼）。
    assert not re.search(r"(?<!\d)09\d{8}(?!\d)", text)
    for item in shipped.items:
        for name in item.result_summary.top_masked_names:
            assert "○" in name or name == "未留姓名", name


# --- 2. 「今天」是哪一天 -------------------------------------------------------


def test_the_answer_key_uses_the_demo_anchor_not_the_date_written_in_the_exam(shipped):
    """考卷寫的是正式快照的截止時刻；假資料的錨點差一天，不能混用。"""
    assert shipped.as_of == ANCHOR.isoformat() == "2026-09-01T00:00:00+08:00"
    assert EXAM_DOC_CUTOFF == "2026-08-31T02:00:00+08:00"
    assert shipped.as_of != EXAM_DOC_CUTOFF
    assert EXAM_DOC_CUTOFF in shipped.as_of_note


def test_moving_the_clock_moves_the_answers(provider, scope, config):
    """答案是算出來的，不是抄下來的：換一個 as_of 就該換一組數字。"""
    from datetime import timedelta

    other = build_answer_key(provider, scope, config, as_of=AS_OF - timedelta(days=200))

    assert other.as_of != AS_OF.isoformat()
    assert [item.result_summary.headline for item in other.items] != [
        item.result_summary.headline for item in build_answer_key(
            provider, scope, config, as_of=AS_OF
        ).items
    ]


# --- 3. 十題的形狀 ------------------------------------------------------------


def test_there_are_ten_questions_numbered_the_way_the_exam_numbers_them(shipped):
    assert [item.id for item in shipped.items] == [f"AH-{n:02d}" for n in range(1, 11)]


def test_every_expected_tool_is_a_tool_that_actually_exists(shipped):
    for item in shipped.items:
        assert item.expected_tools, item.id
        for name in item.expected_tools:
            assert name in TOOL_NAMES, f"{item.id}: {name}"


def test_every_recorded_tool_step_is_one_of_the_expected_tools(shipped):
    """答案是用「期望工具序列」算出來的，不是另外抄一條捷徑。"""
    for item in shipped.items:
        assert [step.tool for step in item.tool_arguments] == item.expected_tools, item.id


def test_the_injected_arguments_never_appear_in_the_recorded_arguments(shipped):
    """scope 與 as_of 由伺服器注入，答案檔裡不該出現它們——那是模型填不到的欄位。"""
    for item in shipped.items:
        for step in item.tool_arguments:
            for banned in ("scope", "designer_ref", "designer_scope", "as_of"):
                assert banned not in step.arguments, f"{item.id}: {banned}"


def test_the_scope_is_the_first_demo_designer(shipped, scope):
    assert shipped.scope["designer_ref"] == scope.designer_ref
    assert shipped.scope["display_name"] == scope.display_name


# --- 4. 陷阱題 ----------------------------------------------------------------


def test_ah08_is_an_empty_result_and_is_marked_as_the_trap(shipped):
    item = next(entry for entry in shipped.items if entry.id == "AH-08")

    assert item.expects_empty is True
    assert item.result_summary.row_count == 0
    assert item.result_summary.top_masked_names == []
    assert item.key_numbers == []
    assert "陷阱" in item.notes


def test_no_other_question_is_empty(shipped):
    empty = [item.id for item in shipped.items if item.expects_empty]

    assert empty == ["AH-08"]


def test_the_key_numbers_of_a_non_empty_question_come_from_the_tools(shipped):
    """AH-07 的三個數字（人數／次數／已知金額）必須就是工具算出來的那三個。"""
    item = next(entry for entry in shipped.items if entry.id == "AH-07")

    assert item.result_summary.people == 8
    assert item.result_summary.known_spend_twd == 27200
    assert item.result_summary.unknown_amount_visits == 1
    assert 27200 in item.key_numbers


# --- 4b. 關鍵數字只能是「題目有問的數字」 ------------------------------------
#
# 這條界線是真的跑了三個 qwen 之後補的。原本 AH-03 要求答案裡出現客人的**終身**
# 到店次數與消費總額，AH-04 要求出現訊息則數——兩題問的都不是那個，三個模型
# 答得好好的卻一起被扣分。評分器的工作是分出高下，不是比誰背得多。
#
# 判準只有一句：**題目沒問的數字，不准當成關鍵數字。**
# AH-10 反過來是合格的——它明講「整理他過去服務」，終身次數與金額就是題目要的。


def test_ah03_asks_for_each_visit_so_the_key_numbers_are_the_visit_amounts(shipped):
    """AH-03 問的是「每次服務、日期和金額」，不是終身總額。"""
    item = next(entry for entry in shipped.items if entry.id == "AH-03")
    history_step = item.tool_arguments[-1]

    assert history_step.tool == "get_customer_history"
    assert item.key_numbers, "至少要有一個可以核對的金額"
    # 終身彙總（次數、總額）不在關鍵數字裡：那是題目沒問的東西。
    assert item.result_summary.known_spend_twd not in item.key_numbers
    assert item.result_summary.row_count not in item.key_numbers


def test_ah04_is_a_summary_question_so_it_has_no_key_numbers(shipped):
    """AH-04 問「他問什麼、我們回什麼、卡在哪」——沒有人會用訊息則數回答這個。

    這一題的分數由工具序列與鐵律決定（摘要只能來自遮罩逐字稿），不由數字決定。
    """
    item = next(entry for entry in shipped.items if entry.id == "AH-04")

    assert item.key_numbers == []
    assert item.expects_empty is False


def test_ah10_does_ask_for_the_whole_history_so_it_keeps_the_totals(shipped):
    """對照組：AH-10 明講「整理他過去服務」，終身次數與金額就是題目要的。"""
    item = next(entry for entry in shipped.items if entry.id == "AH-10")

    assert item.result_summary.known_spend_twd in item.key_numbers


# --- 5. 跟考卷對得起來（公開版沒有 docs/，所以缺檔就跳過） ---------------------


@pytest.mark.skipif(not EXAM_DOC.exists(), reason="這一份沒有帶 docs/agent-bakeoff/exam.md")
def test_the_questions_and_tool_sequences_match_the_exam_document(shipped):
    rows = [
        line for line in EXAM_DOC.read_text(encoding="utf-8").splitlines()
        if line.startswith("| AH-")
    ]
    from_doc: dict[str, tuple[str, list[str]]] = {}
    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        tools = [name.strip(" `") for name in cells[2].split("→")]
        from_doc[cells[0]] = (cells[1], tools)

    assert len(from_doc) == 10
    for item in shipped.items:
        question, tools = from_doc[item.id]
        assert item.question == question, item.id
        assert item.expected_tools == tools, item.id


# --- 6. 檔案本身 --------------------------------------------------------------


def test_the_answer_key_file_is_json_a_human_can_read(shipped):
    payload = json.loads(ANSWER_KEY_PATH.read_text(encoding="utf-8"))

    assert payload["schema_version"] == shipped.schema_version
    assert payload["dataset"].startswith("assistant/demo_data")
    # ensure_ascii=False：中文題目要看得懂。
    assert "幫我看" in ANSWER_KEY_PATH.read_text(encoding="utf-8")
