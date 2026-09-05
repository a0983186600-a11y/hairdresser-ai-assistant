"""v10 接線：按鈕要真的改示範狀態，但永遠不能寫正式資料。

突變：移除 server 的 demo-only gate，正式模式的寫入測試會紅；移除
Workbench 的 overlap 檢查，重疊開單會紅；取消改成 no-op，刷新驗證會紅。
"""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(monkeypatch):
    for key in (
        "DEMO_MODE",
        "PRODUCTION_READ_URL",
        "BACKOFFICE_API_BASE",
        "ASSISTANT_DESIGNER_REF",
        "ASSISTANT_AS_OF",
        "REPLAY_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    from assistant.server import create_app

    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


def state(client):
    result = client.get("/api/workbench")
    assert result.status_code == 200, result.text
    return result.json()


def action(client, kind, **data):
    return client.post("/api/workbench/actions", json={"kind": kind, "data": data})


def free_booking(s):
    # 日期取示範資料錨點的未來日，不寫死日曆日期。
    start = datetime.fromisoformat(s["as_of"]) + timedelta(days=1)
    day = next(d for d in s["days"] if d["date"] >= start.date().isoformat() and not d["slots"])
    return {
        "customer_ref": s["customers"][0]["customer_ref"],
        "date": day["date"],
        "time": "13:00",
        "services": ["cut"],
    }


def test_create_reload_reschedule_cancel_never_touches_the_fixture(client):
    s = state(client)
    fixture = client.get("/api/demo/bookings").json()
    request = free_booking(s)
    result = action(client, "book", **request)
    assert result.status_code == 200, result.text
    booking = result.json()["booking"]
    assert booking["duration"] == 60
    assert booking["status"] == "pending"
    assert booking["id"] in [b["id"] for b in state(client)["bookings"]]
    request["time"] = "15:00"
    assert action(client, "update_booking", id=booking["id"], **request).status_code == 200
    assert action(client, "cancel_booking", id=booking["id"]).status_code == 200
    saved = next(b for b in state(client)["bookings"] if b["id"] == booking["id"])
    assert saved["status"] == "cancelled"
    assert action(client, "sync_booking", id=booking["id"]).status_code == 409
    assert client.get("/api/demo/bookings").json() == fixture


def test_conflicts_and_blocked_times_are_rejected_before_saving(client):
    s = state(client)
    request = free_booking(s)
    assert (
        action(client, "block", date=request["date"], start="12:00", end="14:00").status_code == 200
    )
    rejected = action(client, "book", **request)
    assert rejected.status_code == 409
    assert "不接客" in rejected.json()["detail"]
    block = next(b for b in state(client)["blocks"] if b["date"] == request["date"])
    assert action(client, "remove_block", id=block["id"]).status_code == 200
    assert action(client, "book", **request).status_code == 200
    assert action(client, "book", **request).status_code == 409


def test_prices_duration_and_policy_really_feed_new_booking(client):
    s = state(client)
    settings = deepcopy(s["settings"])
    next(x for x in settings["services"] if x["id"] == "cut")["duration"] = 75
    assert action(client, "settings", **settings).status_code == 200
    request = free_booking(s)
    assert action(client, "book", **request).json()["booking"]["duration"] == 75
    settings["duration_mode"] = "fixed"
    settings["fixed_duration"] = 60
    assert action(client, "settings", **settings).status_code == 200
    request["time"] = "16:00"
    assert action(client, "book", **request).json()["booking"]["duration"] == 60


@pytest.mark.parametrize(
    "patch",
    [
        {"services": ["not-a-service"]},
        {"time": "24:60"},
        {"date": "bad-date"},
        {"customer_ref": "not-mine"},
        {"time": "19:45", "services": ["perm"]},
    ],
)
def test_invalid_bookings_fail_without_changing_state(client, patch):
    s = state(client)
    request = {**free_booking(s), **patch}
    assert action(client, "book", **request).status_code in {400, 404, 409, 422}
    assert state(client)["bookings"] == s["bookings"]


def test_browser_sessions_are_isolated_and_only_demo_may_mutate(app, client):
    s = state(client)
    assert action(client, "book", **free_booking(s)).status_code == 200
    other = TestClient(app)
    assert state(other)["bookings"] == s["bookings"]
    runtime = app.state.runtime
    runtime.mode = "production"
    # A second free slot: without the mode guard this would really be accepted,
    # not incidentally rejected by the overlap guard.
    blocked = action(client, "book", **{**free_booking(s), "time": "16:00"})
    assert blocked.status_code == 403
    assert "唯讀" in blocked.json()["detail"]


def test_cross_site_post_and_unknown_actions_cannot_mutate(client):
    s = state(client)
    response = client.post(
        "/api/workbench/actions",
        headers={"Origin": "https://bad.example"},
        json={"kind": "book", "data": free_booking(s)},
    )
    assert response.status_code == 403
    assert action(client, "execute_sql", query="ignored").status_code == 400
    assert state(client)["bookings"] == s["bookings"]


def test_customer_and_conversation_buttons_use_masked_scoped_tools(client):
    s = state(client)
    ref = s["customers"][0]["customer_ref"]
    profile = client.get(f"/api/workbench/customers/{ref}")
    assert profile.status_code == 200
    assert "visits" in profile.json()["result"]
    assert "full_name" not in profile.text
    assert client.get("/api/workbench/customers/not-mine").status_code == 404
    threads = client.get("/api/workbench/conversations").json()["rows"]
    assert threads
    transcript = client.get(f"/api/workbench/conversations/{threads[0]['conversation_ref']}")
    assert transcript.status_code == 200
    assert transcript.json()["result"]["messages"]
    assert "redacted_content" in transcript.text
    assert client.get("/api/workbench/conversations/not-mine").status_code == 404


def test_simulated_takeover_is_saved_but_does_not_claim_a_line_send(client):
    state(client)
    row = client.get("/api/workbench/conversations").json()["rows"][0]
    ref = row["conversation_ref"]
    reply = action(client, "takeover", conversation_ref=ref, enabled=True)
    assert reply.status_code == 200
    assert reply.json()["simulated"] is True
    assert state(client)["takeovers"][ref] is True
    reply = action(client, "message", conversation_ref=ref, text="示範：明天見")
    assert reply.status_code == 200
    assert reply.json()["sent"] is False


def test_calendar_rotation_really_invalidates_the_previous_demo_link(client):
    s = state(client)
    url = s["calendar_url"]
    result = client.get(url)
    assert result.status_code == 200
    assert "BEGIN:VCALENDAR" in result.text
    assert action(client, "rotate_calendar").status_code == 200
    assert state(client)["calendar_url"] != url
    assert client.get(url).status_code == 404


def test_new_frontend_uses_the_design_shell_and_keeps_all_replay_prompts(client):
    home = client.get("/").text
    assert "data-workbench" in home
    assert "data-sheet-root" in home
    assert "data-tour-start" in home
    assert home.count("data-quick-prompt=") == 8
    assert "support.js" not in home


def test_start_interval_setting_is_not_a_dead_switch(client):
    """拿掉 _check_slot 的 step 檢查時會紅：工時以外，起跑網格也要接通。"""
    s = state(client)
    settings = deepcopy(s["settings"])
    settings["step"] = 60
    assert action(client, "settings", **settings).status_code == 200
    request = free_booking(s)
    request["time"] = "13:30"
    assert action(client, "book", **request).status_code == 409


def test_block_can_be_moved_without_duplicating_or_ignoring_bookings(client):
    """把 update_block 改成另建一塊或跳過重疊檢查都會紅。"""
    s = state(client)
    request = free_booking(s)
    assert (
        action(client, "block", date=request["date"], start="12:00", end="14:00").status_code == 200
    )
    block = next(b for b in state(client)["blocks"] if b["date"] == request["date"])
    total = len(state(client)["blocks"])
    assert (
        action(
            client, "update_block", id=block["id"], date=request["date"], start="14:00", end="15:00"
        ).status_code
        == 200
    )
    assert len(state(client)["blocks"]) == total
    assert action(client, "book", **request).status_code == 200
    assert (
        action(
            client, "update_block", id=block["id"], date=request["date"], start="13:00", end="15:00"
        ).status_code
        == 409
    )


def test_rule_and_same_day_settings_are_applied_not_only_saved(client):
    s = state(client)
    request = free_booking(s)
    settings = deepcopy(s["settings"])
    settings["rules"] = [
        {"scope": "daily", "start": "12:00", "end": "17:00", "mode": "only", "services": ["cut"]}
    ]
    settings["same_day"] = False
    assert action(client, "settings", **settings).status_code == 200
    assert action(client, "book", **{**request, "services": ["perm"]}).status_code == 409
    assert action(client, "book", **{**request, "date": s["days"][0]["date"]}).status_code == 409
    assert action(client, "book", **request).status_code == 200


def test_public_package_includes_the_new_logo():
    """刪掉 package-data 的 assets 規則會紅，避免本機有圖但 B 安裝後破圖。"""
    import tomllib
    from fnmatch import fnmatch
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    definition = root / "docs/hackathon-public/pyproject.public.toml"
    if not definition.exists():
        definition = root / "pyproject.toml"
    config = tomllib.loads(definition.read_text())
    patterns = config["tool"]["setuptools"]["package-data"]["assistant"]
    assert any(fnmatch("frontend/assets/gotyou-bubble.png", pattern) for pattern in patterns)


@pytest.mark.parametrize("replay", [False, True])
def test_header_distinguishes_selected_live_model_from_replay(client, app, monkeypatch, replay):
    """刪掉 health 的模型標記或將 Replay 冒充 live 時會紅；不以 API key 當標記。"""
    runtime = app.state.runtime
    monkeypatch.setenv(runtime.config.model.model_env, "MiniMaxAI/MiniMax-M3")
    monkeypatch.setenv(runtime.config.model.api_key_env, "local-test-secret")
    runtime.replay_available = replay
    health = client.get("/health")
    assert health.json()["chat_model"] == ("replay" if replay else "MiniMaxAI/MiniMax-M3")
    assert "local-test-secret" not in health.text


# ── 亂參數守衛：確定性層不准猜，也不准把亂型別炸成 500 ──────────────────

BAD_TYPES = [["x"], {"a": 1}, 123, 12.5, True, None]


@pytest.fixture
def rough_client(app):
    """不讓例外在測試裡直接爆開，才看得到瀏覽器真正收到的 500。"""
    return TestClient(app, raise_server_exceptions=False)


def client_error(result):
    assert 400 <= result.status_code < 500, f"HTTP {result.status_code}｜{result.text[:300]}"
    return result


@pytest.mark.parametrize("bad", BAD_TYPES)
def test_note_customer_ref_of_any_type_is_a_client_error_not_a_500(rough_client, bad):
    """修前：`ref not in {一堆字串}` 拿 list／dict 當集合成員 → TypeError → 500。"""
    client_error(action(rough_client, "note", customer_ref=bad, text="示範備註"))


def test_note_text_must_be_text_not_a_stringified_object(rough_client):
    """修前：str({"a": 1}) 被當成備註內容存下來。"""
    ref = state(rough_client)["customers"][0]["customer_ref"]
    client_error(action(rough_client, "note", customer_ref=ref, text={"a": 1}))
    assert state(rough_client)["notes"].get(ref) in (None, "")


@pytest.mark.parametrize("bad", BAD_TYPES)
def test_block_date_of_any_type_is_a_client_error_not_a_500(rough_client, bad):
    """修前：`_day()` 的 `value not in {一堆日期}` 同樣會被 list／dict 炸成 500。"""
    client_error(action(rough_client, "block", date=bad, start="12:00", end="13:00"))


@pytest.mark.parametrize("kind", ["cancel_booking", "sync_booking", "remove_block"])
@pytest.mark.parametrize("bad", [["x"], {"a": 1}, 123, None])
def test_ids_that_are_not_text_are_rejected_instead_of_silently_missing(rough_client, kind, bad):
    """亂型別的 id 不該只是「找不到」，要明講型別不對。"""
    result = action(rough_client, kind, id=bad)
    assert result.status_code == 400, f"HTTP {result.status_code}｜{result.text[:300]}"


def test_a_dict_name_is_not_masked_into_a_demo_customer(rough_client):
    """修前：str({"a": 1}) 被收成 masked_name「{○○○○○○}」，假客人真的建起來。"""
    before = len(state(rough_client)["customers"])
    result = action(rough_client, "customer", name={"a": 1}, phone_last4="1234")
    assert result.status_code == 400, f"HTTP {result.status_code}｜{result.text[:300]}"
    rows = state(rough_client)["customers"]
    assert len(rows) == before
    assert all("○○○○○○" not in c["masked_name"] for c in rows)


def test_an_integer_phone_last4_is_not_accepted_as_four_digits(rough_client):
    """修前：str(1234) 通過 isdigit()，整數也能建客人。"""
    before = len(state(rough_client)["customers"])
    result = action(rough_client, "customer", name="示範客", phone_last4=1234)
    assert result.status_code == 400, f"HTTP {result.status_code}｜{result.text[:300]}"
    assert len(state(rough_client)["customers"]) == before


def test_a_message_body_must_be_text_not_a_stringified_object(rough_client):
    """修前：str({"a": 1}) 被當成設計師要送的示範訊息存進逐字稿。"""
    ref = rough_client.get("/api/workbench/conversations").json()["rows"][0]["conversation_ref"]
    client_error(action(rough_client, "message", conversation_ref=ref, text={"a": 1}))
    assert not state(rough_client)["messages"].get(ref)


@pytest.mark.parametrize("ref", ["", " ", "x" * 3000])
def test_empty_blank_and_overlong_refs_are_rejected_cleanly(rough_client, ref):
    client_error(action(rough_client, "note", customer_ref=ref, text="嗨"))
    client_error(action(rough_client, "cancel_booking", id=ref))
    client_error(action(rough_client, "remove_block", id=ref))
    client_error(
        action(rough_client, "book", customer_ref=ref, date=ref, time=ref, services=["cut"])
    )
    client_error(action(rough_client, "block", date=ref, start=ref, end=ref))


def test_no_workbench_action_turns_a_wrong_type_into_a_500(rough_client):
    """掃過每一個 kind 分支：亂型別一律 4xx，沒有任何一條會 5xx。"""
    s = state(rough_client)
    conversation = rough_client.get("/api/workbench/conversations").json()["rows"][0][
        "conversation_ref"
    ]
    known = s["customers"][0]["customer_ref"]
    day = s["days"][1]["date"]
    payloads = [
        ("book", {"customer_ref": ["x"], "date": day, "time": "13:00", "services": ["cut"]}),
        ("book", {"customer_ref": known, "date": {"a": 1}, "time": "13:00", "services": ["cut"]}),
        ("book", {"customer_ref": known, "date": day, "time": 1300, "services": ["cut"]}),
        ("book", {"customer_ref": known, "date": day, "time": "13:00", "services": "cut"}),
        ("book", {"customer_ref": known, "date": day, "time": "13:00", "services": [["cut"]]}),
        (
            "update_booking",
            {"id": ["x"], "customer_ref": known, "date": day, "time": "13:00",
             "services": ["cut"]},
        ),
        ("cancel_booking", {"id": ["x"]}),
        ("sync_booking", {"id": {"a": 1}}),
        ("block", {"date": ["x"], "start": "12:00", "end": "13:00"}),
        ("block", {"date": day, "start": ["12:00"], "end": "13:00"}),
        ("block", {"date": day, "start": "12:00", "end": 13}),
        ("block", {"date": day, "start": "12:00"}),
        ("update_block", {"id": {"a": 1}, "date": day, "start": "12:00", "end": "13:00"}),
        ("remove_block", {"id": ["x"]}),
        ("settings", {"services": "cut"}),
        ("settings", {"services": [{"id": 1, "name": "剪", "duration": 30}], "open_through": day}),
        ("settings", {"services": [{"id": "cut", "name": "剪", "duration": 30}],
                      "open_through": ["x"]}),
        ("customer", {"name": {"a": 1}, "phone_last4": "1234"}),
        ("customer", {"name": "示範客", "phone_last4": 1234}),
        ("customer", {}),
        ("takeover", {"conversation_ref": ["x"], "enabled": True}),
        ("takeover", {"conversation_ref": conversation, "enabled": "yes"}),
        ("message", {"conversation_ref": {"a": 1}, "text": "嗨"}),
        ("message", {"conversation_ref": conversation, "text": {"a": 1}}),
        ("note", {"customer_ref": ["x"], "text": "嗨"}),
        ("note", {"customer_ref": known, "text": ["嗨"]}),
        ("execute_sql", {"query": ["drop"]}),
    ]
    broken = []
    for kind, data in payloads:
        result = rough_client.post("/api/workbench/actions", json={"kind": kind, "data": data})
        if result.status_code >= 500:
            broken.append((kind, data, result.status_code, result.text[:120]))
    assert not broken, broken


# ── 沒來過的客人沒有「上次服務」，不准猜剪髮 ────────────────────────────


def test_a_brand_new_demo_customer_has_no_last_service_to_guess_from(client):
    """修前：新客一律寫死 last_service="cut"／「剪髮」，等於替他編了一次到店紀錄。"""
    created = action(client, "customer", name="示範新客", phone_last4="0001")
    assert created.status_code == 200, created.text
    row = created.json()["customer"]
    assert row["last_service"] is None
    assert row["last_service_label"] != "剪髮"
    assert row["visit_count"] == 0
    saved = next(
        c for c in state(client)["customers"] if c["customer_ref"] == row["customer_ref"]
    )
    assert saved["last_service"] is None
    assert saved["last_service_label"] != "剪髮"


def test_a_customer_without_history_can_still_be_booked_when_services_are_stated(client):
    s = state(client)
    created = action(client, "customer", name="示範新客", phone_last4="0002")
    assert created.status_code == 200, created.text
    ref = created.json()["customer"]["customer_ref"]
    request = {**free_booking(s), "customer_ref": ref}
    booked = action(client, "book", **request)
    assert booked.status_code == 200, booked.text
    assert booked.json()["booking"]["service_label"] == "剪髮"
    empty = action(client, "book", **{**request, "time": "16:00", "services": []})
    assert empty.status_code in {400, 422}, empty.text


def test_the_booking_form_does_not_guess_cut_when_there_is_no_last_service():
    """前端同一條規矩：沒有上次服務就不預選，讓設計師自己挑。"""
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "assistant/frontend/workbench.js"
    text = source.read_text(encoding="utf-8")
    assert 'last_service || "cut"' not in text
    assert 'picked = ["cut"]' not in text


# ── 對話清單要看得到內容，排單要帶著身分（Steve 2026-09-05 在示範站上的回饋）──


def test_the_conversation_list_carries_the_last_lines_not_only_a_name(client):
    """一列只有遮罩姓名＋更新時間時，設計師認不出這是誰——要帶最近 1～2 句。"""
    response = client.get("/api/workbench/conversations")
    rows = response.json()["rows"]

    assert rows
    for row in rows:
        assert isinstance(row["preview"], list), row
        assert len(row["preview"]) <= 2, row
        for line in row["preview"]:
            assert line["text"].strip()
            assert len(line["text"]) <= 40, line
            assert line["role"] in {"user", "assistant", "designer"}, line
    spoken = [row for row in rows if row["preview"]]
    assert spoken, "示範對話都有訊息，清單不該一句都帶不出來"
    for row in spoken:
        roles = [line["role"] for line in row["preview"]]
        # 客人講的那句排前面：那才是設計師拿來認人的線索。
        if "user" in roles:
            assert roles[0] == "user", row
    assert "full_name" not in response.text


def test_a_conversation_preview_never_invents_a_line_that_was_not_said():
    """認不出內容就回空清單。列上少一行字沒關係，編一句沒人講過的話不行。"""
    from assistant.workbench import conversation_preview

    assert conversation_preview([]) == []
    assert conversation_preview([{"role": "user", "redacted_content": "   "}]) == []
    assert conversation_preview([{"role": "user", "redacted_content": 12}]) == []
    picked = conversation_preview(
        [
            {"role": "user", "created_at": "2026-09-01T10:00", "redacted_content": "客" * 80},
            {"role": "assistant", "created_at": "2026-09-01T10:05", "redacted_content": "好的"},
        ]
    )
    assert [line["role"] for line in picked] == ["user", "assistant"]
    assert len(picked[0]["text"]) == 40
    assert picked[0]["text"].endswith("…")
    same = conversation_preview(
        [{"role": "user", "created_at": "2026-09-01T10:00", "redacted_content": "只有一句"}]
    )
    assert same == [{"role": "user", "text": "只有一句", "at": "2026-09-01T10:00"}]


def test_a_conversation_row_carries_the_customer_the_booking_form_needs(client):
    """點進對話再按「幫他排一筆」那條路：清單那一列的客編就是 book 收得下的客編。"""
    s = state(client)
    row = client.get("/api/workbench/conversations").json()["rows"][0]

    assert row["customer_ref"] in {c["customer_ref"] for c in s["customers"]}
    request = {**free_booking(s), "customer_ref": row["customer_ref"]}
    booked = action(client, "book", **request)
    assert booked.status_code == 200, booked.text
    assert booked.json()["booking"]["masked_name"] == row["masked_name"]
    assert booked.json()["booking"]["id"] in [b["id"] for b in state(client)["bookings"]]
    # 對不到名單上的客人就排不進去——畫面上那顆按鈕也因此不准自己配一位。
    stranger = action(client, "book", **{**request, "customer_ref": "not-mine"})
    assert stranger.status_code == 404
