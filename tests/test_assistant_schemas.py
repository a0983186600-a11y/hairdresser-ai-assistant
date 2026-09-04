"""schemas：封閉 enum、時間一律 Asia/Taipei aware、上下限照 tools.md。"""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from assistant.adapters.schemas import (
    CustomerHistory,
    CustomerHistoryInput,
    CustomerVisit,
    DesignerScope,
    InactiveCustomersInput,
    RankBySpendInput,
    RetentionWatchlistInput,
    SegmentSearchInput,
    ServiceFamily,
    ServiceMetricsInput,
    TranscriptInput,
)

TAIPEI = ZoneInfo("Asia/Taipei")
AS_OF = datetime(2026, 9, 1, 0, 0, tzinfo=TAIPEI)


def test_service_family_is_a_closed_enum():
    assert [f.value for f in ServiceFamily] == [
        "cut",
        "perm",
        "color",
        "treatment",
        "bleach",
        "scalp",
    ]


def test_unknown_service_family_is_rejected():
    with pytest.raises(ValidationError):
        SegmentSearchInput(as_of=AS_OF, service_families=["highlight"])


def test_visits_gte_in_period_requires_visits_since():
    # 只給期間門檻、不給期間起點：mock 會靜默忽略門檻、回全部客人（實跑 99 仍回 100 位）。
    # 這種「條件不生效也不報錯」不准存在——缺 visits_since 就當場拒收。
    with pytest.raises(ValidationError) as exc:
        SegmentSearchInput(as_of=AS_OF, visits_gte_in_period=99)
    assert "visits_since" in str(exc.value)
    # 兩個都給才是完整的期間查詢。
    ok = SegmentSearchInput(
        as_of=AS_OF,
        visits_since=AS_OF - timedelta(days=30),
        visits_gte_in_period=2,
    )
    assert ok.visits_gte_in_period == 2
    # 反向不受限：只給 visits_since（拿來排序）是合法的。
    assert SegmentSearchInput(as_of=AS_OF, visits_since=AS_OF).visits_gte_in_period is None


def test_naive_datetime_is_rejected():
    with pytest.raises(ValidationError):
        RankBySpendInput(days=90, limit=5, as_of=datetime(2026, 9, 1, 0, 0))


def test_aware_datetime_is_normalised_to_taipei():
    # UTC 進來，出去一定是 +08:00 的同一瞬間——不准在別處各自 astimezone。
    payload = RankBySpendInput(days=90, limit=5, as_of=datetime(2026, 8, 31, 16, 0, tzinfo=UTC))
    assert payload.as_of.utcoffset() == timedelta(hours=8)
    assert payload.as_of == datetime(2026, 9, 1, 0, 0, tzinfo=TAIPEI)
    assert payload.as_of.isoformat() == "2026-09-01T00:00:00+08:00"


def test_other_offsets_are_accepted_and_converted():
    payload = RankBySpendInput(
        days=90, limit=5, as_of=datetime(2026, 9, 1, 1, 0, tzinfo=timezone(timedelta(hours=9)))
    )
    assert payload.as_of == datetime(2026, 9, 1, 0, 0, tzinfo=TAIPEI)


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"days": 0, "limit": 5}, "days"),
        ({"days": 3651, "limit": 5}, "days"),
        ({"days": 90, "limit": 0}, "limit"),
        ({"days": 90, "limit": 51}, "limit"),
    ],
)
def test_rank_by_spend_bounds(kwargs, field):
    with pytest.raises(ValidationError) as exc:
        RankBySpendInput(as_of=AS_OF, **kwargs)
    assert field in str(exc.value)


def test_inactive_customers_limit_goes_to_100():
    assert InactiveCustomersInput(inactive_days=60, limit=100, as_of=AS_OF).limit == 100
    with pytest.raises(ValidationError):
        InactiveCustomersInput(inactive_days=60, limit=101, as_of=AS_OF)


def test_transcript_message_limit_caps_at_30():
    assert TranscriptInput(conversation_ref="c-1", message_limit=30).message_limit == 30
    with pytest.raises(ValidationError):
        TranscriptInput(conversation_ref="c-1", message_limit=31)


def test_retention_and_history_and_metrics_inputs_exist():
    assert RetentionWatchlistInput(as_of=AS_OF, minimum_inactive_days=45, limit=5).limit == 5
    assert CustomerHistoryInput(customer_ref="k-1", as_of=AS_OF, limit=10).limit == 10
    metrics = ServiceMetricsInput(
        service_families=["color", "bleach"],
        start_at=AS_OF - timedelta(days=30),
        end_at=AS_OF,
    )
    assert metrics.service_families == [ServiceFamily.COLOR, ServiceFamily.BLEACH]


def test_service_metrics_rejects_a_backwards_window():
    # start_at 在 end_at 之後：mock 的 `start_at <= moment <= end_at` 永遠為假，
    # 回全 0 卻附一句「已知金額合計」很肯定的 coverage_note。參數寫反要當場拒收。
    with pytest.raises(ValidationError) as exc:
        ServiceMetricsInput(
            service_families=["color"],
            start_at=AS_OF,
            end_at=AS_OF - timedelta(days=30),
        )
    message = str(exc.value)
    assert "start_at" in message and "end_at" in message
    # 相等（零寬視窗）是合法的邊界。
    same = ServiceMetricsInput(service_families=["color"], start_at=AS_OF, end_at=AS_OF)
    assert same.start_at == same.end_at


def test_customer_history_keeps_known_spend_and_unknown_count_apart():
    history = CustomerHistory(
        customer_ref="k-1",
        full_name="王小明",
        phone="0912345678",
        visit_count=3,
        known_spend_twd=4200,
        unknown_amount_visits=1,
        visits=[
            CustomerVisit(visited_at=AS_OF, service=ServiceFamily.CUT, amount_twd=1200),
            CustomerVisit(visited_at=AS_OF, service=ServiceFamily.COLOR, amount_twd=3000),
            CustomerVisit(visited_at=AS_OF, service=ServiceFamily.SCALP, amount_twd=None),
        ],
    )
    assert history.known_spend_twd == 4200
    assert history.unknown_amount_visits == 1
    # provider 回未遮罩原始資料；遮罩是 tools 層的事。
    assert history.full_name == "王小明"
    assert not hasattr(history, "masked_name")


def test_designer_scope_is_a_model_not_a_model_supplied_argument():
    scope = DesignerScope(designer_ref="d-1", display_name="示範設計師")
    assert scope.designer_ref == "d-1"
    # scope 由登入工作階段注入，不能出現在任何模型可填的 input 模型裡。
    for model in (
        RankBySpendInput,
        InactiveCustomersInput,
        SegmentSearchInput,
        CustomerHistoryInput,
        RetentionWatchlistInput,
        ServiceMetricsInput,
        TranscriptInput,
    ):
        assert "designer_ref" not in model.model_fields
        assert "designer_scope" not in model.model_fields
        assert "scope" not in model.model_fields
