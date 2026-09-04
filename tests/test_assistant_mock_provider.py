"""MockSalonDataProvider：8 個工具的語意、scope 隔離、固定的流失公式。

⚠ 這支刻意寫死絕對日期（`assistant.demo_data.generate.ANCHOR`），理由見
`assistant/demo_data/README.md`：示範資料集是固定的，每個查詢都自己帶 `as_of` 進去，
沒有一行讀系統時鐘，所以日曆走過去它不會變紅。
"""

import inspect
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from assistant.adapters.mock import MockSalonDataProvider
from assistant.adapters.provider import TOOL_METHOD_NAMES, SalonDataProvider
from assistant.adapters.schemas import (
    TAIPEI,
    ConversationSummary,
    ConversationTranscript,
    CustomerHistory,
    CustomerSpendRow,
    DesignerScope,
    InactiveCustomerRow,
    RetentionRow,
    SegmentCustomerRow,
    ServiceFamily,
    ServiceMetrics,
)
from assistant.config.loader import load_config
from assistant.demo_data.generate import ANCHOR, load_dataset

AS_OF = ANCHOR


@pytest.fixture(scope="module")
def demo() -> MockSalonDataProvider:
    return MockSalonDataProvider()


@pytest.fixture(scope="module")
def scopes(demo: MockSalonDataProvider) -> list[DesignerScope]:
    return demo.designer_scopes()


# --- 契約 --------------------------------------------------------------------


def test_mock_satisfies_the_protocol(demo: MockSalonDataProvider):
    assert isinstance(demo, SalonDataProvider)


def test_every_tool_method_takes_scope_first(demo: MockSalonDataProvider):
    for name in TOOL_METHOD_NAMES:
        method = getattr(demo, name)
        params = list(inspect.signature(method).parameters)
        assert params[0] == "scope", name


def test_method_signatures_match_tools_md(demo: MockSalonDataProvider):
    expected = {
        "rank_customers_by_spend": ["scope", "days", "limit", "as_of"],
        "list_inactive_customers": ["scope", "inactive_days", "limit", "as_of"],
        "search_customer_segment": [
            "scope",
            "as_of",
            "inactive_days_gte",
            "visits_gte",
            "visits_since",
            "visits_gte_in_period",
            "service_families",
            "has_recent_conversation",
            "limit",
        ],
        "get_customer_history": ["scope", "customer_ref", "as_of", "limit"],
        "list_recent_conversations": ["scope", "as_of", "customer_refs", "limit"],
        "get_conversation_transcript": ["scope", "conversation_ref", "message_limit"],
        "get_retention_watchlist": ["scope", "as_of", "minimum_inactive_days", "limit"],
        "get_service_metrics": ["scope", "service_families", "start_at", "end_at"],
    }
    for name, params in expected.items():
        assert list(inspect.signature(getattr(demo, name)).parameters) == params, name


def test_every_tool_returns_something_that_survives_a_json_round_trip(
    demo: MockSalonDataProvider, scopes
):
    scope = scopes[0]
    ranked = demo.rank_customers_by_spend(scope, days=90, limit=5, as_of=AS_OF)
    inactive = demo.list_inactive_customers(scope, inactive_days=60, limit=10, as_of=AS_OF)
    segment = demo.search_customer_segment(scope, as_of=AS_OF, visits_gte=3, limit=10)
    watchlist = demo.get_retention_watchlist(scope, as_of=AS_OF, minimum_inactive_days=45, limit=5)
    conversations = demo.list_recent_conversations(scope, as_of=AS_OF, limit=5)
    metrics = demo.get_service_metrics(
        scope,
        service_families=[ServiceFamily.COLOR],
        start_at=AS_OF - timedelta(days=30),
        end_at=AS_OF,
    )
    history = demo.get_customer_history(scope, customer_ref=ranked[0].customer_ref, as_of=AS_OF)
    transcript = demo.get_conversation_transcript(
        scope, conversation_ref=conversations[0].conversation_ref
    )

    pairs = [
        (CustomerSpendRow, ranked),
        (InactiveCustomerRow, inactive),
        (SegmentCustomerRow, segment),
        (RetentionRow, watchlist),
        (ConversationSummary, conversations),
        (CustomerHistory, [history]),
        (ConversationTranscript, [transcript]),
        (ServiceMetrics, [metrics]),
    ]
    for model, rows in pairs:
        assert rows, model.__name__
        for row in rows:
            assert isinstance(row, model)
            model.model_validate(json.loads(row.model_dump_json()))


