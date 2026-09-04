"""agent 迴圈：模型講話、伺服器動手。

測試一律塞假 client（`ChatClient` Protocol 的鴨子），因為要驗的不是模型聰不聰明，
是**伺服器這一側的紀律**：注入 scope 與今天、夾住參數、遮罩、迴圈上限、
未知工具、失敗重試、逐字稿完整。這些不准隨模型心情改變。
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from assistant.adapters.mock import MockSalonDataProvider
from assistant.agent import run_chat
from assistant.agent.loop import IRON_RULES, build_system_prompt
from assistant.agent.types import ChatClient, ChatSession
from assistant.config.loader import load_config
from assistant.demo_data.generate import ANCHOR

# 「今天」＝示範資料集的錨點，從資料那邊 import，不在測試裡抄一份字面日期。
AS_OF = ANCHOR


def tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


class ScriptedClient:
    """照劇本回覆的假模型；順便把每次收到的 messages/tools 留下來給測試檢查。"""

    def __init__(self, script: list[dict], *, repeat_last: bool = False) -> None:
        self.script = list(script)
        self.repeat_last = repeat_last
        self.seen: list[dict] = []

    def complete(self, messages: list[dict], tools: list[dict], *, model: str) -> dict:
        self.seen.append({"messages": [dict(m) for m in messages], "tools": tools, "model": model})
        if len(self.script) == 1 and self.repeat_last:
            return dict(self.script[0])
        if not self.script:
            return {"role": "assistant", "content": "沒有更多了", "tool_calls": None}
        return dict(self.script.pop(0))


def say(text: str) -> dict:
    return {"role": "assistant", "content": text, "tool_calls": None}


def calls(*items: dict) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": list(items)}


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


def chat(message, provider, scope, config, client, *, as_of=AS_OF, session=None):
    return run_chat(
        message,
        provider=provider,
        scope=scope,
        config=config,
        as_of=as_of,
        session=session,
        client=client,
    )


# --- 提示詞 -------------------------------------------------------------------


def test_the_system_prompt_carries_the_persona_and_all_five_iron_rules(config):
    prompt = build_system_prompt(config, AS_OF)
    assert config.persona.strip() in prompt
    assert len(IRON_RULES) == 5
    for rule in IRON_RULES:
        assert rule in prompt


def test_today_in_the_prompt_is_as_of_and_not_the_wall_clock(config):
    """考卷第一句：「今天」固定指傳進來的那一刻，不是模型執行當天。"""
    earlier = AS_OF - timedelta(days=1)
    prompt = build_system_prompt(config, AS_OF)
    other = build_system_prompt(config, earlier)

    assert AS_OF.date().isoformat() in prompt
    assert earlier.date().isoformat() in other
    assert prompt != other


def test_a_naive_as_of_is_refused(provider, scope, config):
    with pytest.raises(ValueError, match="時區"):
        chat(
            "嗨",
            provider,
            scope,
            config,
            ScriptedClient([say("嗨")]),
            as_of=AS_OF.replace(tzinfo=None),
        )


def test_the_prompt_reaches_the_model_as_the_first_message(provider, scope, config):
    client = ScriptedClient([say("好")])
    chat("誰快流失了？", provider, scope, config, client)
    first = client.seen[0]["messages"][0]
    assert first["role"] == "system"
    assert IRON_RULES[0] in first["content"]
    assert client.seen[0]["messages"][1] == {"role": "user", "content": "誰快流失了？"}


def test_the_model_is_told_which_model_it_is(provider, scope, config, monkeypatch):
    monkeypatch.setenv(config.model.model_env, "qwen-test-1")
    client = ScriptedClient([say("好")])
    result = chat("嗨", provider, scope, config, client)
    assert client.seen[0]["model"] == "qwen-test-1"
    assert result.model == "qwen-test-1"


def test_the_model_default_is_used_when_the_env_var_is_missing(
    provider, scope, config, monkeypatch
):
    monkeypatch.delenv(config.model.model_env, raising=False)
    client = ScriptedClient([say("好")])
    assert chat("嗨", provider, scope, config, client).model == config.model.model_default


# --- 工具呼叫 -----------------------------------------------------------------


def test_a_tool_call_runs_for_real_and_the_answer_comes_back(provider, scope, config):
    client = ScriptedClient(
        [
            calls(tool_call("c1", "get_retention_watchlist", {"limit": 3})),
            say("這三位先關心。"),
        ]
    )
    result = chat("誰快流失了？", provider, scope, config, client)

    assert result.reply == "這三位先關心。"
    assert [record.name for record in result.tool_calls] == ["get_retention_watchlist"]
    assert result.tool_calls[0].arguments == {"limit": 3}
    assert result.tool_calls[0].result_summary
    assert result.tool_calls[0].duration_ms >= 0

    payload = json.loads(client.seen[1]["messages"][-1]["content"])
    assert payload["ok"] is True
    assert len(payload["rows"]) == 3


def test_the_scope_the_model_writes_is_dropped_before_the_tool_runs(
    provider, scope, other_scope, config
):
    """模型看不到 designer_ref，但它可能自己編一個——編了也要落空。"""
    forged = ScriptedClient(
        [
            calls(
                tool_call(
                    "c1",
                    "rank_customers_by_spend",
                    {"days": 90, "limit": 3, "designer_ref": other_scope.designer_ref},
                )
            ),
            say("好了"),
        ]
    )
    honest = ScriptedClient(
        [
            calls(tool_call("c1", "rank_customers_by_spend", {"days": 90, "limit": 3})),
            say("好了"),
        ]
    )
    chat("消費前三名", provider, scope, config, forged)
    chat("消費前三名", provider, scope, config, honest)
    assert forged.seen[1]["messages"][-1]["content"] == honest.seen[1]["messages"][-1]["content"]


def test_what_goes_back_to_the_model_is_masked(provider, scope, config):
    client = ScriptedClient(
        [
            calls(tool_call("c1", "rank_customers_by_spend", {"days": 3650, "limit": 50})),
            say("好了"),
        ]
    )
    chat("消費排行", provider, scope, config, client)

    rows = json.loads(client.seen[1]["messages"][-1]["content"])["rows"]
    assert rows
    blob = json.dumps(client.seen[1]["messages"], ensure_ascii=False)
    by_ref = {record.ref: record for record in provider._mine(scope)}
    for row in rows:
        raw = by_ref[row["customer_ref"]]
        assert raw.full_name not in blob
        assert raw.phone not in blob
        assert row["phone_last4"] == raw.phone[-4:]


def test_an_over_the_ceiling_limit_is_clamped_instead_of_blowing_up(provider, scope, config):
    client = ScriptedClient(
        [
            calls(tool_call("c1", "rank_customers_by_spend", {"days": 90, "limit": 999})),
            say("好了"),
        ]
    )
    result = chat("排行", provider, scope, config, client)
    payload = json.loads(client.seen[1]["messages"][-1]["content"])
    assert payload["ok"] is True
    assert payload["clamped"] == {"limit": 50}
    assert result.reply == "好了"


def test_an_unknown_tool_name_comes_back_as_a_tool_message_so_the_model_can_retry(
    provider, scope, config
):
    client = ScriptedClient(
        [
            calls(tool_call("c1", "get_the_secret_stuff", {})),
            calls(tool_call("c2", "get_retention_watchlist", {"limit": 2})),
            say("改用流失名單。"),
        ]
    )
    result = chat("誰快流失了？", provider, scope, config, client)

    first = client.seen[1]["messages"][-1]
    assert first["role"] == "tool"
    assert first["tool_call_id"] == "c1"
    assert json.loads(first["content"])["error"]["code"] == "unknown_tool"
    assert result.reply == "改用流失名單。"
    assert [record.name for record in result.tool_calls] == [
        "get_the_secret_stuff",
        "get_retention_watchlist",
    ]


def test_unparsable_arguments_are_an_error_not_a_crash(provider, scope, config):
    broken = {
        "id": "c1",
        "type": "function",
        "function": {"name": "get_retention_watchlist", "arguments": "{這不是 JSON"},
    }
    client = ScriptedClient([calls(broken), say("好")])
    result = chat("誰快流失了？", provider, scope, config, client)
    assert json.loads(client.seen[1]["messages"][-1]["content"])["error"]["code"] == "bad_arguments"
    assert result.reply == "好"


def test_several_tool_calls_in_one_round_all_run(provider, scope, config):
    client = ScriptedClient(
        [
            calls(
                tool_call("c1", "get_retention_watchlist", {"limit": 2}),
                tool_call("c2", "list_inactive_customers", {"inactive_days": 60, "limit": 2}),
            ),
            say("兩份都看過了。"),
        ]
    )
    result = chat("兩件事", provider, scope, config, client)
    assert [record.name for record in result.tool_calls] == [
        "get_retention_watchlist",
        "list_inactive_customers",
    ]
    tail = client.seen[1]["messages"][-2:]
    assert [m["tool_call_id"] for m in tail] == ["c1", "c2"]


# --- 上限與失敗 ---------------------------------------------------------------


def test_the_loop_stops_at_max_iterations_and_says_what_it_has(provider, scope, config):
    """不准無限打工具：撞到上限就收尾，把已經查到的講出來。"""
    client = ScriptedClient(
        [calls(tool_call("c1", "get_retention_watchlist", {"limit": 2}))], repeat_last=True
    )
    result = chat("誰快流失了？", provider, scope, config, client)

    assert len(client.seen) == config.agent.max_iterations
    assert len(result.tool_calls) == config.agent.max_iterations
    assert result.reply.startswith("我查了太多輪，先把目前找到的整理給你")
    assert "get_retention_watchlist" in result.reply


def test_a_flaky_model_call_is_retried_once(provider, scope, config):
    class FlakyClient:
        def __init__(self):
            self.attempts = 0

        def complete(self, messages, tools, *, model):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("connection reset")
            return say("第二次就好了")

    client = FlakyClient()
    assert chat("嗨", provider, scope, config, client).reply == "第二次就好了"
    assert client.attempts == 2


def test_a_model_that_fails_twice_surfaces_the_error(provider, scope, config):
    class DeadClient:
        def complete(self, messages, tools, *, model):
            raise RuntimeError("connection reset")

    with pytest.raises(RuntimeError, match="connection reset"):
        chat("嗨", provider, scope, config, DeadClient())


def test_a_reply_with_neither_content_nor_tool_calls_does_not_hang(provider, scope, config):
    client = ScriptedClient([{"role": "assistant", "content": None, "tool_calls": None}])
    result = chat("嗨", provider, scope, config, client)
    assert result.reply.strip()


# --- 逐字稿與工作階段 ---------------------------------------------------------


def test_the_transcript_shows_every_step_plus_the_model_and_today(provider, scope, config):
    client = ScriptedClient(
        [
            calls(tool_call("c1", "get_retention_watchlist", {"limit": 2})),
            say("兩位。"),
        ]
    )
    result = chat("誰快流失了？", provider, scope, config, client)

    roles = [entry["role"] for entry in result.transcript]
    assert roles == ["user", "assistant", "tool", "assistant", "meta"]
    assert result.transcript[0]["content"] == "誰快流失了？"
    assert result.transcript[1]["tool_calls"][0]["function"]["name"] == "get_retention_watchlist"
    assert json.loads(result.transcript[2]["content"])["ok"] is True
    assert result.transcript[3]["content"] == "兩位。"

    meta = result.transcript[-1]
    assert meta["as_of"] == AS_OF.isoformat()
    assert meta["model"] == result.model
    # system 提示詞不進逐字稿：UI 要看的是「問了什麼、查了什麼」。
    assert "system" not in roles


def test_the_transcript_is_json_serialisable(provider, scope, config):
    client = ScriptedClient(
        [calls(tool_call("c1", "get_retention_watchlist", {"limit": 1})), say("好")],
    )
    result = chat("誰快流失了？", provider, scope, config, client)
    json.dumps(result.transcript, ensure_ascii=False)


def test_a_session_remembers_the_previous_turn(provider, scope, config):
    session = ChatSession(session_id="s-1")
    chat("誰快流失了？", provider, scope, config, ScriptedClient([say("兩位。")]), session=session)

    client = ScriptedClient([say("好")])
    chat("那第一位呢？", provider, scope, config, client, session=session)

    sent = [m for m in client.seen[0]["messages"] if m["role"] != "system"]
    assert sent[0]["content"] == "誰快流失了？"
    assert sent[1]["content"] == "兩位。"
    assert sent[-1] == {"role": "user", "content": "那第一位呢？"}
    assert not any(m.get("role") == "meta" for m in session.history)


def test_the_fake_clients_here_really_satisfy_the_protocol():
    assert isinstance(ScriptedClient([]), ChatClient)
