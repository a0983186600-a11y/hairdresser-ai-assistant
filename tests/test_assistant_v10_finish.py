"""V10 completion: break the 60-day projection / examples / session isolation to turn red.

Dates are relative to the demo clock. Source fixtures and the model answer key
must stay unchanged: only the session workbench receives presentation examples.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient


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
    # Customer identity and POS status cannot be invented to make a demo pretty.
    assert all("pos_customer_id" not in c for c in s["customers"])


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