# --- scope 隔離 ---------------------------------------------------------------


def test_a_designer_only_ever_sees_their_own_customers(demo: MockSalonDataProvider, scopes):
    owners = {c["customer_ref"]: c["designer_ref"] for c in load_dataset()["customers"]}
    for scope in scopes:
        for row in demo.rank_customers_by_spend(scope, days=3650, limit=50, as_of=AS_OF):
            assert owners[row.customer_ref] == scope.designer_ref
        for row in demo.list_inactive_customers(scope, inactive_days=1, limit=100, as_of=AS_OF):
            assert owners[row.customer_ref] == scope.designer_ref
        for row in demo.search_customer_segment(scope, as_of=AS_OF, limit=100):
            assert owners[row.customer_ref] == scope.designer_ref


def test_another_designers_customer_ref_returns_none_not_an_error(
    demo: MockSalonDataProvider, scopes
):
    """不是報錯——報錯等於承認「這個 ref 存在」，那本身就是洩漏。"""
    mine, theirs = scopes[0], scopes[1]
    victim = demo.rank_customers_by_spend(theirs, days=3650, limit=1, as_of=AS_OF)[0]
    assert demo.get_customer_history(theirs, customer_ref=victim.customer_ref, as_of=AS_OF)
    assert demo.get_customer_history(mine, customer_ref=victim.customer_ref, as_of=AS_OF) is None


def test_another_designers_conversation_ref_returns_none(demo: MockSalonDataProvider, scopes):
    mine, theirs = scopes[0], scopes[1]
    victim = demo.list_recent_conversations(theirs, as_of=AS_OF, limit=1)[0]
    assert demo.get_conversation_transcript(theirs, conversation_ref=victim.conversation_ref)
    assert demo.get_conversation_transcript(mine, conversation_ref=victim.conversation_ref) is None


def test_customer_refs_filter_cannot_reach_across_scopes(demo: MockSalonDataProvider, scopes):
    mine, theirs = scopes[0], scopes[1]
    outsider = demo.rank_customers_by_spend(theirs, days=3650, limit=1, as_of=AS_OF)[0]
    rows = demo.list_recent_conversations(
        mine, as_of=AS_OF, customer_refs=[outsider.customer_ref], limit=50
    )
    assert rows == []


def test_an_unknown_ref_is_also_just_none(demo: MockSalonDataProvider, scopes):
    assert demo.get_customer_history(scopes[0], customer_ref="nope", as_of=AS_OF) is None
    assert demo.get_conversation_transcript(scopes[0], conversation_ref="nope") is None


# --- as_of、limit、封閉 enum ---------------------------------------------------


def test_as_of_is_the_only_now_there_is(demo: MockSalonDataProvider, scopes):
    """把 as_of 往回撥，之後才發生的到店就必須消失。"""
    scope = scopes[0]
    late = demo.rank_customers_by_spend(scope, days=3650, limit=50, as_of=AS_OF)
    early = demo.rank_customers_by_spend(
        scope, days=3650, limit=50, as_of=AS_OF - timedelta(days=365)
    )
    late_total = sum(row.visit_count for row in late)
    early_total = sum(row.visit_count for row in early)
    assert early_total < late_total
    for row in early:
        assert row.last_visit_at is None or row.last_visit_at <= AS_OF - timedelta(days=365)


def test_naive_as_of_is_refused(demo: MockSalonDataProvider, scopes):
    with pytest.raises(ValidationError):
        demo.rank_customers_by_spend(
            scopes[0], days=90, limit=5, as_of=datetime(2026, 9, 1, 0, 0)
        )


