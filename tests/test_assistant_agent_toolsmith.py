"""工具工坊：提案 → 沙盒跑 → 人按「採用」→ 這次對話才多一支工具。

守的是「自己長工具」這件事**不會偷偷長進系統裡**：

1. 提案只是提案：跑完不改任何狀態，沒有人按採用就不會出現在工具清單上。
2. 採用只影響**這一段對話**，別的 session 看不到，也不會寫進磁碟。
3. 名字不准撞既有九個工具，也不准同一段對話裡撞自己。
4. 同一個問題最多試兩次；第三次直接被擋，模型只能老實說答不出來。
"""

from __future__ import annotations

import json

import pytest

from assistant.adapters.mock import MockSalonDataProvider
from assistant.agent import toolsmith as smith
from assistant.demo_data.generate import ANCHOR
from assistant.tools.registry import TOOL_NAMES, tool_schemas

GOOD_CODE = (
    "def run(provider, as_of):\n"
    "    rows = provider.list_inactive_customers(inactive_days=90, limit=10, as_of=as_of)\n"
    "    return [{'who': r['masked_name'], 'days': r['days_since_last_visit']} for r in rows]\n"
)
BAD_CODE = "import os\ndef run(provider, as_of):\n    return os.listdir('/')\n"
BOOM_CODE = "def run(provider, as_of):\n    return [][1]\n"


@pytest.fixture
def scope():
    return MockSalonDataProvider().designer_scopes()[0]


@pytest.fixture
def workshop():
    return smith.Toolsmith()


def _propose(workshop, scope, *, code=GOOD_CODE, name="inactive_90d", question="問句") -> dict:
    return workshop.run(
        smith.PROPOSE_TOOL_NAME,
        {"name": name, "description": "九十天沒回來的客人", "code": code, "question": question},
        scope=scope,
        as_of=ANCHOR,
    )


# --- 提案 -----------------------------------------------------------------------


def test_a_good_proposal_runs_in_the_sandbox_and_comes_back_ok(workshop, scope):
    payload = _propose(workshop, scope)

    assert payload["ok"] is True
    result = payload["result"]
    assert result["status"] == "ok"
    assert result["name"] == "inactive_90d"
    assert result["proposal_id"]
    assert result["code"] == GOOD_CODE
    assert result["row_count"] == 10
    assert json.loads(result["result_preview"])[0]["who"]


def test_the_preview_handed_to_the_model_is_capped(workshop, scope):
    payload = _propose(
        workshop,
        scope,
        code=(
            "def run(provider, as_of):\n"
            "    return [{'i': i, 'pad': 'x' * 200} for i in range(200)]\n"
        ),
    )
    assert payload["result"]["status"] == "ok"
    assert len(payload["result"]["result_preview"]) <= smith.PREVIEW_CHARS


def test_forbidden_code_comes_back_rejected_with_the_offending_lines(workshop, scope):
    payload = _propose(workshop, scope, code=BAD_CODE)

    assert payload["ok"] is True, "被拒絕不是工具壞了，是這一次提案不通過"
    assert payload["result"]["status"] == "rejected"
    assert payload["result"]["error"]["violations"][0]["line"] == 1
    assert workshop.adopted() == []


def test_code_that_blows_up_comes_back_as_an_error_not_an_exception(workshop, scope):
    payload = _propose(workshop, scope, code=BOOM_CODE)

    assert payload["ok"] is True
    assert payload["result"]["status"] == "error"
    assert payload["result"]["error"]["exception"] == "IndexError"


def test_a_proposal_changes_nothing_until_someone_adopts_it(workshop, scope):
    _propose(workshop, scope)

    assert workshop.adopted() == []
    names = [s["function"]["name"] for s in tool_schemas(_config(), toolsmith=workshop)]
    assert "inactive_90d" not in names
    assert smith.PROPOSE_TOOL_NAME in names


def test_a_name_that_collides_with_the_fixed_nine_is_refused(workshop, scope):
    payload = _propose(workshop, scope, name="get_customer_history")
    assert payload["result"]["status"] == "rejected"
    assert "已經有" in payload["result"]["error"]["message"]


def test_the_same_question_gets_at_most_two_attempts(workshop, scope):
    first = _propose(workshop, scope, code=BAD_CODE, question="同一題")
    second = _propose(workshop, scope, code=BAD_CODE, question="同一題")
    third = _propose(workshop, scope, code=GOOD_CODE, question="同一題")

    assert first["result"]["status"] == "rejected"
    assert second["result"]["status"] == "rejected"
    assert third["result"]["status"] == "rejected"
    assert "已經試過" in third["result"]["error"]["message"]
    # 換一個問題就重新算次數。
    assert _propose(workshop, scope, question="另一題")["result"]["status"] == "ok"


