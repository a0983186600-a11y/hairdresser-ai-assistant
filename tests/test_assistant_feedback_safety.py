"""手機回饋回歸：模型失敗可分辨，等待不因逾時重試加倍，12 點才開店。

突變：刪掉 timeout/4xx 免重試分支會多叫一次；刪掉 API 錯誤映射會 500；
open_time 改回 11:00，預設與真正開單邊界測試都會紅。
"""

from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant.agent.http_client import HttpChatClient
from assistant.server import create_app


@pytest.fixture
def client(monkeypatch):
    for key in (
        "PRODUCTION_READ_URL",
        "ASSISTANT_AS_OF",
        "ASSISTANT_DESIGNER_REF",
        "BACKOFFICE_API_BASE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DEMO_MODE", "1")
    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


class FailingModel(HttpChatClient):
    def __init__(self, error):
        self.error, self.count = error, 0

    def post(self, *args, **kwargs):
        self.count += 1
        raise self.error


def failure(status):
    if status == "timeout":
        return httpx.ReadTimeout("private-token-and-customer")
    return httpx.HTTPStatusError(
        "private-token-and-customer",
        request=httpx.Request("POST", "https://example.invalid/private-token"),
        response=httpx.Response(status),
    )


@pytest.mark.parametrize(
    "upstream,code,attempts",
    [
        ("timeout", "model_timeout", 1),
        (401, "model_auth", 1),
        (429, "model_busy", 1),
        (503, "model_unavailable", 2),
    ],
)
def test_real_chat_failure_is_safe_and_retry_is_bounded(
    client, monkeypatch, upstream, code, attempts
):
    model = FailingModel(failure(upstream))
    monkeypatch.setattr(httpx, "post", model.post)
    client.app.state.runtime.client = HttpChatClient("https://example.invalid/v1", "test-only")
    before = client.get("/api/workbench").json()["bookings"]
    result = client.post("/api/chat", json={"message": "請幫我查一下"})
    assert result.status_code == (504 if upstream == "timeout" else 503)
    assert result.json()["detail"]["code"] == code
    assert "private-token" not in result.text
    assert model.count == attempts
    assert client.app.state.runtime.sessions == {}
    assert client.get("/api/workbench").json()["bookings"] == before


def test_opening_hour_is_twelve_and_configurable_at_the_booking_gate(client):
    state = client.get("/api/workbench").json()
    settings = state["settings"]
    assert settings["open_time"] == "12:00"
    future = datetime.fromisoformat(state["as_of"]) + timedelta(days=1)
    day = next(
        d["date"]
        for d in state["days"]
        if not d["slots"] and d["date"] >= future.date().isoformat()
    )
    request = dict(
        customer_ref=state["customers"][0]["customer_ref"], date=day, time="11:00", services=["cut"]
    )

    def post(kind, data):
        return client.post("/api/workbench/actions", json={"kind": kind, "data": data})

    assert post("book", request).status_code == 409
    assert post("book", {**request, "time": "12:00"}).status_code == 200
    settings["open_time"] = "10:00"
    assert post("settings", settings).status_code == 200
    assert post("book", {**request, "time": "10:00"}).status_code == 200