def test_limit_is_respected_everywhere(demo: MockSalonDataProvider, scopes):
    scope = scopes[0]
    assert len(demo.rank_customers_by_spend(scope, days=3650, limit=3, as_of=AS_OF)) == 3
    assert len(demo.list_inactive_customers(scope, inactive_days=1, limit=4, as_of=AS_OF)) == 4
    assert len(demo.search_customer_segment(scope, as_of=AS_OF, limit=6)) == 6
    assert len(demo.list_recent_conversations(scope, as_of=AS_OF, limit=2)) == 2
    assert len(demo.get_retention_watchlist(scope, as_of=AS_OF, limit=5)) == 5


def test_an_unknown_service_family_is_refused_not_silently_dropped(
    demo: MockSalonDataProvider, scopes
):
    with pytest.raises(ValidationError):
        demo.get_service_metrics(
            scopes[0],
            service_families=["highlight"],
            start_at=AS_OF - timedelta(days=30),
            end_at=AS_OF,
        )
    with pytest.raises(ValidationError):
        demo.search_customer_segment(scopes[0], as_of=AS_OF, service_families=["highlight"])


def test_out_of_range_limit_is_refused(demo: MockSalonDataProvider, scopes):
    with pytest.raises(ValidationError):
        demo.rank_customers_by_spend(scopes[0], days=90, limit=999, as_of=AS_OF)


# --- 語意 --------------------------------------------------------------------


def test_ranking_is_by_known_spend_descending(demo: MockSalonDataProvider, scopes):
    rows = demo.rank_customers_by_spend(scopes[0], days=90, limit=10, as_of=AS_OF)
    assert rows == sorted(rows, key=lambda r: -r.known_spend_twd)
    assert rows[0].known_spend_twd > 0


def test_inactive_list_is_longest_gap_first_and_honours_the_threshold(
    demo: MockSalonDataProvider, scopes
):
    rows = demo.list_inactive_customers(scopes[0], inactive_days=60, limit=10, as_of=AS_OF)
    assert rows
    assert [r.days_since_last_visit for r in rows] == sorted(
        (r.days_since_last_visit for r in rows), reverse=True
    )
    for row in rows:
        assert row.days_since_last_visit >= 60
        assert row.last_service is not None


def test_segment_search_matches_service_families(demo: MockSalonDataProvider, scopes):
    rows = demo.search_customer_segment(
        scopes[0],
        as_of=AS_OF,
        inactive_days_gte=60,
        service_families=[ServiceFamily.COLOR, ServiceFamily.PERM],
        limit=10,
    )
    assert rows
    for row in rows:
        assert row.days_since_last_visit >= 60
        assert set(row.matched_service_families) & {ServiceFamily.COLOR, ServiceFamily.PERM}


def test_segment_search_counts_visits_inside_a_period(demo: MockSalonDataProvider, scopes):
    since = AS_OF - timedelta(days=120)
    rows = demo.search_customer_segment(
        scopes[0], as_of=AS_OF, visits_since=since, visits_gte_in_period=2, limit=20
    )
    assert rows
    counts = []
    for row in rows:
        history = demo.get_customer_history(scopes[0], customer_ref=row.customer_ref, as_of=AS_OF)
        recent = [v for v in history.visits if v.visited_at >= since]
        assert len(recent) >= 2
        counts.append(len(recent))
    # AH-08「按次數排序」：問期間內來幾次，就照期間內的次數排，不是照總次數。
    assert counts == sorted(counts, reverse=True)


def test_period_threshold_without_a_window_is_refused_not_silently_ignored(
    demo: MockSalonDataProvider, scopes
):
    """舊行為：visits_gte_in_period=99 沒 visits_since → 門檻被 `if visits_since` 包死，
    靜默回全部 100 位。現在缺期間起點就當場 ValidationError，不准假裝篩過。"""
    with pytest.raises(ValidationError):
        demo.search_customer_segment(scopes[0], as_of=AS_OF, visits_gte_in_period=99)
    # 兩個都給時門檻真的生效：回來的每一位期間內到店數都達標。
    since = AS_OF - timedelta(days=120)
    rows = demo.search_customer_segment(
        scopes[0], as_of=AS_OF, visits_since=since, visits_gte_in_period=2, limit=100
    )
    for row in rows:
        history = demo.get_customer_history(scopes[0], customer_ref=row.customer_ref, as_of=AS_OF)
        assert len([v for v in history.visits if v.visited_at >= since]) >= 2