# --- 採用 -----------------------------------------------------------------------


def _config():
    from assistant.config.loader import load_config

    return load_config()


def test_adopting_puts_the_tool_on_this_conversation_and_nowhere_else(workshop, scope):
    proposal_id = _propose(workshop, scope)["result"]["proposal_id"]
    other = smith.Toolsmith()

    adopted = workshop.adopt(proposal_id)

    assert adopted["name"] == "inactive_90d"
    assert [tool["name"] for tool in workshop.adopted()] == ["inactive_90d"]
    assert other.adopted() == []

    names = [s["function"]["name"] for s in tool_schemas(_config(), toolsmith=workshop)]
    assert "inactive_90d" in names
    assert len(names) == len(TOOL_NAMES) + 1 + 1  # 九個固定的 ＋ 提案工具 ＋ 新採用的
    assert [s["function"]["name"] for s in tool_schemas(_config())] == list(TOOL_NAMES)


def test_an_adopted_tool_runs_again_through_the_sandbox(workshop, scope):
    workshop.adopt(_propose(workshop, scope)["result"]["proposal_id"])

    payload = workshop.run("inactive_90d", {}, scope=scope, as_of=ANCHOR)

    assert payload["ok"] is True
    assert payload["result"]["row_count"] == 10
    assert len(payload["result"]["rows"]) == 10


def test_adopting_a_proposal_that_did_not_run_is_refused(workshop, scope):
    proposal_id = _propose(workshop, scope, code=BAD_CODE)["result"]["proposal_id"]

    with pytest.raises(smith.ToolsmithError) as caught:
        workshop.adopt(proposal_id)
    assert caught.value.status == 409


def test_adopting_an_unknown_proposal_is_refused(workshop):
    with pytest.raises(smith.ToolsmithError) as caught:
        workshop.adopt("no-such-proposal")
    assert caught.value.status == 404


def test_adopting_the_same_name_twice_is_refused(workshop, scope):
    first = _propose(workshop, scope)["result"]["proposal_id"]
    second = _propose(workshop, scope, question="第二題")["result"]["proposal_id"]

    workshop.adopt(first)
    with pytest.raises(smith.ToolsmithError) as caught:
        workshop.adopt(second)
    assert caught.value.status == 409


def test_nothing_is_ever_written_to_disk(workshop, scope, tmp_path, monkeypatch):
    """工具只活在記憶體裡：重開服務就沒了，這是刻意的。"""
    monkeypatch.chdir(tmp_path)
    workshop.adopt(_propose(workshop, scope)["result"]["proposal_id"])
    assert list(tmp_path.iterdir()) == []


# --- 提案工具本身的形狀 ---------------------------------------------------------


def test_the_propose_tool_declares_the_four_fields_the_model_must_fill(workshop):
    schema = next(
        s for s in workshop.schemas() if s["function"]["name"] == smith.PROPOSE_TOOL_NAME
    )
    properties = schema["function"]["parameters"]["properties"]
    assert sorted(properties) == ["code", "description", "name", "question"]
    assert sorted(schema["function"]["parameters"]["required"]) == [
        "code",
        "description",
        "name",
        "question",
    ]
    assert "run(provider, as_of)" in schema["function"]["description"]


def test_bad_arguments_come_back_as_a_fixable_error(workshop, scope):
    payload = workshop.run(
        smith.PROPOSE_TOOL_NAME, {"name": "x"}, scope=scope, as_of=ANCHOR
    )
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_arguments"
    assert payload["error"]["problems"]


def test_the_workshop_only_handles_its_own_tools(workshop, scope):
    assert workshop.handles(smith.PROPOSE_TOOL_NAME)
    assert not workshop.handles("get_customer_history")
    workshop.adopt(_propose(workshop, scope)["result"]["proposal_id"])
    assert workshop.handles("inactive_90d")


# --- 每個瀏覽器 session 一間工坊 ------------------------------------------------


def test_the_store_keeps_one_workshop_per_session_and_finds_proposals_by_id(scope):
    store = smith.ToolsmithStore()
    first = store.acquire("session-a")
    second = store.acquire("session-b")

    assert first is not second
    assert store.acquire("session-a") is first

    proposal_id = _propose(first, scope)["result"]["proposal_id"]
    assert store.owner_of(proposal_id) is first
    assert store.owner_of("nope") is None


def test_a_workshop_born_without_a_session_gets_bound_after_the_first_answer(scope):
    store = smith.ToolsmithStore()
    fresh = store.acquire(None)
    store.bind("brand-new", fresh)

    assert store.acquire("brand-new") is fresh


# --- 給模型的那份說明書，要跟沙盒真的交出去的東西一致 ---------------------------


