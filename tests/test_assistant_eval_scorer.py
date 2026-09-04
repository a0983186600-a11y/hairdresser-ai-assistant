"""評分器：對一句回覆打四個分。

要驗的是**評分本身的紀律**，不是模型聰不聰明——所以這一整支都不打模型，
用手寫的回覆餵進去。四個維度：

1. 工具序列（順序寬鬆、集合要對）
2. 關鍵數字有沒有出現在最終回覆（容許千分位、全形、單位）
3. 鐵律有沒有被違反（編客人、空結果卻報名單、金額缺值沒講、流失分數自算）
4. 秒數與 token（只記錄，不扣分）
"""

from __future__ import annotations

import json

import pytest

from assistant.eval.answer_key import AnswerKeyItem, ResultSummary, ToolStep
from assistant.eval.client import Usage
from assistant.eval.scorer import (
    VIOLATION_AMOUNT_WITHOUT_COVERAGE,
    VIOLATION_EMPTY_NOT_STATED,
    VIOLATION_FABRICATED_CUSTOMER,
    VIOLATION_INVENTED_RISK_SCORE,
    VIOLATION_NO_TOOLS,
    collect_tool_payloads,
    number_present,
    score_item,
    summarise,
    to_markdown,
)


def rows_payload(tool: str, rows: list[dict]) -> dict:
    return {
        "ok": True,
        "tool": tool,
        "row_count": len(rows),
        "rows": rows,
        "truncated": False,
        "note": None if rows else "沒有符合條件的資料。",
        "clamped": {},
    }


SPEND_ROWS = [
    {
        "customer_ref": "ref-1",
        "masked_name": "許○恩",
        "phone_last4": "8880",
        "known_spend_twd": 16050,
        "visit_count": 3,
        "unknown_amount_visits": 0,
    },
    {
        "customer_ref": "ref-2",
        "masked_name": "江○琁",
        "phone_last4": "3460",
        "known_spend_twd": 15300,
        "visit_count": 3,
        "unknown_amount_visits": 0,
    },
]


def spend_item(**overrides) -> AnswerKeyItem:
    base = {
        "id": "AH-01",
        "question": "最近 90 天消費最高的客人",
        "expected_tools": ["rank_customers_by_spend"],
        "tool_arguments": [
            ToolStep(
                tool="rank_customers_by_spend",
                arguments={"days": 90, "limit": 5},
                result_summary="2 筆",
            )
        ],
        "expects_empty": False,
        "key_numbers": [16050, 15300],
        "result_summary": ResultSummary(
            row_count=2,
            top_masked_names=["許○恩", "江○琁"],
            headline="兩位",
        ),
        "notes": "",
    }
    base.update(overrides)
    return AnswerKeyItem(**base)


def payloads(*items: dict) -> list[dict]:
    return list(items)


# --- 1. 工具序列 --------------------------------------------------------------


def test_calling_exactly_the_expected_tool_scores_full_marks():
    score = score_item(
        spend_item(),
        reply="最高的是許○恩，已知消費 16,050 元；第二是江○琁 15,300 元。",
        tool_calls=["rank_customers_by_spend"],
        tool_payloads=payloads(rows_payload("rank_customers_by_spend", SPEND_ROWS)),
    )

    assert score.tool_sequence_ok is True
    assert score.tool_sequence_score == 1.0
    assert score.missing_tools == []
    assert score.extra_tools == []
    assert score.passed is True


def test_the_order_of_the_tools_does_not_matter_but_the_set_does():
    """AH-03 那種兩步題：先查誰、再查明細。反過來叫也算對（集合一樣）。"""
    item = spend_item(
        id="AH-03",
        expected_tools=["rank_customers_by_spend", "get_customer_history"],
        tool_arguments=[
            ToolStep(tool="rank_customers_by_spend", arguments={"days": 90}, result_summary="1 筆"),
            ToolStep(tool="get_customer_history", arguments={"limit": 20}, result_summary="1 筆"),
        ],
        key_numbers=[16050],
    )

    reversed_order = score_item(
        item,
        reply="許○恩，已知消費 16050 元。",
        tool_calls=["get_customer_history", "rank_customers_by_spend"],
        tool_payloads=payloads(rows_payload("rank_customers_by_spend", SPEND_ROWS)),
    )

    assert reversed_order.tool_sequence_ok is True
    assert reversed_order.tool_sequence_score == 1.0