def test_an_empty_segment_is_an_empty_list_not_an_error(demo: MockSalonDataProvider, scopes):
    """考卷鐵律第 2 條：查不到就是查不到，不准補一位看起來合理的客人。

    回訪間隔本來就 28 天起跳，「最近 30 天來兩次以上」在這份示範資料裡沒有人符合。
    正確答案是空清單——不是報錯，也不是放寬條件湊一個人出來。
    """
    rows = demo.search_customer_segment(
        scopes[0],
        as_of=AS_OF,
        visits_since=AS_OF - timedelta(days=30),
        visits_gte_in_period=2,
        limit=20,
    )
    assert rows == []


def test_history_separates_known_spend_from_unknown_amount_visits(
    demo: MockSalonDataProvider, scopes
):
    scope = scopes[0]
    ranked = demo.rank_customers_by_spend(scope, days=3650, limit=50, as_of=AS_OF)
    refs = [row.customer_ref for row in ranked]
    with_unknown = None
    for ref in refs:
        history = demo.get_customer_history(scope, customer_ref=ref, as_of=AS_OF)
        assert history.visit_count == len(
            [v for v in history.visits if v.visited_at <= AS_OF]
        ) or history.visit_count >= len(history.visits)
        priced = [v.amount_twd for v in history.visits if v.amount_twd is not None]
        blanks = [v for v in history.visits if v.amount_twd is None]
        assert history.known_spend_twd == sum(priced)
        assert history.unknown_amount_visits == len(blanks)
        if blanks:
            with_unknown = history
    assert with_unknown is not None, "示範資料裡必須有缺金額的客人，不然這條規則沒被證明"
    # 缺金額的那幾次沒有被當成 0 元混進已知金額。
    assert with_unknown.visit_count > with_unknown.unknown_amount_visits


def test_history_limit_trims_the_visit_list_but_not_the_totals(
    demo: MockSalonDataProvider, scopes
):
    scope = scopes[0]
    ref = demo.rank_customers_by_spend(scope, days=3650, limit=1, as_of=AS_OF)[0].customer_ref
    full = demo.get_customer_history(scope, customer_ref=ref, as_of=AS_OF, limit=100)
    trimmed = demo.get_customer_history(scope, customer_ref=ref, as_of=AS_OF, limit=2)
    assert len(trimmed.visits) == 2
    assert trimmed.visit_count == full.visit_count
    assert trimmed.known_spend_twd == full.known_spend_twd
    # 留下的是最近兩次。
    assert [v.visited_at for v in trimmed.visits] == [v.visited_at for v in full.visits][:2]


def test_transcript_returns_the_last_messages_in_time_order(demo: MockSalonDataProvider, scopes):
    scope = scopes[0]
    summary = max(
        demo.list_recent_conversations(scope, as_of=AS_OF, limit=50),
        key=lambda c: c.message_count,
    )
    transcript = demo.get_conversation_transcript(
        scope, conversation_ref=summary.conversation_ref, message_limit=2
    )
    assert len(transcript.messages) == 2
    assert transcript.messages[0].created_at <= transcript.messages[1].created_at
    assert transcript.customer_ref == summary.customer_ref


def test_service_metrics_counts_people_visits_and_known_money_apart(
    demo: MockSalonDataProvider, scopes
):
    scope = scopes[0]
    start = AS_OF - timedelta(days=30)
    metrics = demo.get_service_metrics(
        scope, service_families=[ServiceFamily.COLOR], start_at=start, end_at=AS_OF
    )
    assert metrics.visit_count > 0
    assert metrics.linked_customer_count <= metrics.visit_count
    assert metrics.known_spend_twd > 0
    assert "已知金額" in metrics.coverage_note


def test_service_metrics_refuses_a_backwards_window(demo: MockSalonDataProvider, scopes):
    """參數寫反時，舊行為是回全 0 加一句很肯定的 coverage_note。provider 這一頭也要拒收。"""
    with pytest.raises(ValidationError):
        demo.get_service_metrics(
            scopes[0],
            service_families=[ServiceFamily.COLOR],
            start_at=AS_OF,
            end_at=AS_OF - timedelta(days=30),
        )


# --- 流失公式（固定算法） ------------------------------------------------------


