"""Exercise the installed wheel from an empty directory, without keys or network.

Run with an isolated interpreter: /path/to/venv/bin/python -I /path/to/this/file.
Do not use the editable development installation for this release check.
"""

import os
import re
import socket
import sys
import tempfile
from pathlib import Path


def main():
    for key in list(os.environ):
        if key.startswith(("QWEN_", "ASSISTANT_", "BACKOFFICE_", "PRODUCTION_")):
            os.environ.pop(key)
    os.environ.update(DEMO_MODE="1", REPLAY_MODE="1")

    # Local ASGI requests only. Any accidental model/provider connection fails.
    def no_network(*args, **kwargs):
        raise AssertionError("The zero-key smoke check attempted a network connection")

    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    with tempfile.TemporaryDirectory() as directory:
        os.chdir(directory)
        from fastapi.testclient import TestClient

        import assistant
        from assistant import server
        from assistant.agent.replay import NO_RECORDING_REPLY

        package = Path(assistant.__file__).resolve()
        assert package.is_relative_to(Path(sys.prefix).resolve()), (
            "Expected a wheel installed inside this venv, not an editable source checkout"
        )
        with TestClient(server.create_app()) as client:
            health = client.get("/health").json()
            assert health["mode"] == "demo"
            assert health["replay_available"] is True
            assert health["production_available"] is False
            page = client.get("/")
            assert page.status_code == 200
            prompts = re.findall(r'data-quick-prompt="([^"]+)"', page.text)
            assert len(prompts) == 8, "Expected all eight shipped replay prompts"
            for prompt in prompts:
                response = client.post("/api/chat", json={"message": prompt})
                assert response.status_code == 200
                result = response.json()
                assert result["reply"] and result["reply"] != NO_RECORDING_REPLY
                assert result["tool_calls"], "Replay must actually execute tools"
            unknown = client.post("/api/chat", json={"message": "unrecorded-smoke-question"})
            assert unknown.json()["reply"] == NO_RECORDING_REPLY
            assert client.post("/api/mode", json={"mode": "production"}).status_code == 400
            # Check actual references so renaming an asset cannot skip this check.
            assets = re.findall(r'(?:src|href)="([^"?#]+\.(?:js|css|png))', page.text)
            assert assets
            for asset in assets:
                assert not asset.startswith(("http:", "https:", "//"))
                assert client.get("/" + asset.lstrip("./")).status_code == 200, asset
        print("Installed wheel: health, 8 replay prompts, assets, unknown prompt, mode guard PASS")


if __name__ == "__main__":
    main()
