"""示範伺服器的 API 面：`assistant/server.py`。

這一支守的是「評審 clone 下來就能跑」的那條路：沒有金鑰、沒有資料庫、
`DEMO_MODE` 預設開著，打開瀏覽器就有東西可以問。

四件被釘住的事：

1. **`/health` 講實話**——徽章（DEMO／PRODUCTION）整支影片都在畫面上，
   它讀的就是這裡；標錯等於影片說謊。
2. **不准偷偷切到 PRODUCTION**。沒有 `PRODUCTION_READ_URL`、或這一份根本沒有
   正式 provider 時，`POST /api/mode` 要當場 400 並說清楚缺什麼，
   不是安靜地退回 demo 卻把徽章寫成 PRODUCTION。
3. **`scope` 由伺服器注入**，不是由前端傳進來的參數。前端送得出去的只有
   一句話與 session_id；設計師看不到別人的客人，這一層就是那道門。
4. **示範資料頁固定不動**。同一個問題問兩次、README 截圖、影片畫面要對得起來，
   所以 `/api/demo/*` 是存好的 fixture，不是每次現算。

`run_chat` 的本體由第二階段另一個人寫，這裡一律 monkeypatch——
這一支測的是伺服器怎麼呼叫它，不是它算得對不對。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assistant.adapters.schemas import TAIPEI
from assistant.agent.types import ChatResult, ToolCallRecord
from assistant.demo_data.generate import ANCHOR

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 伺服器讀的每一個環境變數。每個測試都先清乾淨，
#: 否則 Steve 本機 `.env` 或前一個測試留下來的值會讓結果隨機。
SERVER_ENV_NAMES = (
    "DEMO_MODE",
    "PRODUCTION_READ_URL",
    "REPLAY_MODE",
    "ASSISTANT_DESIGNER_REF",
    "BACKOFFICE_API_BASE",
    "ASSISTANT_AS_OF",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in SERVER_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def make_client(clean_env, **env: str) -> TestClient:
    """每個測試自己一個 app：模式是 app 上的狀態，共用會互相污染。"""
    from assistant import server

    for name, value in env.items():
        clean_env.setenv(name, value)
    return TestClient(server.create_app())


def _record_run_chat(monkeypatch, *, result: ChatResult | None = None) -> list[dict]:
    """把 `run_chat` 換成一個只記帳的假貨，回傳收到的呼叫參數。"""
    seen: list[dict] = []
    answer = result if result is not None else ChatResult(reply="好的。")

    def fake_run_chat(message, **kwargs):
        seen.append({"message": message, **kwargs})
        return answer

    monkeypatch.setattr("assistant.agent.run_chat", fake_run_chat)
    return seen


# --- /health 與模式徽章 --------------------------------------------------------


def test_health_tells_the_badge_which_mode_it_is_really_in(clean_env):
    client = make_client(clean_env)
    payload = client.get("/health").json()

    assert payload["status"] == "ok"
    assert payload["mode"] == "demo"
    assert payload["replay_available"] is False
    assert payload["provider"] == "MockSalonDataProvider"


def test_mode_endpoint_reports_that_production_is_not_reachable_from_here(clean_env):
    client = make_client(clean_env)
    payload = client.get("/api/mode").json()

    assert payload["mode"] == "demo"
    assert payload["production_available"] is False
    assert payload["production_note"]


def test_switching_to_production_without_a_read_url_is_refused_with_a_reason(clean_env):
    client = make_client(clean_env)
    response = client.post("/api/mode", json={"mode": "production"})

    assert response.status_code == 400
    assert "PRODUCTION_READ_URL" in response.json()["detail"]
    # 被拒絕之後徽章不准變。
    assert client.get("/health").json()["mode"] == "demo"


def test_switching_back_to_demo_always_works(clean_env):
    client = make_client(clean_env)
    response = client.post("/api/mode", json={"mode": "demo"})

    assert response.status_code == 200
    assert response.json()["mode"] == "demo"


def test_an_unknown_mode_is_refused(clean_env):
    client = make_client(clean_env)
    assert client.post("/api/mode", json={"mode": "staging"}).status_code == 400


# --- /api/chat -----------------------------------------------------------------


def test_chat_injects_the_scope_the_front_end_cannot_choose(clean_env, monkeypatch):
    seen = _record_run_chat(monkeypatch)
    client = make_client(clean_env)

    response = client.post("/api/chat", json={"message": "誰快流失？"})

    assert response.status_code == 200
    assert len(seen) == 1
    call = seen[0]
    assert call["message"] == "誰快流失？"
    designers = json.loads(
        (REPO_ROOT / "assistant" / "demo_data" / "designers.json").read_text("utf-8")
    )
    assert call["scope"].designer_ref == designers[0]["designer_ref"]


def test_chat_lets_the_operator_pick_which_designer_is_logged_in(clean_env, monkeypatch):
    seen = _record_run_chat(monkeypatch)
    designers = json.loads(
        (REPO_ROOT / "assistant" / "demo_data" / "designers.json").read_text("utf-8")
    )
    client = make_client(clean_env, ASSISTANT_DESIGNER_REF=designers[2]["designer_ref"])

    client.post("/api/chat", json={"message": "嗨"})

    assert seen[0]["scope"].designer_ref == designers[2]["designer_ref"]


def test_demo_mode_hands_the_agent_the_mock_provider_and_the_data_anchor(
    clean_env, monkeypatch
):
    seen = _record_run_chat(monkeypatch)
    client = make_client(clean_env)

    client.post("/api/chat", json={"message": "嗨"})

    call = seen[0]
    assert type(call["provider"]).__name__ == "MockSalonDataProvider"
    # 錨點：資料是固定的，「現在」也必須固定，答案才跟 README／影片對得上。
    assert call["as_of"] == ANCHOR
    assert call["as_of"].tzinfo is not None


def test_an_explicit_as_of_override_wins(clean_env, monkeypatch):
    """錄影與測試要指定「現在」時的後門。時刻從錨點算出來，不寫死——
    寫死的日期過了那天就變成一顆會自己引爆的紅燈（CLAUDE.md）。"""
    seen = _record_run_chat(monkeypatch)
    moment = ANCHOR - timedelta(days=120)
    client = make_client(clean_env, ASSISTANT_AS_OF=moment.isoformat())

    client.post("/api/chat", json={"message": "嗨"})

    assert seen[0]["as_of"] == moment


def test_chat_returns_the_tool_calls_so_the_ui_can_show_its_working(
    clean_env, monkeypatch
):
    result = ChatResult(
        reply="前十位如下。",
        tool_calls=[
            ToolCallRecord(
                name="rank_customers_by_spend",
                arguments={"days": 365, "limit": 10},
                result_summary="10 位",
                duration_ms=12,
            )
        ],
    )
    _record_run_chat(monkeypatch, result=result)
    client = make_client(clean_env)

    payload = client.post("/api/chat", json={"message": "今年消費前十"}).json()

    assert payload["reply"] == "前十位如下。"
    assert payload["tool_calls"] == [
        {
            "name": "rank_customers_by_spend",
            "arguments": {"days": 365, "limit": 10},
            "result_summary": "10 位",
            "duration_ms": 12,
            # 查詢工具沒有待確認的動作；只有提案工具會把那張單子帶到瀏覽器。
            "proposal": None,
        }
    ]
    assert payload["session_id"]


def test_the_second_turn_carries_the_first_turn_back_in(clean_env, monkeypatch):
    seen = _record_run_chat(
        monkeypatch,
        result=ChatResult(
            reply="好。",
            transcript=[
                {"role": "user", "content": "嗨"},
                {"role": "assistant", "content": "好。"},
            ],
        ),
    )
    client = make_client(clean_env)

    first = client.post("/api/chat", json={"message": "嗨"}).json()
    client.post("/api/chat", json={"message": "那她呢？", "session_id": first["session_id"]})

    assert seen[0]["session"] is None
    carried = seen[1]["session"]
    assert carried is not None
    assert carried.session_id == first["session_id"]
    assert carried.history == [
        {"role": "user", "content": "嗨"},
        {"role": "assistant", "content": "好。"},
    ]


def test_an_unknown_session_id_starts_a_new_one_instead_of_crashing(clean_env, monkeypatch):
    _record_run_chat(monkeypatch)
    client = make_client(clean_env)

    response = client.post(
        "/api/chat", json={"message": "嗨", "session_id": "not-a-session"}
    )

    assert response.status_code == 200


def test_an_empty_message_is_refused(clean_env, monkeypatch):
    _record_run_chat(monkeypatch)
    client = make_client(clean_env)

    assert client.post("/api/chat", json={"message": "   "}).status_code == 422


def test_the_agent_not_being_wired_yet_is_a_readable_503_not_a_traceback(
    clean_env, monkeypatch
):
    def not_yet(*_args, **_kwargs):
        raise NotImplementedError("phase 2 implements the loop")

    monkeypatch.setattr("assistant.agent.run_chat", not_yet)
    client = make_client(clean_env)

    response = client.post("/api/chat", json={"message": "嗨"})

    assert response.status_code == 503
    assert "agent" in response.json()["detail"]


# --- REPLAY_MODE ---------------------------------------------------------------


def test_replay_mode_without_the_replay_module_still_boots(clean_env, monkeypatch):
    """B 版若少了 `assistant/agent/replay.py`（或它 import 失敗），伺服器不准掛掉。

    第二階段合併前這個檔真的不存在；合併後要用攔截 import 來模擬「缺席」，
    否則這條測試會因為模組已經在而失去意義。
    """
    import importlib

    real_import_module = importlib.import_module

    def without_replay(name, *args, **kwargs):
        if name == "assistant.agent.replay":
            raise ModuleNotFoundError(name)
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr("assistant.server.importlib.import_module", without_replay)
    seen = _record_run_chat(monkeypatch)
    client = make_client(clean_env, REPLAY_MODE="1")

    health = client.get("/health").json()
    assert health["replay_available"] is False
    assert health["replay_note"]

    client.post("/api/chat", json={"message": "嗨"})
    # 沒有 replay 模組時塞一個回固定句子的假 client，而不是 None——
    # 傳 None 會讓 agent 去建真的 client，那就需要金鑰了。
    stand_in = seen[0]["client"]
    assert stand_in is not None
    message = stand_in.complete([], [], model="none")
    assert message["role"] == "assistant"
    assert isinstance(message["content"], str) and message["content"]


def test_without_replay_mode_the_agent_builds_its_own_client(clean_env, monkeypatch):
    seen = _record_run_chat(monkeypatch)
    client = make_client(clean_env)

    client.post("/api/chat", json={"message": "嗨"})

    assert seen[0]["client"] is None


# --- /api/demo/* ---------------------------------------------------------------


@pytest.mark.parametrize("page", ["bookings", "schedule", "customers", "settings"])
def test_the_four_data_pages_are_fixed_fixtures(clean_env, page: str):
    client = make_client(clean_env)

    first = client.get(f"/api/demo/{page}")
    second = client.get(f"/api/demo/{page}")

    assert first.status_code == 200
    assert first.json() == second.json(), "示範資料每次都要一樣，不然截圖與影片對不上"
    assert first.json()


def test_an_unknown_demo_page_is_a_404(clean_env):
    client = make_client(clean_env)
    assert client.get("/api/demo/payroll").status_code == 404


def test_a_failed_forward_is_a_502_that_says_which_way_it_failed(clean_env, monkeypatch):
    import httpx

    class _Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, url, **_kwargs):
            raise httpx.ConnectError("nope")

    monkeypatch.setattr("httpx.Client", _Client)
    _production_is_reachable(monkeypatch)
    client = make_client(
        clean_env,
        DEMO_MODE="0",
        PRODUCTION_READ_URL=FAKE_READ_URL,
        BACKOFFICE_API_BASE="http://backoffice.invalid",
    )

    response = client.get("/api/demo/bookings")

    assert response.status_code == 502
    assert "backoffice.invalid" in response.json()["detail"]


# --- 匯出前的獨立性 -------------------------------------------------------------


def test_importing_the_server_never_pulls_the_private_system_in():
    """伺服器是最容易手滑 `from app...` 的一個檔，所以真的載入一次看看。"""
    probe = (
        "import sys, json\n"
        "import assistant.server\n"
        "leaked = sorted(m for m in sys.modules "
        "if m.split('.')[0] in {'app', 'scripts', 'alembic'})\n"
        "print(json.dumps(leaked))\n"
    )
    finished = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    assert json.loads(finished.stdout.strip().splitlines()[-1]) == []


def test_replay_mode_with_the_replay_module_present_reports_available(clean_env, monkeypatch):
    """合併後的正常狀態：Replay 模組在，/health 要老實說 replay_available=True。"""
    _record_run_chat(monkeypatch)
    client = make_client(clean_env, REPLAY_MODE="1")

    health = client.get("/health").json()
    assert health["replay_available"] is True


# --- 徽章與四個資料頁：兩件事，各講各的 ----------------------------------------
#
# 審查員抓到的洞：`demo_page()` 只看有沒有設 `BACKOFFICE_API_BASE`，不看 mode。
# 於是兩個方向都會說謊——
#
# - 設了 `BACKOFFICE_API_BASE` 卻在 DEMO 模式：四頁轉發**正式後台**（全名、
#   完整電話就這樣出現在錄影裡），徽章還寫著 DEMO。
# - 切到 PRODUCTION 卻沒設 `BACKOFFICE_API_BASE`：四頁還是固定 seed 假資料，
#   徽章卻寫 PRODUCTION，看的人以為畫面上那幾筆是真的。
#
# 現在的規矩：**mode 決定資料頁**。demo 一律 fixture；production 有後台位址才
# 轉發，沒有就照樣 fixture，但回應與 /health 都要把 `data_source` 講出來。


class _StubProductionProvider:
    """形狀對就好的替身。測試不准連正式庫——那是正在服務真實客人的資料。"""


#: `PRODUCTION_READ_URL` 只要**非空**就會讓伺服器試著切到正式模式，而
#: `_load_production_provider` 在這些測試裡已經被換掉了（`_production_is_reachable`），
#: 所以這個值從頭到尾不會被拿去連線——它是一個旗標，不是連線字串。
#:
#: 這裡**故意不寫成連線字串的樣子**：公開版的洩密掃描
#:（`scripts/hackathon_leak_scan.py`）會把任何 DSN 形狀的字面量當成外洩，
#: 而它擋得對——寫一個「明顯是假的」DSN 只會逼掃描器學會放行真的那種形狀。
#: 想確認掃描器抓的是什麼，去讀它的 pattern，不要在這裡照抄一個給它看。
FAKE_READ_URL = "stub-production-read-url"


def _production_is_reachable(monkeypatch) -> None:
    """讓伺服器以為這一份帶得動正式 provider，但不建立任何連線。"""

    def _fake(read_url: str):
        if not read_url:
            return None, "沒有設定 PRODUCTION_READ_URL，這一份只跑得動示範資料"
        return _StubProductionProvider(), None

    monkeypatch.setattr("assistant.server._load_production_provider", _fake)


def _forwarding_is_a_failure(monkeypatch) -> list[str]:
    """把 httpx 換成「一被呼叫就記一筆」，用來證明真的沒有往外送。"""
    forwarded: list[str] = []

    class _Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, url, **_kwargs):
            forwarded.append(url)
            raise AssertionError(f"示範模式不該轉發到 {url}")

    monkeypatch.setattr("httpx.Client", _Client)
    return forwarded


def _fixture_on_disk(name: str) -> dict:
    path = REPO_ROOT / "assistant" / "frontend" / "fixtures" / f"{name}.json"
    return json.loads(path.read_text("utf-8"))


def test_demo_mode_never_forwards_even_with_a_backoffice_base(clean_env, monkeypatch):
    """徽章寫 DEMO 就不准去讀正式後台——那一頁有全名與完整電話。"""
    forwarded = _forwarding_is_a_failure(monkeypatch)
    client = make_client(clean_env, BACKOFFICE_API_BASE="http://backoffice.invalid")

    payload = client.get("/api/demo/bookings").json()

    assert forwarded == [], "DEMO 模式竟然往正式後台送了請求"
    assert payload == _fixture_on_disk("bookings")
    assert client.get("/health").json()["data_source"] == "demo_fixtures"


def test_production_mode_forwards_the_same_path_to_the_backoffice(clean_env, monkeypatch):
    """切到 PRODUCTION 又設了後台位址，才是「四頁讀正式資料」的那個組合。"""
    forwarded: list[str] = []

    class _Response:
        status_code = 200

        def json(self):
            return {"rows": [{"customer": "轉發來的"}]}

    class _Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, url, **_kwargs):
            forwarded.append(url)
            return _Response()

    monkeypatch.setattr("httpx.Client", _Client)
    _production_is_reachable(monkeypatch)
    client = make_client(
        clean_env,
        DEMO_MODE="0",
        PRODUCTION_READ_URL=FAKE_READ_URL,
        BACKOFFICE_API_BASE="http://backoffice.invalid",
    )

    payload = client.get("/api/demo/bookings").json()

    assert forwarded == ["http://backoffice.invalid/api/demo/bookings"]
    assert payload == {"rows": [{"customer": "轉發來的"}]}
    health = client.get("/health").json()
    assert health["mode"] == "production"
    assert health["data_source"] == "backoffice_forward"


def test_production_mode_without_a_backoffice_base_admits_the_pages_are_demo(
    clean_env, monkeypatch
):
    """徽章寫 PRODUCTION、四頁卻還是假資料時，回應本身要說出來。

    這一格最會騙人：助理讀的**是**正式唯讀資料（徽章沒說謊），
    但四個資料頁沒有後台位址可轉發，畫面上那幾筆全是產生出來的。
    """
    forwarded = _forwarding_is_a_failure(monkeypatch)
    _production_is_reachable(monkeypatch)
    client = make_client(clean_env, DEMO_MODE="0", PRODUCTION_READ_URL=FAKE_READ_URL)

    payload = client.get("/api/demo/bookings").json()
    health = client.get("/health").json()

    assert forwarded == []
    assert health["mode"] == "production"
    assert health["data_source"] == "demo_fixtures"
    assert health["data_source_label"] == "資料頁：示範"
    assert payload["data_source"] == "demo_fixtures"
    assert "BACKOFFICE_API_BASE" in payload["data_source_note"]
    # fixture 的內容一個字都沒被動到，只是多掛了來源標記。
    assert payload["rows"] == _fixture_on_disk("bookings")["rows"]


def test_the_mode_endpoint_carries_the_same_data_source_as_health(clean_env, monkeypatch):
    """徽章與資料頁標籤只能有一份真相：/api/mode 與 /health 講的必須一樣。"""
    _production_is_reachable(monkeypatch)
    client = make_client(clean_env, DEMO_MODE="0", PRODUCTION_READ_URL=FAKE_READ_URL)

    mode = client.get("/api/mode").json()
    health = client.get("/health").json()

    assert mode["data_source"] == health["data_source"] == "demo_fixtures"
    assert mode["data_source_label"] == health["data_source_label"]

    switched = client.post("/api/mode", json={"mode": "demo"}).json()
    assert switched["data_source"] == "demo_fixtures"
    assert client.get("/health").json()["mode"] == "demo"


# --- ASSISTANT_AS_OF 的兩個坑 --------------------------------------------------


@pytest.fixture
def utc_host():
    """把主機時區搬到 UTC。

    正式主機是 UTC（CLAUDE.md 那張排程表整張都在講這件事），而「不帶時區的
    `ASSISTANT_AS_OF` 被當成主機本地時間」這個坑只在那裡張開——
    在 Steve 的 Mac 上（本地就是台北）它是隱形的。
    """
    original = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


def test_an_as_of_without_a_timezone_is_read_as_taipei_time(clean_env, monkeypatch, utc_host):
    """沒帶時區的值一律當台北時間，不是主機的本地時間。

    交給 `astimezone()` 去猜，在 UTC 主機上會把 09:00 讀成台北 17:00——
    整份「今天」偏八個小時，而畫面上完全看不出來。
    """
    seen = _record_run_chat(monkeypatch)
    naive = (ANCHOR + timedelta(days=1)).replace(tzinfo=None)
    client = make_client(clean_env, ASSISTANT_AS_OF=naive.isoformat())

    client.post("/api/chat", json={"message": "嗨"})

    assert seen[0]["as_of"] == naive.replace(tzinfo=TAIPEI)
    assert seen[0]["as_of"].utcoffset() == timedelta(hours=8)
    assert "台北" in (client.get("/health").json()["as_of_note"] or "")


def test_an_as_of_we_cannot_read_does_not_take_the_server_down(clean_env, monkeypatch):
    """看不懂的值退回預設並講一句人話，不是在 import 時噴一整串 traceback。

    `create_app()` 在 `import assistant.server` 就跑了。格式錯一個字元就起不來，
    而錄影前五分鐘沒有人讀得完那串堆疊。
    """
    seen = _record_run_chat(monkeypatch)
    client = make_client(clean_env, ASSISTANT_AS_OF="下禮拜三下午")

    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert "ASSISTANT_AS_OF" in health["as_of_note"]

    client.post("/api/chat", json={"message": "嗨"})
    assert seen[0]["as_of"] == ANCHOR, "看不懂就退回示範錨點"


# --- 聊天 → 確認卡 → 才寫入 -----------------------------------------------------
#
# 聊天那頭永遠不寫。這三支把那條界線釘在伺服器層：
# 一次 /api/chat 之後工作台一個字都不能變，寫入只發生在後來那一次
# POST /api/workbench/actions——而那個端點原本的守門員（同源、正式唯讀 403）
# 照樣管得到這條新路，不必再守第二次。


class _ScriptedClient:
    """照劇本回話的假模型：先叫一次工具，再講一句話。"""

    is_replay = False

    def __init__(self, tool_name: str, arguments: dict) -> None:
        self._rounds = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_test",
                        "type": "function",
                        "index": 0,
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "我整理成這樣，請按確認。", "tool_calls": None},
        ]
        self.calls = 0

    def complete(self, messages, tools, *, model):
        step = self._rounds[min(self.calls, len(self._rounds) - 1)]
        self.calls += 1
        return step


def _scripted(monkeypatch, tool_name: str, arguments: dict) -> None:
    monkeypatch.setattr(
        "assistant.agent.http_client.build_client_from_env",
        lambda config: _ScriptedClient(tool_name, arguments),
    )


def _booking_proposal(client, monkeypatch) -> dict:
    customer = client.get("/api/workbench").json()["customers"][0]
    _scripted(
        monkeypatch,
        "propose_booking",
        {"customer": customer["phone_last4"], "start": "明天 15:00", "service": "剪髮"},
    )
    payload = client.post("/api/chat", json={"message": "幫我排一筆"}).json()
    return payload["tool_calls"][0]["proposal"]


def test_a_proposal_comes_back_with_the_answer_and_writes_nothing(clean_env, monkeypatch):
    client = make_client(clean_env)
    before = client.get("/api/workbench").json()

    proposal = _booking_proposal(client, monkeypatch)

    assert proposal["kind"] == "book"
    assert proposal["missing"] == []
    assert proposal["action"]["kind"] == "book"
    assert proposal["action"]["data"]["time"] == "15:00"
    after = client.get("/api/workbench").json()
    assert after["bookings"] == before["bookings"]
    assert after["settings"] == before["settings"]


def test_the_proposal_only_becomes_a_booking_after_the_confirm_button(
    clean_env, monkeypatch
):
    client = make_client(clean_env)
    proposal = _booking_proposal(client, monkeypatch)
    action = proposal["action"]

    response = client.post(
        "/api/workbench/actions", json={"kind": action["kind"], "data": action["data"]}
    )

    assert response.status_code == 200, response.text
    assert response.json()["booking"]["time"] == "15:00"
    booked = client.get("/api/workbench").json()["bookings"]
    assert any(row["id"] == response.json()["booking"]["id"] for row in booked)


def test_the_confirm_button_still_hits_the_read_only_wall_in_production(
    clean_env, monkeypatch
):
    from assistant import server

    clean_env.setenv("DEMO_MODE", "1")
    app = server.create_app()
    client = TestClient(app)
    proposal = _booking_proposal(client, monkeypatch)
    action = proposal["action"]

    app.state.runtime.mode = "production"
    response = client.post(
        "/api/workbench/actions", json={"kind": action["kind"], "data": action["data"]}
    )

    assert response.status_code == 403
    assert "唯讀" in response.json()["detail"]


def test_a_chat_tool_call_that_is_not_a_proposal_carries_no_action(clean_env, monkeypatch):
    """只有提案工具會帶 proposal；查詢工具的完整結果一樣不准離開伺服器。"""
    client = make_client(clean_env)
    _scripted(monkeypatch, "get_retention_watchlist", {"limit": 1})

    payload = client.post("/api/chat", json={"message": "誰快流失"}).json()

    assert payload["tool_calls"][0]["name"] == "get_retention_watchlist"
    assert payload["tool_calls"][0]["proposal"] is None
