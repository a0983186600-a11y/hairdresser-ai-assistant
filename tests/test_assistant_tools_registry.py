"""工具層：模型看得到什麼、拿得到什麼。

這一層是「模型」與「真資料」之間唯一的門，所以四件事要在這裡被釘死：

1. **模型看不到 scope 也看不到 as_of**——這兩個由伺服器注入，模型填了也會被丟掉。
2. **出去的每一列都遮罩過**：全名與完整電話不准離開這一層。
3. **limit 超界用夾的，不是報錯**：模型寫 999 是常態，為這個中斷一輪很蠢。
4. **空結果要長得像空結果**：`rows: []` 加一句「沒有符合」，模型才不會去編一位客人
   （exam.md 鐵律 2）。
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from assistant.adapters.mock import MockSalonDataProvider
from assistant.config.loader import load_config
from assistant.demo_data.generate import ANCHOR
from assistant.tools import registry

# 示範資料集的錨點就是這一層的「今天」（理由見 assistant/demo_data/README.md）。
# 從那裡 import 而不是抄一份字面日期：資料錨點改了測試要跟著動，不是各寫各的。
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
def other_scope(provider):
    return provider.designer_scopes()[1]


def _call(name, arguments, provider, scope, config, as_of=AS_OF):
    return registry.dispatch(name, arguments, provider, scope, config, as_of=as_of)


# --- schema ------------------------------------------------------------------


def test_every_tool_in_tools_md_is_declared_plus_the_drafting_one(config):
    """tools.md 那八個要在最前面、順序一樣，第九個是確定性草稿。

    後面還接著兩個提案工具（見本檔最後一節），所以這裡釘的是**前九個**，
    不是總數——總數由那一節自己守。
    """
    names = [schema["function"]["name"] for schema in registry.tool_schemas(config)]
    assert names[:8] == list(registry.PROVIDER_TOOL_NAMES)
    assert names[8] == registry.DRAFT_TOOL_NAME
    assert len(set(names)) == len(names)


def test_the_schema_is_openai_function_calling_shaped(config):
    for schema in registry.tool_schemas(config):
        assert schema["type"] == "function"
        function = schema["function"]
        assert isinstance(function["name"], str)
        assert function["description"].strip(), function["name"]
        parameters = function["parameters"]
        assert parameters["type"] == "object"
        assert isinstance(parameters["properties"], dict)
        # additionalProperties=False：模型多塞一個欄位要當場看得出來，不要靜默吃掉。
        assert parameters["additionalProperties"] is False


def test_the_model_can_never_ask_for_a_scope_or_a_today(config):
    """scope 是授權、as_of 是「現在」，兩個都由伺服器注入。

    schema 裡留一個 designer_ref 的洞，等於讓模型有機會要別人的客人；留一個 as_of，
    等於讓模型用它訓練資料裡的今天去查——考卷第一句就在講這件事。
    """
    banned = {"scope", "designer_ref", "as_of", "designer_scope"}
    for schema in registry.tool_schemas(config):
        properties = set(schema["function"]["parameters"]["properties"])
        assert not (properties & banned), schema["function"]["name"]
        assert not (set(schema["function"]["parameters"].get("required", [])) & banned)


def test_service_families_stay_a_closed_enum_in_the_schema(config):
    by_name = {s["function"]["name"]: s for s in registry.tool_schemas(config)}
    metrics = by_name["get_service_metrics"]["function"]["parameters"]
    families = metrics["properties"]["service_families"]
    assert families["type"] == "array"
    assert families["items"]["enum"] == ["cut", "perm", "color", "treatment", "bleach", "scalp"]


def test_the_drafting_tool_offers_exactly_the_configured_reasons(config):
    by_name = {s["function"]["name"]: s for s in registry.tool_schemas(config)}
    draft = by_name[registry.DRAFT_TOOL_NAME]["function"]["parameters"]
    assert draft["properties"]["reason"]["enum"] == [t.id for t in config.follow_up_templates]
    assert set(draft["required"]) == {"customer_ref", "reason"}


def test_the_schema_is_json_serialisable(config):
    """要原封不動送進 OpenAI 相容端點；有 pydantic 物件殘留在裡面就會在那裡才炸。"""
    json.dumps(registry.tool_schemas(config), ensure_ascii=False)


# --- 注入與授權 ---------------------------------------------------------------


def test_a_scope_the_model_made_up_is_thrown_away(provider, scope, other_scope, config):
    honest = _call("rank_customers_by_spend", {"days": 90, "limit": 5}, provider, scope, config)
    forged = _call(
        "rank_customers_by_spend",
        {
            "days": 90,
            "limit": 5,
            "scope": {"designer_ref": other_scope.designer_ref},
            "designer_ref": other_scope.designer_ref,
        },
        provider,
        scope,
        config,
    )
    assert forged == honest
    theirs = _call(
        "rank_customers_by_spend",
        {"days": 90, "limit": 5},
        provider,
        other_scope,
        config,
    )
    assert theirs["rows"] and theirs != honest


def test_a_today_the_model_made_up_is_thrown_away(provider, scope, config):
    """模型寫 as_of 也沒用：查的「現在」永遠是呼叫端傳進來的那一個。"""
    plain = _call(
        "list_inactive_customers",
        {"inactive_days": 60, "limit": 5},
        provider,
        scope,
        config,
    )
    lying = _call(
        "list_inactive_customers",
        {
            "inactive_days": 60,
            "limit": 5,
            "as_of": (AS_OF - timedelta(days=600)).isoformat(),
        },
        provider,
        scope,
        config,
    )
    assert lying == plain

    later = _call(
        "list_inactive_customers",
        {"inactive_days": 60, "limit": 5},
        provider,
        scope,
        config,
        as_of=AS_OF + timedelta(days=91),
    )
    assert later != plain


def test_another_designers_customer_ref_is_just_not_found(provider, scope, other_scope, config):
    theirs = _call(
        "rank_customers_by_spend",
        {"days": 3650, "limit": 1},
        provider,
        other_scope,
        config,
    )
    stolen = theirs["rows"][0]["customer_ref"]

    result = _call("get_customer_history", {"customer_ref": stolen}, provider, scope, config)
    assert result["ok"] is False
    assert result["error"]["code"] == "not_found"
    # 「找不到」不等於「存在但不給你」——訊息不准洩漏那個 ref 是真的。
    assert stolen not in json.dumps(result, ensure_ascii=False)


# --- 遮罩 ---------------------------------------------------------------------


def _raw_customer(provider, scope):
    return next(iter(provider._mine(scope)))


def test_no_full_name_and_no_full_phone_ever_reaches_the_model(provider, scope, config):
    record = _raw_customer(provider, scope)
    blob = json.dumps(
        [
            _call("rank_customers_by_spend", {"days": 3650, "limit": 50}, provider, scope, config),
            _call(
                "list_inactive_customers",
                {"inactive_days": 1, "limit": 100},
                provider,
                scope,
                config,
            ),
            _call(
                "search_customer_segment",
                {"visits_gte": 1, "limit": 100},
                provider,
                scope,
                config,
            ),
            _call("get_retention_watchlist", {"limit": 50}, provider, scope, config),
            _call("get_customer_history", {"customer_ref": record.ref}, provider, scope, config),
        ],
        ensure_ascii=False,
    )
    assert record.full_name not in blob
    assert record.phone not in blob
    assert record.phone[-4:] in blob


def test_rows_carry_masked_name_and_phone_last4_not_the_raw_columns(provider, scope, config):
    rows = _call(
        "rank_customers_by_spend",
        {"days": 3650, "limit": 3},
        provider,
        scope,
        config,
    )["rows"]
    assert rows
    for row in rows:
        assert "full_name" not in row and "phone" not in row
        assert "○" in row["masked_name"]
        assert row["phone_last4"] is None or len(row["phone_last4"]) == 4


def test_known_spend_and_missing_amounts_stay_two_separate_numbers(provider, scope, config):
    """鐵律 3：金額有缺值要講得出「缺幾筆」，所以這兩欄不准合併成一個總額。"""
    rows = _call(
        "rank_customers_by_spend",
        {"days": 3650, "limit": 10},
        provider,
        scope,
        config,
    )["rows"]
    for row in rows:
        assert "known_spend_twd" in row
        assert "unknown_amount_visits" in row


def test_transcripts_come_back_redacted(provider, scope, config):
    conversations = _call("list_recent_conversations", {"limit": 1}, provider, scope, config)
    ref = conversations["rows"][0]["conversation_ref"]
    result = _call(
        "get_conversation_transcript",
        {"conversation_ref": ref},
        provider,
        scope,
        config,
    )
    messages = result["result"]["messages"]
    assert messages
    for message in messages:
        # tools.md 寫的是 redacted_content：欄位名要提醒模型這是遮罩過的。
        assert "redacted_content" in message
        assert "content" not in message


# --- 夾住、報錯、空的 ---------------------------------------------------------


def test_a_limit_over_the_ceiling_is_clamped_not_rejected(provider, scope, config):
    result = _call("rank_customers_by_spend", {"days": 90, "limit": 999}, provider, scope, config)
    assert result["ok"] is True
    assert len(result["rows"]) <= 50
    assert result["clamped"] == {"limit": 50}


def test_a_limit_under_the_floor_is_clamped_too(provider, scope, config):
    result = _call(
        "list_inactive_customers",
        {"inactive_days": 1, "limit": 0},
        provider,
        scope,
        config,
    )
    assert result["ok"] is True
    assert result["clamped"] == {"limit": 1}
    assert len(result["rows"]) <= 1


def test_a_made_up_service_family_comes_back_as_a_fixable_error(provider, scope, config):
    result = _call(
        "get_service_metrics",
        {
            "service_families": ["haircut"],
            "start_at": (AS_OF - timedelta(days=31)).isoformat(),
            "end_at": AS_OF.isoformat(),
        },
        provider,
        scope,
        config,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_arguments"
    # 要讓模型改得動：告訴它合法值是哪些。
    assert result["error"]["allowed"]["service_families"] == [
        "cut",
        "perm",
        "color",
        "treatment",
        "bleach",
        "scalp",
    ]


def test_an_unknown_tool_name_is_an_error_the_model_can_recover_from(provider, scope, config):
    result = _call("get_the_secret_stuff", {}, provider, scope, config)
    assert result["ok"] is False
    assert result["error"]["code"] == "unknown_tool"
    assert "rank_customers_by_spend" in result["error"]["allowed"]["tool"]


def test_an_empty_result_says_so_out_loud(provider, scope, config):
    """鐵律 2：空的就要看起來是空的，否則模型會「幫忙」補一位客人。"""
    result = _call(
        "list_inactive_customers",
        {"inactive_days": 3650, "limit": 10},
        provider,
        scope,
        config,
    )
    assert result["ok"] is True
    assert result["rows"] == []
    assert result["row_count"] == 0
    assert "沒有符合" in result["note"]


def test_rows_are_truncated_to_the_configured_context_budget(provider, scope, config):
    small = config.model_copy(deep=True)
    small.agent.tool_result_limit = 2
    result = _call("rank_customers_by_spend", {"days": 3650, "limit": 50}, provider, scope, small)
    assert len(result["rows"]) == 2
    assert result["truncated"] is True
    assert result["row_count"] > 2  # 總數要照實講，截的是「看幾筆」不是「有幾筆」


# --- 第 9 個工具：確定性草稿 --------------------------------------------------


def test_the_draft_is_written_by_the_template_not_by_the_model(provider, scope, config):
    watch = _call("get_retention_watchlist", {"limit": 1}, provider, scope, config)["rows"][0]
    result = _call(
        registry.DRAFT_TOOL_NAME,
        {"customer_ref": watch["customer_ref"], "reason": "gentle_checkin"},
        provider,
        scope,
        config,
    )
    assert result["ok"] is True
    draft = result["result"]
    template = next(t for t in config.follow_up_templates if t.id == "gentle_checkin")
    expected = template.text.format(
        name=watch["masked_name"],
        service=draft["service"],
        days=watch["days_since_last_visit"],
    )
    assert draft["text"] == expected
    assert draft["template_id"] == "gentle_checkin"
    # 草稿裡也不准有全名。
    assert draft["text"].startswith(watch["masked_name"])


def test_the_draft_refuses_a_customer_outside_the_scope(provider, scope, other_scope, config):
    theirs = _call(
        "rank_customers_by_spend",
        {"days": 3650, "limit": 1},
        provider,
        other_scope,
        config,
    )
    stolen = theirs["rows"][0]["customer_ref"]
    result = _call(
        registry.DRAFT_TOOL_NAME,
        {"customer_ref": stolen, "reason": "gentle_checkin"},
        provider,
        scope,
        config,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "not_found"


def test_the_draft_rejects_a_template_that_does_not_exist(provider, scope, config):
    watch = _call("get_retention_watchlist", {"limit": 1}, provider, scope, config)["rows"][0]
    result = _call(
        registry.DRAFT_TOOL_NAME,
        {"customer_ref": watch["customer_ref"], "reason": "我自己想的理由"},
        provider,
        scope,
        config,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_arguments"
    assert result["error"]["allowed"]["reason"] == [t.id for t in config.follow_up_templates]


def test_the_draft_never_calls_the_model(provider, scope, config, monkeypatch):
    """確定性層：這個工具跟模型端點一點關係都沒有，斷網也要能出稿。"""
    import assistant.agent.http_client as http_client

    def explode(*args, **kwargs):  # pragma: no cover - 被呼叫就代表壞了
        raise AssertionError("draft_follow_up_message 不准連模型")

    monkeypatch.setattr(http_client.HttpChatClient, "complete", explode)
    watch = _call("get_retention_watchlist", {"limit": 1}, provider, scope, config)["rows"][0]
    result = _call(
        registry.DRAFT_TOOL_NAME,
        {"customer_ref": watch["customer_ref"], "reason": "service_due"},
        provider,
        scope,
        config,
    )
    assert result["ok"] is True


# --- 第 10、11 個工具：提案（只讀、回結構，人按了才寫） --------------------------
#
# 這兩個工具是「聊天 → 確認卡 → 才寫入」那條路的第一段。它們**永遠不寫**：
# 寫入只發生在設計師按下確認卡之後，由前端打既有的 POST /api/workbench/actions。
# 所以這一節守三件事：拆得出來的欄位要對、拆不出來的要進 missing（不准補預設值）、
# 呼叫前後工作台狀態一模一樣。


def _propose(name, arguments, provider, scope, config, as_of=AS_OF):
    result = _call(name, arguments, provider, scope, config, as_of=as_of)
    assert result["ok"] is True, result
    return result["result"]


def _one_customer(provider, scope, config):
    """示範名單裡遮罩姓名與末四碼**都唯一**的一位客人（「找得到人」的樣本）。"""
    from collections import Counter

    rows = _call(
        "search_customer_segment", {"limit": 100}, provider, scope, config
    )["rows"]
    names = Counter(row["masked_name"] for row in rows)
    phones = Counter(row["phone_last4"] for row in rows)
    return next(
        row
        for row in rows
        if names[row["masked_name"]] == 1
        and row["phone_last4"]
        and phones[row["phone_last4"]] == 1
    )


def test_both_proposal_tools_are_declared_next_to_the_read_only_ones(config):
    names = [schema["function"]["name"] for schema in registry.tool_schemas(config)]
    assert names[:8] == list(registry.PROVIDER_TOOL_NAMES)
    assert registry.DRAFT_TOOL_NAME in names
    for name in registry.PROPOSAL_TOOL_NAMES:
        assert name in names, name
    assert len(names) == 11
    assert len(set(names)) == 11


def test_a_complete_sentence_becomes_an_action_the_write_endpoint_accepts(
    provider, scope, config
):
    """「明天下午三點 ○○○ 剪髮」→ 一筆 POST /api/workbench/actions 吃得下的 payload。"""
    who = _one_customer(provider, scope, config)
    proposal = _propose(
        "propose_booking",
        {"customer": who["masked_name"], "start": "明天下午三點", "service": "剪髮"},
        provider,
        scope,
        config,
    )

    assert proposal["kind"] == "book"
    assert proposal["missing"] == []
    assert proposal["action"] == {
        "kind": "book",
        "data": {
            "customer_ref": who["customer_ref"],
            "date": (AS_OF + timedelta(days=1)).date().isoformat(),
            "time": "15:00",
            "services": ["cut"],
        },
    }
    assert proposal["fields"]["service_label"] == "剪髮"
    assert proposal["fields"]["duration_minutes"] == 60
    assert who["masked_name"] in proposal["summary"]
    assert proposal["proposal_id"]


def test_the_proposal_never_hands_back_a_full_name(provider, scope, config):
    who = _one_customer(provider, scope, config)
    proposal = _propose(
        "propose_booking",
        {"customer": who["phone_last4"], "start": "明天 15:00", "service": "cut"},
        provider,
        scope,
        config,
    )
    assert proposal["fields"]["customer_ref"] == who["customer_ref"]
    assert "○" in proposal["fields"]["customer_label"]


def test_a_field_the_model_could_not_pull_out_goes_to_missing_not_to_a_default(
    provider, scope, config
):
    """空的服務 ≠ 剪髮，看不懂的時間 ≠ 今天。缺就是缺，而且缺了就沒有 action。"""
    proposal = _propose("propose_booking", {}, provider, scope, config)

    assert sorted(proposal["missing"]) == ["customer", "service", "start"]
    assert proposal["action"] is None
    assert proposal["fields"]["service_id"] is None
    assert proposal["fields"]["date"] is None
    assert proposal["fields"]["time"] is None
    assert proposal["fields"]["duration_minutes"] is None


def test_a_time_with_no_day_is_missing_rather_than_guessed_as_today(
    provider, scope, config
):
    who = _one_customer(provider, scope, config)
    proposal = _propose(
        "propose_booking",
        {"customer": who["masked_name"], "start": "三點", "service": "剪髮"},
        provider,
        scope,
        config,
    )
    assert proposal["missing"] == ["start"]
    assert proposal["action"] is None
    assert "日期" in proposal["note"]


def test_a_name_that_matches_more_than_one_customer_asks_instead_of_picking(
    provider, scope, config
):
    from collections import Counter

    rows = _call(
        "search_customer_segment", {"limit": 100}, provider, scope, config
    )["rows"]
    counts = Counter(row["masked_name"] for row in rows)
    shared = next(name for name, count in counts.items() if count > 1)

    proposal = _propose(
        "propose_booking",
        {"customer": shared, "start": "明天 15:00", "service": "剪髮"},
        provider,
        scope,
        config,
    )
    assert proposal["missing"] == ["customer"]
    assert proposal["action"] is None
    assert "末四碼" in proposal["note"]


def test_a_customer_nobody_has_heard_of_is_missing_not_invented(provider, scope, config):
    proposal = _propose(
        "propose_booking",
        {"customer": "查無此人", "start": "明天 15:00", "service": "剪髮"},
        provider,
        scope,
        config,
    )
    assert proposal["missing"] == ["customer"]
    assert proposal["fields"]["customer_ref"] is None
    assert proposal["action"] is None


def test_a_service_that_is_not_on_the_price_list_is_missing_not_sixty_minutes(
    provider, scope, config
):
    who = _one_customer(provider, scope, config)
    proposal = _propose(
        "propose_booking",
        {"customer": who["masked_name"], "start": "明天 15:00", "service": "接髮"},
        provider,
        scope,
        config,
    )
    assert proposal["missing"] == ["service"]
    assert proposal["fields"]["duration_minutes"] is None
    assert proposal["action"] is None


def test_a_price_the_shop_never_set_is_reported_unresolved_not_blocking(
    provider, scope, config
):
    """示範項目表沒有填價格。缺價格不准擋住排單——排單那條路根本不寫價格。"""
    who = _one_customer(provider, scope, config)
    proposal = _propose(
        "propose_booking",
        {"customer": who["masked_name"], "start": "明天 15:00", "service": "剪髮"},
        provider,
        scope,
        config,
    )
    assert proposal["fields"]["price_twd"] is None
    assert proposal["unresolved"] == ["price_twd"]
    assert proposal["missing"] == []
    assert proposal["action"] is not None


def test_the_designer_can_say_the_price_and_it_is_not_written_into_the_booking(
    provider, scope, config
):
    who = _one_customer(provider, scope, config)
    proposal = _propose(
        "propose_booking",
        {
            "customer": who["masked_name"],
            "start": "明天 15:00",
            "service": "剪髮",
            "price_twd": 800,
        },
        provider,
        scope,
        config,
    )
    assert proposal["fields"]["price_twd"] == 800
    assert "800" in proposal["summary"]
    # 排單的寫入 payload 沒有價格這一格（BookingInput extra="forbid"）。
    assert set(proposal["action"]["data"]) == {"customer_ref", "date", "time", "services"}


def test_a_price_change_proposes_a_settings_merge_not_a_whole_new_settings_object(
    provider, scope, config
):
    proposal = _propose(
        "propose_service_price",
        {"service": "剪髮", "price_twd": 800, "duration_minutes": 75},
        provider,
        scope,
        config,
    )
    assert proposal["kind"] == "settings"
    assert proposal["missing"] == []
    assert proposal["action"] == {
        "kind": "settings",
        "merge": "service",
        "data": {"service": {"id": "cut", "duration": 75, "price": 800}},
    }
    assert "剪髮" in proposal["summary"]


def test_a_price_change_with_nothing_to_change_asks_what_to_change(
    provider, scope, config
):
    proposal = _propose("propose_service_price", {"service": "剪髮"}, provider, scope, config)
    assert sorted(proposal["missing"]) == ["duration_minutes", "price_twd"]
    assert proposal["action"] is None


def test_a_price_change_for_an_unknown_service_lists_the_ones_that_exist(
    provider, scope, config
):
    proposal = _propose(
        "propose_service_price", {"service": "接髮", "price_twd": 800}, provider, scope, config
    )
    assert proposal["missing"] == ["service"]
    assert proposal["action"] is None
    assert "剪髮" in proposal["note"]


def test_an_out_of_range_price_is_a_fixable_error_not_a_silent_clamp(
    provider, scope, config
):
    result = _call(
        "propose_service_price",
        {"service": "剪髮", "price_twd": 999999},
        provider,
        scope,
        config,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_arguments"
    assert "cut" in result["error"]["allowed"]["service"]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("propose_booking", {"customer": "何○", "start": "明天 15:00", "service": "剪髮"}),
        ("propose_service_price", {"service": "剪髮", "price_twd": 800}),
    ],
)
def test_calling_a_proposal_tool_changes_nothing(name, arguments, provider, scope, config):
    """提案工具是只讀的：同一台工作台，呼叫前後那份狀態一個字都不能變。"""
    import json as _json

    from assistant.demo_data.generate import load_dataset
    from assistant.server import DEMO_PAGES, FIXTURES_DIR
    from assistant.workbench import Workbench

    bench = Workbench(
        {
            page: _json.loads((FIXTURES_DIR / f"{page}.json").read_text("utf-8"))
            for page in DEMO_PAGES
        },
        load_dataset(),
        as_of=AS_OF,
        designer_ref=scope.designer_ref,
        calendar_key="fixed-for-this-test",
    )
    before = _json.dumps(bench.snapshot(), ensure_ascii=False, sort_keys=True)

    _call(name, arguments, provider, scope, config)

    assert _json.dumps(bench.snapshot(), ensure_ascii=False, sort_keys=True) == before