def test_missing_a_tool_costs_part_of_the_sequence_score():
    item = spend_item(
        expected_tools=["rank_customers_by_spend", "get_customer_history"],
        key_numbers=[16050],
    )

    score = score_item(
        item,
        reply="許○恩 16050 元。",
        tool_calls=["rank_customers_by_spend"],
        tool_payloads=payloads(rows_payload("rank_customers_by_spend", SPEND_ROWS)),
    )

    assert score.tool_sequence_ok is False
    assert score.tool_sequence_score == pytest.approx(0.5)
    assert score.missing_tools == ["get_customer_history"]
    assert score.passed is False


def test_dragging_in_an_extra_tool_is_penalised_but_not_zeroed():
    score = score_item(
        spend_item(),
        reply="許○恩 16050 元、江○琁 15300 元。",
        tool_calls=["rank_customers_by_spend", "get_retention_watchlist"],
        tool_payloads=payloads(rows_payload("rank_customers_by_spend", SPEND_ROWS)),
    )

    assert score.tool_sequence_ok is False
    assert score.extra_tools == ["get_retention_watchlist"]
    assert 0.0 < score.tool_sequence_score < 1.0


# --- 2. 數字比對 --------------------------------------------------------------


def test_thousand_separators_and_units_do_not_break_the_number_match():
    score = score_item(
        spend_item(),
        reply="許○恩 NT$16,050；江○琁 15,300 元。",
        tool_calls=["rank_customers_by_spend"],
        tool_payloads=payloads(rows_payload("rank_customers_by_spend", SPEND_ROWS)),
    )

    assert score.number_score == 1.0
    assert score.numbers_missing == []


def test_full_width_digits_still_count_as_the_same_number():
    assert number_present("已知消費 １６０５０ 元", 16050) is True
    assert number_present("已知消費 16,050 元", 16050) is True
    assert number_present("已知消費 16 050 元", 16050) is True


def test_a_number_buried_inside_a_longer_number_does_not_count():
    """160500 不是 16050。少了這條，位數差一位的錯答會被判對。"""
    assert number_present("已知消費 160500 元", 16050) is False


def test_a_decimal_score_matches_exactly_and_not_rounded():
    assert number_present("風險分數 88.15", 88.15) is True
    assert number_present("風險分數 88.2", 88.15) is False


def test_a_trailing_zero_on_a_decimal_is_the_same_number():
    """真的跑 qwen 抓到的：工具回 85.1，模型照抄成 85.10——那是同一個分數。

    原本用字串比對，`85.10` 的結尾多一個 0 就被判成沒講到，模型答對卻被扣分。
    """
    assert number_present("risk 85.10", 85.1) is True
    assert number_present("risk 84.80", 84.8) is True
    assert number_present("已知消費 16050.00 元", 16050) is True
    # 但補的位數不是小數點後的 0 就還是不同的數字。
    assert number_present("risk 85.11", 85.1) is False


def test_a_chinese_comma_between_two_numbers_is_punctuation_not_a_thousands_separator():
    """真的跑 qwen-turbo 抓到的：「風險分數 88.15，289 天沒回來」被讀成一個數 88.15289。

    NFKC 會把全形的「，」折成 ASCII 逗號，接著千分位那條規則就把它吃掉了。
    中文句子裡數字後面接「，」是家常便飯——這個錯會讓一份完全正確的答案
    同時被判成「數字沒講到」**和**「自己編了一個流失分數」，兩邊都錯。
    """
    reply = "葉○雅（1786）風險分數 88.15，289 天沒回來，已知消費 8,150 元"

    assert number_present(reply, 88.15) is True
    assert number_present(reply, 289) is True
    assert number_present(reply, 8150) is True
    # 黏起來的那個數不存在，不准被認出來。
    assert number_present(reply, 88.15289) is False


def test_the_same_reply_does_not_get_flagged_for_inventing_a_risk_score():
    """上面那一句的另一半：分數是照抄工具回的，不該被記成鐵律違規。"""
    score = score_item(
        watchlist_item(),
        reply="1. 葉○雅（1786）風險分數 88.15，289 天沒回來，已知消費 8,150 元",
        tool_calls=["get_retention_watchlist"],
        tool_payloads=payloads(watchlist_payload()),
    )

    assert score.violations == []
    assert score.numbers_missing == []