def _write_fixture(tmp_path: Path) -> Path:
    """一份手寫的小資料，用來把流失分數算到小數點。"""
    d1 = "designer-one"
    d2 = "designer-two"
    designers = [
        {"designer_ref": d1, "display_name": "甲設計師", "store_name": "示範一店",
         "joined_at": "2024-01-01T10:00:00+08:00"},
        {"designer_ref": d2, "display_name": "乙設計師", "store_name": "示範二店",
         "joined_at": "2024-01-01T10:00:00+08:00"},
    ]
    customers = [
        {"customer_ref": "k-scored", "designer_ref": d1, "full_name": "王小明",
         "phone": "0912345678", "created_at": "2024-02-01T12:00:00+08:00",
         "line_user_ref": "demo-line-user-0001"},
        {"customer_ref": "k-too-fresh", "designer_ref": d1, "full_name": "陳怡",
         "phone": "0922333444", "created_at": "2024-02-01T12:00:00+08:00",
         "line_user_ref": "demo-line-user-0002"},
        {"customer_ref": "k-only-once", "designer_ref": d1, "full_name": "林大同",
         "phone": "0933444555", "created_at": "2024-02-01T12:00:00+08:00",
         "line_user_ref": "demo-line-user-0003"},
        {"customer_ref": "k-other-designer", "designer_ref": d2, "full_name": "黃小華",
         "phone": "0955666777", "created_at": "2024-02-01T12:00:00+08:00",
         "line_user_ref": "demo-line-user-0004"},
    ]

    def stamp(days_before: int) -> str:
        return (ANCHOR - timedelta(days=days_before)).isoformat()

    visits = []
    # k-scored：6 次到店，最後一次 100 天前；5 次各 2400 元＝12000，1 次缺金額。
    for index in range(5):
        visits.append(
            {"visit_ref": f"v-scored-{index}", "customer_ref": "k-scored", "designer_ref": d1,
             "visited_at": stamp(100 + index * 40), "service_family": "color",
             "amount_twd": 2400}
        )
    visits.append(
        {"visit_ref": "v-scored-5", "customer_ref": "k-scored", "designer_ref": d1,
         "visited_at": stamp(340), "service_family": "cut", "amount_twd": None}
    )
    # k-too-fresh：30 天前才來過，達不到 45 天的門檻。
    for index in range(3):
        visits.append(
            {"visit_ref": f"v-fresh-{index}", "customer_ref": "k-too-fresh", "designer_ref": d1,
             "visited_at": stamp(30 + index * 45), "service_family": "cut", "amount_twd": 1000}
        )
    # k-only-once：只來過一次，達不到 2 次的門檻。
    visits.append(
        {"visit_ref": "v-once-0", "customer_ref": "k-only-once", "designer_ref": d1,
         "visited_at": stamp(200), "service_family": "perm", "amount_twd": 5000}
    )
    # 別的設計師的客人，分數再高也不准出現在 d1 的名單裡。
    for index in range(9):
        visits.append(
            {"visit_ref": f"v-other-{index}", "customer_ref": "k-other-designer",
             "designer_ref": d2, "visited_at": stamp(200 + index * 20),
             "service_family": "bleach", "amount_twd": 6000}
        )

    payload = {
        "designers.json": designers,
        "customers.json": customers,
        "visits.json": visits,
        "appointments.json": [],
        "conversations.json": [],
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    for name, rows in payload.items():
        (tmp_path / name).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return tmp_path


def test_retention_score_matches_the_fixed_formula(tmp_path: Path):
    """min(days,180)*0.4 + min(visits,10)*4 + min(known_spend,20000)/1000。"""
    provider = MockSalonDataProvider(data_dir=_write_fixture(tmp_path / "fixture"))
    scope = DesignerScope(designer_ref="designer-one", display_name="甲設計師")
    rows = provider.get_retention_watchlist(scope, as_of=ANCHOR, minimum_inactive_days=45, limit=10)

    assert [r.customer_ref for r in rows] == ["k-scored"]
    row = rows[0]
    assert row.days_since_last_visit == 100
    assert row.visit_count == 6
    assert row.known_spend_twd == 12000
    # 100*0.4 + 6*4 + 12000/1000 = 40 + 24 + 12
    assert row.risk_score == pytest.approx(76.0)
    assert row.reasons
    assert any("100" in reason for reason in row.reasons)


def test_retention_caps_bite(tmp_path: Path):
    """天數、次數、金額都有上限：再久／再多／再貴也不會把分數衝到無限大。"""
    provider = MockSalonDataProvider(data_dir=_write_fixture(tmp_path / "fixture"))
    scope = DesignerScope(designer_ref="designer-two", display_name="乙設計師")
    row = provider.get_retention_watchlist(scope, as_of=ANCHOR, minimum_inactive_days=45)[0]
    assert row.customer_ref == "k-other-designer"
    assert row.days_since_last_visit == 200
    assert row.visit_count == 9
    assert row.known_spend_twd == 54000
    # min(200,180)*0.4 + min(9,10)*4 + min(54000,20000)/1000 = 72 + 36 + 20
    assert row.risk_score == pytest.approx(128.0)


def test_retention_floor_is_the_config_value_and_the_input_can_only_raise_it(tmp_path: Path):
    provider = MockSalonDataProvider(data_dir=_write_fixture(tmp_path / "fixture"))
    scope = DesignerScope(designer_ref="designer-one", display_name="甲設計師")
    # 傳 1 天也不會把 30 天前來過的人放進來：45 天是 config 的地板。
    loosened = provider.get_retention_watchlist(scope, as_of=ANCHOR, minimum_inactive_days=1)
    assert [row.customer_ref for row in loosened] == ["k-scored"]
    # 傳 120 天會把 100 天的那位擋掉。
    assert provider.get_retention_watchlist(scope, as_of=ANCHOR, minimum_inactive_days=120) == []


def test_thresholds_and_weights_come_from_config_not_from_hardcoded_numbers(tmp_path: Path):
    override = tmp_path / "tuned.yaml"
    override.write_text(
        "retention:\n  min_visits: 7\n  weights:\n    days: 1.0\n",
        encoding="utf-8",
    )
    provider = MockSalonDataProvider(
        data_dir=_write_fixture(tmp_path / "fixture"), config=load_config(override)
    )
    scope = DesignerScope(designer_ref="designer-one", display_name="甲設計師")
    # min_visits 拉到 7，六次到店的那位就掉出名單——數字真的是從 config 讀的。
    assert provider.get_retention_watchlist(scope, as_of=ANCHOR, minimum_inactive_days=45) == []

    other = DesignerScope(designer_ref="designer-two", display_name="乙設計師")
    row = provider.get_retention_watchlist(other, as_of=ANCHOR, minimum_inactive_days=45)[0]
    # 天數權重改成 1.0：min(200,180)*1.0 + 9*4 + 20 = 180 + 36 + 20
    assert row.risk_score == pytest.approx(236.0)


def test_watchlist_is_highest_risk_first(demo: MockSalonDataProvider, scopes):
    rows = demo.get_retention_watchlist(scopes[0], as_of=AS_OF, minimum_inactive_days=45, limit=10)
    assert rows == sorted(rows, key=lambda r: -r.risk_score)
    for row in rows:
        assert row.visit_count >= 2
        assert row.days_since_last_visit >= 45


# --- 拿考卷上的題目實際問一次 --------------------------------------------------


def test_the_sixty_day_question_from_the_exam_has_an_answer(demo: MockSalonDataProvider, scopes):
    """AH-02：超過 60 天沒回來的客人，按沒回來天數由久到短列 10 位。"""
    rows = demo.list_inactive_customers(scopes[0], inactive_days=60, limit=10, as_of=AS_OF)
    assert len(rows) == 10
    assert rows[0].days_since_last_visit >= rows[-1].days_since_last_visit >= 60
    assert all(row.full_name for row in rows)


def test_dates_come_back_in_taipei(demo: MockSalonDataProvider, scopes):
    row = demo.rank_customers_by_spend(scopes[0], days=3650, limit=1, as_of=AS_OF)[0]
    assert row.last_visit_at.tzinfo is not None
    assert row.last_visit_at.utcoffset() == timedelta(hours=8)
    assert row.last_visit_at.tzname() == datetime(2026, 9, 1, tzinfo=TAIPEI).tzname()
