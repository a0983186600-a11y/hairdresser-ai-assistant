"""「助理自己長工具」那條路的伺服器面：提案怎麼回到瀏覽器、採用怎麼進門。

守的是三件會出事的事：

1. **提案不改任何狀態。** 聊完一輪，工具清單一個字都不會變；只有人按下「採用」
   打了 `POST /api/workbench/tools/adopt` 才會多一支。
2. **採用只影響那一段對話。** 別的 session 打 `GET /api/workbench/tools` 看到空的，
   固定九個也一個字沒動。不落地，重啟就沒了。
3. **正式模式一支都不准長。** 唯讀就是唯讀，回 403 並說是示範限定，
   不是安靜地照做。跨站送過來的一樣擋掉（跟工作台那些動作同一條規矩）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assistant.agent import toolsmith as smith
from assistant.demo_data.generate import ANCHOR

FRONTEND = Path(__file__).resolve().parents[1] / "assistant" / "frontend"

GOOD_CODE = (
    "def run(provider, as_of):\n"
    "    rows = provider.list_inactive_customers(inactive_days=90, limit=5, as_of=as_of)\n"
    "    return [{'who': r['masked_name'], 'days': r['days_since_last_visit']} for r in rows]\n"
)
BAD_CODE = "import os\ndef run(provider, as_of):\n    return os.listdir('/')\n"

SERVER_ENV_NAMES = (
    "DEMO_MODE",
    "PRODUCTION_READ_URL",
    "REPLAY_MODE",
    "ASSISTANT_DESIGNER_REF",
    "BACKOFFICE_API_BASE",
    "ASSISTANT_AS_OF",
)


class _ProposeThenTalk:
    """假模型：第一輪叫 propose_new_tool，第二輪講一句話收尾。

    不連任何端點——這一支測的是伺服器怎麼把提案帶回瀏覽器、採用怎麼進門，
    不是模型寫得好不好。
    """

    is_replay = False

    def __init__(self, code: str = GOOD_CODE, name: str = "inactive_90d") -> None:
        self.code = code
        self.name = name
        self.tools_seen: list[list[str]] = []

    def complete(self, messages: list[dict], tools: list[dict], *, model: str) -> dict:
        self.tools_seen.append([t["function"]["name"] for t in tools])
        if any(entry.get("role") == "tool" for entry in messages):
            return {"role": "assistant", "content": "我寫了一支，你看看要不要採用。"}
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": smith.PROPOSE_TOOL_NAME,
                        "arguments": json.dumps(
                            {
                                "name": self.name,
                                "description": "九十天沒回來的客人",
                                "code": self.code,
                                "question": "九十天沒回來的有誰？",
                            }
                        ),
                    },
                }
            ],
        }


@pytest.fixture
def clean_env(monkeypatch):
    for name in SERVER_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def make_client(clean_env, client_stub=None, **env: str) -> TestClient:
    """一個測試一台伺服器：工坊掛在 app 上，共用會互相污染。"""
    from assistant import server

    for name, value in env.items():
        clean_env.setenv(name, value)
    application = server.create_app()
    application.state.runtime.as_of_override = ANCHOR
    if client_stub is not None:
        application.state.runtime.client = client_stub
    return TestClient(application)


def _propose(client: TestClient, session_id: str | None = None) -> dict:
    payload = {"message": "九十天沒回來的有誰？", "session_id": session_id}
    answer = client.post("/api/chat", json=payload)
    assert answer.status_code == 200, answer.text
    return answer.json()


def _card_of(answer: dict) -> dict:
    calls = [call for call in answer["tool_calls"] if call.get("tool_proposal")]
    assert calls, f"提案沒有帶回瀏覽器：{answer['tool_calls']}"
    return calls[0]["tool_proposal"]


# --- 提案回到瀏覽器 -------------------------------------------------------------


def test_the_proposal_comes_back_with_the_code_and_the_result(clean_env):
    """卡片上要看得到程式碼與跑出來的結果——採用是人的動作，人得先看得到。"""
    client = make_client(clean_env, client_stub=_ProposeThenTalk(), DEMO_MODE="1")

    card = _card_of(_propose(client))

    assert card["kind"] == "tool_proposal"
    assert card["status"] == "ok"
    assert card["name"] == "inactive_90d"
    assert card["code"] == GOOD_CODE
    assert card["proposal_id"]
    assert len(card["rows"]) == 5
    assert card["rows"][0]["who"]


def test_a_query_tool_never_brings_its_rows_to_the_browser(clean_env):
    """只有提案工具會帶東西給 UI。查詢工具的完整結果照舊留在伺服器上。"""
    client = make_client(clean_env, client_stub=_ProposeThenTalk(), DEMO_MODE="1")

    answer = _propose(client)

    for call in answer["tool_calls"]:
        if call["name"] != smith.PROPOSE_TOOL_NAME:
            assert call.get("tool_proposal") is None, call["name"]


def test_proposing_changes_nothing_until_someone_adopts(clean_env):
    """聊完一輪，這段對話的工具清單還是空的。"""
    client = make_client(clean_env, client_stub=_ProposeThenTalk(), DEMO_MODE="1")

    answer = _propose(client)
    listed = client.get("/api/workbench/tools", params={"session_id": answer["session_id"]})

    assert listed.status_code == 200
    assert listed.json()["tools"] == []


def test_forbidden_code_comes_back_as_a_rejected_card_not_a_500(clean_env):
    """模型寫了 `import os`：卡片上寫「被拒絕」，伺服器照常回 200。"""
    client = make_client(clean_env, client_stub=_ProposeThenTalk(code=BAD_CODE), DEMO_MODE="1")

    card = _card_of(_propose(client))

    assert card["status"] == "rejected"
    assert card["error"]["violations"][0]["line"] == 1


# --- 採用 -----------------------------------------------------------------------


def test_adopting_puts_the_tool_on_this_session_and_nowhere_else(clean_env):
    stub = _ProposeThenTalk()
    client = make_client(clean_env, client_stub=stub, DEMO_MODE="1")
    answer = _propose(client)
    session_id = answer["session_id"]

    adopted = client.post(
        "/api/workbench/tools/adopt", json={"proposal_id": _card_of(answer)["proposal_id"]}
    )

    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["adopted"]["name"] == "inactive_90d"

    mine = client.get("/api/workbench/tools", params={"session_id": session_id}).json()
    assert [tool["name"] for tool in mine["tools"]] == ["inactive_90d"]

    # 另一段對話什麼都沒多。
    other = client.get("/api/workbench/tools", params={"session_id": "someone-else"}).json()
    assert other["tools"] == []


def test_an_adopted_tool_is_offered_to_the_model_next_turn(clean_env):
    """採用之後，下一輪的工具清單裡才有它——而且只有這一段對話有。"""
    stub = _ProposeThenTalk()
    client = make_client(clean_env, client_stub=stub, DEMO_MODE="1")
    answer = _propose(client)
    client.post(
        "/api/workbench/tools/adopt", json={"proposal_id": _card_of(answer)["proposal_id"]}
    )

    before = list(stub.tools_seen)
    assert all("inactive_90d" not in seen for seen in before), "採用之前不該出現"

    _propose(client, session_id=answer["session_id"])
    assert "inactive_90d" in stub.tools_seen[-1]

    # 換一段對話問，清單裡就沒有它。
    _propose(client, session_id="a-different-conversation")
    assert "inactive_90d" not in stub.tools_seen[-1]


def test_the_fixed_nine_are_still_exactly_nine_after_an_adoption(clean_env):
    """長出來的工具不准擠進固定清單。"""
    from assistant.config.loader import load_config
    from assistant.tools.registry import TOOL_NAMES, tool_schemas

    stub = _ProposeThenTalk()
    client = make_client(clean_env, client_stub=stub, DEMO_MODE="1")
    answer = _propose(client)
    client.post(
        "/api/workbench/tools/adopt", json={"proposal_id": _card_of(answer)["proposal_id"]}
    )

    fixed = [s["function"]["name"] for s in tool_schemas(load_config())]
    assert fixed == list(TOOL_NAMES)


def test_adopting_a_proposal_that_did_not_run_is_refused(clean_env):
    client = make_client(clean_env, client_stub=_ProposeThenTalk(code=BAD_CODE), DEMO_MODE="1")
    card = _card_of(_propose(client))

    refused = client.post("/api/workbench/tools/adopt", json={"proposal_id": card["proposal_id"]})

    assert refused.status_code == 409


def test_adopting_an_unknown_proposal_is_refused(clean_env):
    client = make_client(clean_env, client_stub=_ProposeThenTalk(), DEMO_MODE="1")

    refused = client.post("/api/workbench/tools/adopt", json={"proposal_id": "no-such-thing"})

    assert refused.status_code == 404


def test_a_cross_site_adopt_is_refused(clean_env):
    """跟工作台那些動作同一條規矩：請從工作台本身操作。"""
    client = make_client(clean_env, client_stub=_ProposeThenTalk(), DEMO_MODE="1")
    card = _card_of(_propose(client))

    refused = client.post(
        "/api/workbench/tools/adopt",
        json={"proposal_id": card["proposal_id"]},
        headers={"sec-fetch-site": "cross-site"},
    )

    assert refused.status_code == 403
    assert "工作台" in refused.json()["detail"]


# --- 正式模式一支都不准長 -------------------------------------------------------


def test_production_mode_refuses_to_adopt_and_says_it_is_demo_only(clean_env, monkeypatch):
    """正式資料唯讀。這裡不是「還沒做」，是刻意只在示範開放。"""
    client = make_client(clean_env, client_stub=_ProposeThenTalk(), DEMO_MODE="1")
    card = _card_of(_propose(client))

    client.app.state.runtime.mode = "production"
    refused = client.post("/api/workbench/tools/adopt", json={"proposal_id": card["proposal_id"]})

    assert refused.status_code == 403
    assert "示範限定" in refused.json()["detail"]

    client.app.state.runtime.mode = "demo"
    assert (
        client.get("/api/workbench/tools", params={"session_id": "whatever"}).json()["read_only"]
        is False
    )


# --- 卡片：程式碼要看得見，採用要按得到 -----------------------------------------


def test_the_card_shows_the_code_and_says_it_is_not_adopted_yet():
    """沙盒擋得住「會不會弄壞東西」，擋不住「算得對不對」。

    算式對不對只有設計師本人看得出來，所以程式碼要攤在卡片上，而且在他按下去
    之前，畫面上必須說這支工具**還沒有**加進來。
    """
    chat = (FRONTEND / "chat.js").read_text("utf-8")

    assert "尚未採用" in chat
    assert "看它寫了什麼" in chat, "程式碼要收在 details 裡，但要看得到"
    assert "採用" in chat and "不要" in chat
    assert "已加入這次對話的工具" in chat
    assert "沒有加入：" in chat, "伺服器拒絕時要照它的話講，不准假裝成功"
    assert "沒有採用，這支工具就到這裡" in chat


def test_the_chat_never_adopts_by_itself_only_the_button_does():
    """採用是**人的動作**。整份 chat.js 只有一個 adoptTool 呼叫，而且住在那顆鍵裡。

    哪天有人為了「順一點」把它搬進 ask() 或工具卡的 handler，這裡會紅。
    """
    chat = (FRONTEND / "chat.js").read_text("utf-8")

    assert re.findall(r"fetch\s*\(", chat) == [], "對外只走 api-client 那一支"
    calls = re.findall(r"AssistantApi\.adoptTool\s*\(", chat)
    assert len(calls) == 1, f"chat.js 裡的採用呼叫應該只有一個，找到 {len(calls)} 個"

    start = chat.index("async function adoptProposal(")
    rest = chat.index("\n  g.AssistantChat", start)
    assert "AssistantApi.adoptTool(" in chat[start:rest], "那個呼叫要住在採用鍵的 handler 裡"


def test_the_browser_only_ever_sends_a_proposal_id_never_code():
    """瀏覽器送得出去的只有一個 id。讓它指定要跑什麼，等於開一條沙盒擋不住的路。"""
    api = (FRONTEND / "api-client.js").read_text("utf-8")

    start = api.index("adoptTool:")
    body = api[start : api.index("},", start)]
    assert "proposal_id" in body
    assert "code" not in body


def test_the_card_has_its_own_styles_so_it_is_not_an_unstyled_block():
    css = (FRONTEND / "app.css").read_text("utf-8")

    assert ".proposal {" in css
    assert ".proposal pre" in css


def test_only_the_source_listing_is_collapsed_never_the_card_itself():
    """程式碼可以收起來，卡片不行。

    `test_the_chat_renders_one_card_per_tool_call_not_just_the_answer` 守的是
    「收起來的東西鏡頭拍不到」；那條規矩在這裡沒有鬆掉，只是換了範圍——
    收起來的只有那一段原始碼，名字、說明、結果表、狀態與兩顆鍵都攤在外面。
    """
    chat = (FRONTEND / "chat.js").read_text("utf-8")
    grown = chat[chat.index("function toolProposal(") :]

    assert '"看它寫了什麼"' in grown, "收合的那顆要講清楚裡面是什麼"

    # 卡片是照這個順序長出來的：名字說明 →（收起來的程式碼）→ 結果 → 兩顆鍵。
    # 收合的那一段前後都有攤開的東西，所以它不可能把整張卡包進去。
    title_at = grown.index('"tool-title"')
    code_at = grown.index("wrap.append(code);")
    result_at = grown.index("proposalResult(p)")
    adopt_at = grown.index('b("採用"')
    assert title_at < code_at < result_at < adopt_at, "只有程式碼收得起來"

    # details 裡面只有兩樣東西：那顆 summary，跟裝原始碼的 <pre>。再多就是把
    # 本來該攤開的東西也收進去了。
    inside = grown[grown.index('code.append(n("summary"') : code_at]
    assert inside.count("code.append(") == 2
    assert "summary" in inside and "code.append(pre);" in inside