def _run(workshop, scope, code):
    payload = workshop.run(
        smith.PROPOSE_TOOL_NAME,
        {"name": "probe_shapes", "description": "看形狀", "code": code, "question": "形狀？"},
        scope=scope,
        as_of=ANCHOR,
    )
    assert payload["result"]["status"] == "ok", payload["result"].get("error")
    return json.loads(payload["result"]["result_preview"])


@pytest.mark.parametrize(
    ("method", "call"),
    [
        ("list_inactive_customers", "provider.list_inactive_customers("
                                    "inactive_days=1, limit=1, as_of=as_of)"),
        ("get_retention_watchlist", "provider.get_retention_watchlist(limit=1, as_of=as_of)"),
        ("list_recent_conversations", "provider.list_recent_conversations(limit=1, as_of=as_of)"),
    ],
)
def test_the_reference_lists_the_keys_a_list_method_really_hands_over(
    workshop, scope, method, call
):
    """說明書上的鍵名是從 pydantic 模型長出來的；這一條確認它跟沙盒實際交出去的一樣。

    手抄一份遲早會對不上，而對不上的那天模型會寫 `row['full_name']` 然後 KeyError，
    卡片上只看得到一句莫名其妙的錯誤。
    """
    real = _run(workshop, scope, f"def run(provider, as_of):\n    return {call}\n")
    assert real, f"{method} 在示範資料上回了空的，這條就驗不到東西"

    line = next(row for row in smith.provider_reference().splitlines() if method in row)
    for key in real[0]:
        assert key in line, f"{method} 實際回了 {key}，說明書上沒有"


def test_the_reference_gets_the_nested_and_masked_keys_right(workshop, scope):
    """巢狀那一層才是真正要算的東西；逐字稿那一層則一定是遮罩過的鍵名。"""
    history = _run(
        workshop,
        scope,
        "def run(provider, as_of):\n"
        "    rows = provider.list_inactive_customers(inactive_days=1, limit=1, as_of=as_of)\n"
        "    one = provider.get_customer_history("
        "customer_ref=rows[0]['customer_ref'], as_of=as_of, limit=3)\n"
        "    return [one['visits'][0]]\n",
    )
    line = next(
        row for row in smith.provider_reference().splitlines() if "get_customer_history" in row
    )
    for key in history[0]:
        assert key in line, f"visits 實際有 {key}，說明書上沒有"

    talk = _run(
        workshop,
        scope,
        "def run(provider, as_of):\n"
        "    rows = provider.list_recent_conversations(limit=1, as_of=as_of)\n"
        "    one = provider.get_conversation_transcript("
        "conversation_ref=rows[0]['conversation_ref'], message_limit=1)\n"
        "    return [one['messages'][0]]\n",
    )
    assert "redacted_content" in talk[0], "逐字稿交出去的是遮罩過的那一格"
    line = next(
        row
        for row in smith.provider_reference().splitlines()
        if "get_conversation_transcript" in row
    )
    assert "redacted_content" in line and "、content" not in line


def test_the_prompt_hands_the_model_the_signatures_instead_of_letting_it_guess():
    """不給它，它就只能猜——2026-09-05 實跑漏了 as_of、把 list 當成 dict。"""
    prompt = smith.toolsmith_prompt()

    for name in TOOL_NAMES[:8]:
        assert name in prompt, name
    assert "as_of" in prompt
    assert "ISO 8601" in prompt, "回的是字串不是 datetime，這件事一定要講"


# --- 卡片上那張表 ---------------------------------------------------------------


def test_the_card_finds_the_table_even_when_it_is_wrapped_in_a_dict(workshop, scope):
    """模型常常回 `{'breakdown': [...], 'total': 546}`——要看的表包在裡面。

    2026-09-05 實跑（qwen3.7-max，「哪一天最忙」那一題）回的就是這個形狀。
    """
    payload = _propose(
        workshop,
        scope,
        code=(
            "def run(provider, as_of):\n"
            "    return {'total': 3, 'note': 'x',\n"
            "            'breakdown': [{'day': 'Mon', 'n': 1}, {'day': 'Tue', 'n': 2}]}\n"
        ),
    )

    card = payload["card"]
    assert card["rows"] == [{"day": "Mon", "n": 1}, {"day": "Tue", "n": 2}]
    assert card["row_count"] == 3, "row_count 講的還是 run() 回的那個東西有幾格"


def test_no_table_is_drawn_when_there_is_nothing_table_shaped(workshop, scope):
    """寧可少一張表，也不要端出一張猜錯的表。"""
    payload = _propose(
        workshop,
        scope,
        code="def run(provider, as_of):\n    return {'answer': 42, 'why': ['a', 'b']}\n",
    )

    assert payload["card"]["rows"] is None
    assert "42" in payload["result"]["result_preview"]
