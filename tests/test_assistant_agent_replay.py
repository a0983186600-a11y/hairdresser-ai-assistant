"""REPLAY_MODE：沒有金鑰也要能跑完一輪 demo。

工具照樣對資料跑一次，最終文字則沿用錄音。這些測試驗證固定示範資料的重現性，
不宣稱更換資料後錄音裡的姓名與數字也會更新。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from assistant.adapters.mock import MockSalonDataProvider
from assistant.agent import run_chat
from assistant.agent.replay import (
    NO_RECORDING_REPLY,
    ReplayClient,
    normalize_message,
    record,
)
from assistant.agent.types import ChatClient
from assistant.config.loader import load_config
from assistant.demo_data.generate import ANCHOR
from tests.test_assistant_agent_loop import ScriptedClient, calls, say, tool_call

# 「今天」＝示範資料集的錨點；錄音裡存的 as_of 也是它。
AS_OF = ANCHOR
QUESTION = "誰快流失了？先抓 3 位。"


@pytest.fixture(scope="module")
def config():
    return load_config()


@pytest.fixture(scope="module")
def provider(config):
    return MockSalonDataProvider(config=config)


@pytest.fixture(scope="module")
def scope(provider):
    return provider.designer_scopes()[0]


class CountingProvider:
    """數一數工具到底有沒有真的跑。"""

    def __init__(self, inner):
        self._inner = inner
        self.hits: list[str] = []

    def __getattr__(self, name):
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def wrapped(*args, **kwargs):
            self.hits.append(name)
            return attribute(*args, **kwargs)

        return wrapped


def _live_result(provider, scope, config):
    client = ScriptedClient(
        [
            calls(tool_call("c1", "get_retention_watchlist", {"limit": 3})),
            say("這三位最該先關心，分數與理由都在名單上。"),
        ]
    )
    return run_chat(
        QUESTION, provider=provider, scope=scope, config=config, as_of=AS_OF, client=client
    )


# --- 正規化 -------------------------------------------------------------------


def test_the_lookup_key_ignores_spacing_and_full_width_punctuation():
    assert normalize_message("誰快流失了？") == normalize_message(" 誰快流失了？ ")
    assert normalize_message("誰 快 流 失 了？") == normalize_message("誰快流失了？")
    # NFKC：全形英數與半形視為同一句。
    assert normalize_message("ＡＢＣ") == normalize_message("ABC")
    assert normalize_message("誰快流失了？") != normalize_message("誰回來了？")


# --- 錄 -----------------------------------------------------------------------


def test_recording_keeps_what_the_model_said_and_nothing_else(tmp_path, provider, scope, config):
    result = _live_result(provider, scope, config)
    path = record(result, tmp_path, "retention", recorded_at=AS_OF)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["slug"] == "retention"
    assert saved["user_message"] == QUESTION
    assert saved["normalized"] == normalize_message(QUESTION)
    assert saved["model"] == result.model
    assert saved["recorded_at"] == AS_OF.isoformat()
    assert saved["as_of"] == AS_OF.isoformat()

    rounds = saved["rounds"]
    assert len(rounds) == 2
    assert rounds[0]["tool_calls"][0]["function"]["name"] == "get_retention_watchlist"
    assert rounds[1]["content"] == "這三位最該先關心，分數與理由都在名單上。"
    assert rounds[1]["tool_calls"] is None
    # 錄音裡不准有工具結果：客人資料不落地，只有模型講的話。
    assert "rows" not in path.read_text(encoding="utf-8")


# --- 放 -----------------------------------------------------------------------


def test_a_replayed_turn_gives_the_same_reply(tmp_path, provider, scope, config):
    original = _live_result(provider, scope, config)
    record(original, tmp_path, "retention", recorded_at=AS_OF)

    replayed = run_chat(
        QUESTION,
        provider=provider,
        scope=scope,
        config=config,
        as_of=AS_OF,
        client=ReplayClient(tmp_path),
    )
    assert replayed.reply == original.reply
    assert [c.name for c in replayed.tool_calls] == [c.name for c in original.tool_calls]
    assert [c.arguments for c in replayed.tool_calls] == [c.arguments for c in original.tool_calls]


def test_the_tools_really_run_during_a_replay(tmp_path, provider, scope, config):
    """只證明工具確實執行；不把工具有執行誤當成最終錄音文字也會更新。"""
    record(_live_result(provider, scope, config), tmp_path, "retention", recorded_at=AS_OF)

    counting = CountingProvider(provider)
    replayed = run_chat(
        QUESTION,
        provider=counting,
        scope=scope,
        config=config,
        as_of=AS_OF,
        client=ReplayClient(tmp_path),
    )
    assert counting.hits == ["get_retention_watchlist"]
    assert json.loads(replayed.transcript[2]["content"])["row_count"] == 3


def test_a_replay_reports_no_model(tmp_path, provider, scope, config):
    record(_live_result(provider, scope, config), tmp_path, "retention", recorded_at=AS_OF)
    replayed = run_chat(
        QUESTION,
        provider=provider,
        scope=scope,
        config=config,
        as_of=AS_OF,
        client=ReplayClient(tmp_path),
    )
    # ChatResult.model is None 代表「這一輪沒有連模型」。
    assert replayed.model is None


def test_a_question_nobody_recorded_answers_politely_instead_of_crashing(
    tmp_path, provider, scope, config
):
    record(_live_result(provider, scope, config), tmp_path, "retention", recorded_at=AS_OF)
    result = run_chat(
        "幫我看一下明天的天氣",
        provider=provider,
        scope=scope,
        config=config,
        as_of=AS_OF,
        client=ReplayClient(tmp_path),
    )
    assert result.reply == NO_RECORDING_REPLY
    assert result.tool_calls == []


def test_an_empty_replay_directory_still_answers(tmp_path, provider, scope, config):
    result = run_chat(
        QUESTION,
        provider=provider,
        scope=scope,
        config=config,
        as_of=AS_OF,
        client=ReplayClient(tmp_path / "does-not-exist"),
    )
    assert result.reply == NO_RECORDING_REPLY


def test_the_replay_client_is_a_chat_client(tmp_path):
    assert isinstance(ReplayClient(tmp_path), ChatClient)


# --- 出貨的那六段 -------------------------------------------------------------


def test_the_shipped_recordings_replay_against_the_demo_data(provider, scope, config):
    """`assistant/replay/` 裡的錄音是 demo 的保命索：評審沒金鑰也要看得到六題。"""
    from assistant.agent.replay import REPLAY_DIR, load_recordings

    recordings = load_recordings(REPLAY_DIR)
    assert len(recordings) >= 6

    client = ReplayClient(REPLAY_DIR)
    for recording in recordings.values():
        as_of = datetime.fromisoformat(recording["as_of"])
        result = run_chat(
            recording["user_message"],
            provider=provider,
            scope=scope,
            config=config,
            as_of=as_of,
            client=client,
        )
        assert result.reply != NO_RECORDING_REPLY, recording["slug"]
        assert result.reply.strip(), recording["slug"]
