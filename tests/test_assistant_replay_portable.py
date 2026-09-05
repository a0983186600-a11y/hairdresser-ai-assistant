"""零金鑰模式不依賴啟動目錄；health 必須反映真的載到錄音。"""

import re

import pytest
from fastapi.testclient import TestClient

from assistant import server
from assistant.agent.replay import NO_RECORDING_REPLY
from assistant.config.loader import load_config


@pytest.fixture(autouse=True)
def replay_environment(monkeypatch):
    for name in (
        "PRODUCTION_READ_URL", "ASSISTANT_DESIGNER_REF", "BACKOFFICE_API_BASE",
        "ASSISTANT_AS_OF", "ASSISTANT_CONFIG_PATH", "QWEN_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("REPLAY_MODE", "1")


def test_every_recording_works_when_the_process_starts_outside_the_repo(monkeypatch, tmp_path):
    """把 ReplayClient 解析改回 Path(directory) 會紅；真的 API／工具，不替換 run_chat。"""
    monkeypatch.chdir(tmp_path)
    client = TestClient(server.create_app())
    prompts = re.findall(r'data-quick-prompt="([^"]+)"', client.get("/").text)
    assert len(prompts) == 7
    replies = [client.post("/api/chat", json={"message": prompt}).json() for prompt in prompts]
    assert all(reply["reply"] != NO_RECORDING_REPLY and reply["tool_calls"] for reply in replies)
    assert client.get("/health").json()["replay_available"] is True


@pytest.mark.parametrize("exists", [False, True], ids=["missing", "empty"])
def test_zero_recordings_are_not_advertised_as_available(monkeypatch, tmp_path, exists):
    """把 server 的 recording_count 判斷拿掉會紅；自訂空路徑不能偷退回出貨錄音。"""
    directory = tmp_path / "recordings"
    if exists:
        directory.mkdir()
    cfg = load_config()
    cfg = cfg.model_copy(update={
        "agent": cfg.agent.model_copy(update={"replay_dir": str(directory)})
    })
    monkeypatch.setattr(server, "load_config", lambda: cfg)
    client = TestClient(server.create_app())
    health = client.get("/health").json()
    assert health["replay_available"] is False
    assert "0" in health["replay_note"]
    answer = client.post("/api/chat", json={"message": "誰快流失了？"}).json()
    assert answer["reply"] == NO_RECORDING_REPLY
    assert answer["tool_calls"] == []
