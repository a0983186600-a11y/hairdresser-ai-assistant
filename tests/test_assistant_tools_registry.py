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
    names = [schema["function"]["name"] for schema in registry.tool_schemas(config)]
    assert names[:8] == list(registry.PROVIDER_TOOL_NAMES)
    assert registry.DRAFT_TOOL_NAME in names
    assert len(names) == 9
    assert len(set(names)) == 9


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
