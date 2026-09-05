"""`run_chat` 的簽名是第二階段兩個人（agent 迴圈、前端伺服器）的共同依據。

這一支釘的不是行為，是**介面**：名稱、參數、預設值、回傳型別。
第二階段兩邊各寫各的，靠這個簽名對接；誰改名改參數，這裡先紅。
"""

import inspect
import json
from datetime import datetime

import pytest

from assistant.adapters.mock import MockSalonDataProvider
from assistant.agent import run_chat
from assistant.agent.types import ChatClient, ChatResult, ChatSession, ToolCallRecord
from assistant.config.loader import load_config
from assistant.demo_data.generate import ANCHOR

# 「今天」＝示範資料集的錨點，從資料那邊 import，不在測試裡抄一份字面日期。
AS_OF = ANCHOR


def test_run_chat_signature_is_frozen():
    signature = inspect.signature(run_chat)
    params = signature.parameters
    assert list(params) == [
        "message",
        "provider",
        "scope",
        "config",
        "as_of",
        "session",
        "client",
        "toolsmith",
    ]
    assert params["message"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("provider", "scope", "config", "as_of", "session", "client", "toolsmith"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
    assert params["provider"].default is inspect.Parameter.empty
    assert params["scope"].default is inspect.Parameter.empty
    assert params["config"].default is inspect.Parameter.empty
    # as_of 必填：這一層不准讀系統時鐘，唯一的「現在」由呼叫端傳進來。
    assert params["as_of"].default is inspect.Parameter.empty
    assert params["as_of"].annotation in (datetime, "datetime")
    assert params["session"].default is None
    assert params["client"].default is None
    # client 不再是「隨便什麼」：型別釘成 ChatClient Protocol，第二階段兩邊靠它對接。
    assert params["client"].annotation == "ChatClient | None"
    # toolsmith 是後來加的第七個洞：這一段對話的工具工坊。**預設 None**——
    # 不給就是原本那九個固定工具，所以「助理會自己長工具」是一個選配的能力，
    # 不是每個呼叫端都被迫接受的行為。
    assert params["toolsmith"].default is None
    assert signature.return_annotation in (ChatResult, "ChatResult")


def test_chat_client_is_a_protocol_phase_two_can_implement_against():
    """client 這個洞要有型別，不然「塞一個假的進來」兩邊會塞成不同形狀。"""
    assert getattr(ChatClient, "_is_protocol", False) is True
    complete = inspect.signature(ChatClient.complete)
    params = complete.parameters
    assert list(params) == ["self", "messages", "tools", "model"]
    assert params["messages"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params["tools"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    # model 是 keyword-only：呼叫端一定要指名用哪個模型，不准靠位置賭。
    assert params["model"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["model"].default is inspect.Parameter.empty


def test_a_conforming_fake_client_satisfies_the_protocol():
    """runtime_checkable：湊齊 complete 的鴨子就算數，缺了就不算——測試靠這個塞假的。"""

    class _FakeClient:
        def complete(self, messages, tools, *, model):
            return {"role": "assistant", "content": "嗨", "tool_calls": None}

    class _NotAClient:
        def chat(self):  # 沒有 complete
            return {}

    assert isinstance(_FakeClient(), ChatClient)
    assert not isinstance(_NotAClient(), ChatClient)


def test_run_chat_is_implemented_and_needs_no_network_when_a_client_is_injected():
    """第二階段接上了本體。這一支只確認「注入 client 就不碰網路」這個洞還在——
    伺服器、測試與 REPLAY_MODE 三邊都靠它。"""

    class _FakeClient:
        def complete(self, messages, tools, *, model):
            return {"role": "assistant", "content": "沒有符合條件的資料。", "tool_calls": None}

    provider = MockSalonDataProvider()
    result = run_chat(
        "誰快流失了？",
        provider=provider,
        scope=provider.designer_scopes()[0],
        config=load_config(),
        as_of=AS_OF,
        client=_FakeClient(),
    )
    assert isinstance(result, ChatResult)
    assert result.reply == "沒有符合條件的資料。"


def test_run_chat_without_a_client_and_without_credentials_says_which_env_var_is_missing(
    monkeypatch,
):
    """沒有 client 又沒有金鑰時要講清楚缺哪個變數，不要丟一個 KeyError 就算了。"""
    from assistant.agent.http_client import MissingModelCredentials

    config = load_config()
    monkeypatch.delenv(config.model.base_url_env, raising=False)
    monkeypatch.delenv(config.model.api_key_env, raising=False)

    provider = MockSalonDataProvider()
    with pytest.raises(MissingModelCredentials) as exc:
        run_chat(
            "誰快流失了？",
            provider=provider,
            scope=provider.designer_scopes()[0],
            config=config,
            as_of=AS_OF,
        )
    assert config.model.api_key_env in str(exc.value)


def test_chat_session_carries_history():
    session = ChatSession(session_id="s-1", history=[{"role": "user", "content": "嗨"}])
    assert session.session_id == "s-1"
    assert session.history[0]["content"] == "嗨"
    # 沒給就是空的，不是 None——呼叫端可以直接 append。
    assert ChatSession(session_id="s-2").history == []


def test_tool_call_record_is_what_the_ui_shows_under_the_answer():
    record = ToolCallRecord(
        name="get_retention_watchlist",
        arguments={"minimum_inactive_days": 45, "limit": 5},
        result_summary="5 位；最高風險 128.0",
        duration_ms=12,
    )
    assert record.name == "get_retention_watchlist"
    assert record.arguments["limit"] == 5
    assert record.duration_ms == 12


def test_chat_result_round_trips_through_json():
    result = ChatResult(
        reply="有 5 位值得先關心。",
        tool_calls=[
            ToolCallRecord(
                name="get_retention_watchlist",
                arguments={"limit": 5},
                result_summary="5 位",
                duration_ms=8,
            )
        ],
        transcript=[{"role": "user", "content": "誰快流失了？"}],
        model="qwen-plus",
    )
    again = ChatResult.model_validate(json.loads(result.model_dump_json()))
    assert again == result
    # 沒接模型（REPLAY_MODE）時 model 是 None，不是空字串。
    assert ChatResult(reply="嗨").model is None
    assert ChatResult(reply="嗨").tool_calls == []