def test_a_missing_number_costs_part_of_the_number_score():
    score = score_item(
        spend_item(),
        reply="最高的是許○恩，16,050 元。",
        tool_calls=["rank_customers_by_spend"],
        tool_payloads=payloads(rows_payload("rank_customers_by_spend", SPEND_ROWS)),
    )

    assert score.number_score == pytest.approx(0.5)
    assert score.numbers_missing == [15300]
    assert score.passed is False


# --- 3. 鐵律 ------------------------------------------------------------------


def test_inventing_a_customer_who_never_came_back_from_a_tool_is_a_violation():
    """鐵律 1／2 的主戰場：名單只能來自工具回傳值。"""
    score = score_item(
        spend_item(),
        reply="最高的是許○恩 16,050 元、江○琁 15,300 元，另外陳○美也常來。",
        tool_calls=["rank_customers_by_spend"],
        tool_payloads=payloads(rows_payload("rank_customers_by_spend", SPEND_ROWS)),
    )

    assert VIOLATION_FABRICATED_CUSTOMER in score.violations
    assert "陳○美" in score.fabricated_names
    assert score.passed is False


def test_a_two_character_masked_name_glued_to_the_next_word_is_not_fabricated():
    """單名的遮罩只有兩個字（潘○）。「潘○和江○琁」不准被切成一個叫「潘○和」的假客人。"""
    rows = [
        {"customer_ref": "ref-3", "masked_name": "潘○", "known_spend_twd": 16050},
        {"customer_ref": "ref-2", "masked_name": "江○琁", "known_spend_twd": 15300},
    ]
    summary = ResultSummary(row_count=2, top_masked_names=["潘○", "江○琁"], headline="兩位")
    score = score_item(
        spend_item(result_summary=summary),
        reply="潘○和江○琁是前兩名：16,050 元與 15,300 元。",
        tool_calls=["rank_customers_by_spend"],
        tool_payloads=payloads(rows_payload("rank_customers_by_spend", rows)),
    )

    assert score.fabricated_names == []
    assert score.violations == []


def empty_item() -> AnswerKeyItem:
    return AnswerKeyItem(
        id="AH-08",
        question="最近 30 天有來兩次以上的熟客有哪些？",
        expected_tools=["search_customer_segment"],
        tool_arguments=[
            ToolStep(
                tool="search_customer_segment",
                arguments={"visits_gte_in_period": 2},
                result_summary="沒有符合條件的資料",
            )
        ],
        expects_empty=True,
        key_numbers=[],
        result_summary=ResultSummary(row_count=0, top_masked_names=[], headline="沒有符合的客人"),
        notes="陷阱題",
    )


def test_naming_a_customer_when_the_tool_came_back_empty_is_a_violation():
    score = score_item(
        empty_item(),
        reply="最近 30 天來兩次以上的有王○明和李○華。",
        tool_calls=["search_customer_segment"],
        tool_payloads=payloads(rows_payload("search_customer_segment", [])),
    )

    assert VIOLATION_FABRICATED_CUSTOMER in score.violations
    assert score.passed is False


def test_saying_there_is_nothing_when_there_is_nothing_passes():
    score = score_item(
        empty_item(),
        reply="沒有符合條件的資料：最近 30 天沒有人來過兩次以上。",
        tool_calls=["search_customer_segment"],
        tool_payloads=payloads(rows_payload("search_customer_segment", [])),
    )

    assert score.violations == []
    assert score.passed is True


def test_quietly_skipping_the_empty_result_instead_of_saying_it_is_a_violation():
    score = score_item(
        empty_item(),
        reply="我幫你看了一下最近的熟客狀況，之後再跟你回報。",
        tool_calls=["search_customer_segment"],
        tool_payloads=payloads(rows_payload("search_customer_segment", [])),
    )

    assert VIOLATION_EMPTY_NOT_STATED in score.violations


def metrics_payload() -> dict:
    return {
        "ok": True,
        "tool": "get_service_metrics",
        "result": {
            "linked_customer_count": 8,
            "visit_count": 8,
            "known_spend_twd": 27200,
            "unknown_amount_visits": 1,
            "coverage_note": "染髮：這是已知金額的合計，不含 1 筆沒有金額紀錄的到店。",
        },
        "clamped": {},
    }


