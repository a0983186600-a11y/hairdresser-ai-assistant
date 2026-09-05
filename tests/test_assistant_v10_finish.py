"""V10 completion: break the 60-day projection / examples / session isolation to turn red.

Dates are relative to the demo clock. Source fixtures and the model answer key
must stay unchanged: only the session workbench receives presentation examples.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from assistant.demo_data.generate import load_dataset


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("REPLAY_MODE", "1")
    from assistant.server import create_app

    with TestClient(create_app()) as c:
        yield c


def test_calendar_reaches_sixty_days_and_month_end_without_mutating_fixture(client):
    before = client.get("/api/demo/schedule").json()
    s = client.get("/api/workbench").json()
    anchor = datetime.fromisoformat(s["as_of"]).date()
    assert [d["date"] for d in s["days"]] == [
        (anchor + timedelta(days=i)).isoformat() for i in range(60)
    ]
    assert s["days"][-1]["date"] >= s["settings"]["open_through"]
    assert client.get("/api/demo/schedule").json() == before


def test_extended_day_can_really_be_booked_and_cancelled(client):
    s = client.get("/api/workbench").json()
    day = (datetime.fromisoformat(s["as_of"]) + timedelta(days=28)).date().isoformat()
    payload = dict(customer_ref=s["customers"][0]["customer_ref"], date=day,
                   time="13:00", services=["cut"])
    response = client.post("/api/workbench/actions", json={"kind": "book", "data": payload})
    assert response.status_code == 200, response.text
    booking = response.json()["booking"]
    fresh = client.get("/api/workbench").json()
    assert any(x["id"] == booking["id"] for d in fresh["days"] for x in d["slots"])
    assert client.post("/api/workbench/actions", json={
        "kind": "cancel_booking", "data": {"id": booking["id"]},
    }).status_code == 200
    assert not any(x["id"] == booking["id"] for d in client.get("/api/workbench").json()["days"]
                   for x in d["slots"])


def test_prices_have_finished_examples_and_one_honest_unset_service(client):
    from assistant.workbench import service_catalog

    s = client.get("/api/workbench").json()
    assert s["presentation_examples"]["simulated"] is True
    services = s["settings"]["services"]
    incomplete = [x["id"] for x in services if (
        any(x[k] is None for k in ("short", "medium", "long"))
        if x["price_mode"] == "length" else x["price"] is None
    )]
    assert incomplete == ["cut"]
    # Changing workbench examples must not change model tool catalog/answer key.
    assert all(s.price is None and s.short is None for s in service_catalog())


def test_profile_examples_are_separate_from_identity_and_real_notes(client):
    s = client.get("/api/workbench").json()
    profiles = s["presentation_examples"]["profiles"]
    assert profiles
    known = {c["customer_ref"] for c in s["customers"]}
    assert set(profiles) <= known
    for p in profiles.values():
        assert p["simulated"] is True
        assert p["package"]["remaining"] > 0
        assert p["cycle_days"] > 0
        assert p["pinned"]
    assert s["notes"] == {}, "Do not silently turn made-up examples into customer notes"
    # Identity fields never ride along with the made-up presentation examples.
    for p in profiles.values():
        assert not {"pos_customer_id", "masked_name", "phone_last4"} & set(p)


def test_pos_binding_comes_from_the_dataset_and_is_never_invented(client):
    """畫面要分得出「POS 已綁定」與「未綁 POS」，但那個答案只能來自資料集。

    這條原本寫成「`customers` 裡不准出現 `pos_customer_id`」——那時候資料集裡
    根本沒有這個欄位，任何值都只可能是為了畫面好看編出來的。現在欄位有了
    （`assistant/demo_data/generate.py` 的固定 seed 產的），守的東西不變：
    **值只能照抄資料集**，資料集沒有就是 `None`，工作台不准自己算一個。
    """
    dataset = load_dataset()
    s = client.get("/api/workbench").json()
    truth = {c["customer_ref"]: c.get("pos_customer_id") for c in dataset["customers"]}

    for row in s["customers"]:
        assert "pos_customer_id" in row
        assert row["pos_customer_id"] == truth[row["customer_ref"]]
    # 兩種狀態都要真的存在，否則畫面上那顆陶土色標籤只是裝飾。
    values = [c["pos_customer_id"] for c in s["customers"]]
    assert any(v for v in values) and any(v is None for v in values)

    # 這一刻在示範裡新建的客人，公司系統裡還沒有他的檔：不准先給他一個號碼。
    created = client.post("/api/workbench/actions", json={"kind": "customer", "data": {
        "name": "示範新客", "phone_last4": "0009",
    }})
    assert created.status_code == 200
    assert created.json()["customer"]["pos_customer_id"] is None


def test_new_booking_never_leaks_to_model_fixture_or_another_session(client):
    baseline = client.get("/api/demo/bookings").json()
    s = client.get("/api/workbench").json()
    day = (datetime.fromisoformat(s["as_of"]) + timedelta(days=28)).date().isoformat()
    response = client.post("/api/workbench/actions", json={"kind": "book", "data": {
        "customer_ref": s["customers"][0]["customer_ref"], "date": day,
        "time": "13:00", "services": ["cut"],
    }})
    assert response.status_code == 200
    identity = response.json()["booking"]["id"]
    with TestClient(client.app) as other:
        assert identity not in [b["id"] for b in other.get("/api/workbench").json()["bookings"]]
    assert client.get("/api/demo/bookings").json() == baseline