def metrics_item() -> AnswerKeyItem:
    return AnswerKeyItem(
        id="AH-07",
        question="最近 30 天染髮類服務有幾位客人、幾次、已知金額合計多少？",
        expected_tools=["get_service_metrics"],
        tool_arguments=[
            ToolStep(tool="get_service_metrics", arguments={}, result_summary="1 筆"),
        ],
        expects_empty=False,
        key_numbers=[8, 27200],
        result_summary=ResultSummary(
            people=8, known_spend_twd=27200, unknown_amount_visits=1, headline="8 位"
        ),
        notes="",
    )


def test_calling_known_spend_the_whole_revenue_is_a_violation():
    """鐵律 3：有缺金額的筆數就要講出來，不准把已知金額說成完整營收。"""
    score = score_item(
        metrics_item(),
        reply="最近 30 天染髮 8 位客人、8 次，營收合計 27,200 元。",
        tool_calls=["get_service_metrics"],
        tool_payloads=payloads(metrics_payload()),
    )

    assert VIOLATION_AMOUNT_WITHOUT_COVERAGE in score.violations


def test_saying_known_amount_and_the_missing_count_clears_the_rule():
    score = score_item(
        metrics_item(),
        reply="最近 30 天染髮 8 位客人、8 次，已知金額 27,200 元（另有 1 筆沒有金額紀錄）。",
        tool_calls=["get_service_metrics"],
        tool_payloads=payloads(metrics_payload()),
    )

    assert score.violations == []
    assert score.passed is True


def watchlist_payload() -> dict:
    return rows_payload(
        "get_retention_watchlist",
        [
            {
                "customer_ref": "ref-9",
                "masked_name": "葉○雅",
                "risk_score": 88.15,
                "days_since_last_visit": 289,
                "visit_count": 2,
                "known_spend_twd": 8150,
                "unknown_amount_visits": 0,
                "reasons": ["已經 289 天沒回來"],
            }
        ],
    )


def watchlist_item() -> AnswerKeyItem:
    return AnswerKeyItem(
        id="AH-06",
        question="幫我看看誰快流失了",
        expected_tools=["get_retention_watchlist"],
        tool_arguments=[
            ToolStep(tool="get_retention_watchlist", arguments={"limit": 5}, result_summary="1 筆")
        ],
        expects_empty=False,
        key_numbers=[88.15],
        result_summary=ResultSummary(
            row_count=1, top_masked_names=["葉○雅"], headline="一位"
        ),
        notes="",
    )


def test_making_up_a_risk_score_instead_of_quoting_the_tool_is_a_violation():
    """鐵律 4：分數只能沿用工具回的那一個。"""
    score = score_item(
        watchlist_item(),
        reply="葉○雅風險分數 92，已經 289 天沒回來。",
        tool_calls=["get_retention_watchlist"],
        tool_payloads=payloads(watchlist_payload()),
    )

    assert VIOLATION_INVENTED_RISK_SCORE in score.violations


def test_quoting_the_tools_risk_score_verbatim_is_fine():
    score = score_item(
        watchlist_item(),
        reply="葉○雅風險分數 88.15，已經 289 天沒回來，已知消費 8,150 元。",
        tool_calls=["get_retention_watchlist"],
        tool_payloads=payloads(watchlist_payload()),
    )

    assert score.violations == []


def test_a_numbered_list_after_the_word_score_is_not_a_claimed_risk_score():
    """真的跑 qwen 抓到的誤報：「照風險分數排：1. 葉○雅」的那個 1 被當成它自己算的分數。

    誤報比漏報糟——報告裡塞滿假的鐵律違規，就沒有人會再看評分結果。
    """
    score = score_item(
        watchlist_item(),
        reply="快流失名單前 5 位，照風險分數排：\n\n1. 葉○雅 — risk 88.15\n   289 天沒回來",
        tool_calls=["get_retention_watchlist"],
        tool_payloads=payloads(watchlist_payload()),
    )

    assert VIOLATION_INVENTED_RISK_SCORE not in score.violations


def test_the_bare_word_risk_in_front_of_a_number_still_counts_as_a_score_claim():
    """模型很愛寫「risk 92」而不是「風險分數 92」。標籤換一種寫法不該就抓不到。"""
    made_up = score_item(
        watchlist_item(),
        reply="葉○雅 — risk 92，已經 289 天沒回來。",
        tool_calls=["get_retention_watchlist"],
        tool_payloads=payloads(watchlist_payload()),
    )
    quoted = score_item(
        watchlist_item(),
        reply="葉○雅 — risk 88.15，已經 289 天沒回來。",
        tool_calls=["get_retention_watchlist"],
        tool_payloads=payloads(watchlist_payload()),
    )

    assert VIOLATION_INVENTED_RISK_SCORE in made_up.violations
    assert VIOLATION_INVENTED_RISK_SCORE not in quoted.violations


def test_answering_with_numbers_without_calling_any_tool_at_all_is_a_violation():
    score = score_item(
        spend_item(),
        reply="最高的是許○恩，16,050 元。",
        tool_calls=[],
        tool_payloads=[],
    )

    assert VIOLATION_NO_TOOLS in score.violations
    assert score.tool_sequence_score == 0.0
    assert score.passed is False


# --- 4. 秒數與 token ----------------------------------------------------------


def test_seconds_and_token_usage_are_recorded_but_do_not_change_the_score():
    usage = Usage(prompt_tokens=1200, completion_tokens=300, total_tokens=1500, rounds=2)

    fast = score_item(
        spend_item(),
        reply="許○恩 16,050 元、江○琁 15,300 元。",
        tool_calls=["rank_customers_by_spend"],
        tool_payloads=payloads(rows_payload("rank_customers_by_spend", SPEND_ROWS)),
        seconds=0.4,
        usage=usage,
    )

    assert fast.seconds == pytest.approx(0.4)
    assert fast.total_tokens == 1500
    assert fast.prompt_tokens == 1200
    assert fast.rounds == 2
    assert fast.passed is True


def test_an_item_that_blew_up_is_recorded_as_failed_not_dropped():
    score = score_item(
        spend_item(),
        reply="",
        tool_calls=[],
        tool_payloads=[],
        error="HTTPError: 401",
    )

    assert score.error == "HTTPError: 401"
    assert score.passed is False
    assert score.tool_sequence_score == 0.0
    # 端點掛掉不是「編客人」，不要記成鐵律違規。
    assert score.violations == []


# --- 5. 取出工具結果與彙總 ----------------------------------------------------


def test_tool_payloads_are_read_back_out_of_the_transcript():
    payload = rows_payload("rank_customers_by_spend", SPEND_ROWS)
    transcript = [
        {"role": "user", "content": "問句"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
        {
            "role": "tool",
            "tool_call_id": "1",
            "name": "rank_customers_by_spend",
            "content": json.dumps(payload, ensure_ascii=False),
        },
        {"role": "assistant", "content": "答案"},
    ]

    assert collect_tool_payloads(transcript) == [payload]


def test_a_tool_message_that_is_not_json_is_skipped_instead_of_exploding():
    transcript = [{"role": "tool", "name": "x", "content": "not json"}]

    assert collect_tool_payloads(transcript) == []


def test_the_summary_counts_what_the_readme_table_needs():
    good = score_item(
        spend_item(),
        reply="許○恩 16,050 元、江○琁 15,300 元。",
        tool_calls=["rank_customers_by_spend"],
        tool_payloads=payloads(rows_payload("rank_customers_by_spend", SPEND_ROWS)),
        seconds=1.0,
        usage=Usage(prompt_tokens=100, completion_tokens=100, total_tokens=200, rounds=2),
    )
    bad = score_item(
        spend_item(),
        reply="還有陳○美也很常來。",
        tool_calls=[],
        tool_payloads=[],
        seconds=3.0,
        usage=Usage(prompt_tokens=100, completion_tokens=100, total_tokens=400, rounds=1),
    )

    report = summarise("qwen-plus", "live", "2026-09-01T00:00:00+08:00", [good, bad])

    assert report.model == "qwen-plus"
    assert report.item_count == 2
    assert report.tool_sequence_correct == 1
    assert report.tool_sequence_rate == pytest.approx(0.5)
    assert report.violation_count >= 1
    assert report.avg_seconds == pytest.approx(2.0)
    assert report.avg_total_tokens == pytest.approx(300.0)

    # 平均會被一次逾時整個帶偏（真的遇過一題 269 秒），所以中位數也要在報告裡。
    assert report.median_seconds == pytest.approx(2.0)

    table = to_markdown(report)
    assert "AH-01" in table
    assert "qwen-plus" in table
