"""Public demo boundaries must be executable checks, not comments in compose."""

import fnmatch
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_compose_has_only_a_loopback_synthetic_assistant():
    compose = yaml.safe_load((ROOT / "docker-compose.demo.yml").read_text())
    assert set(compose["services"]) == {"assistant"}
    service = compose["services"]["assistant"]
    assert service["ports"] == ["127.0.0.1:8100:8100"]
    assert service["environment"] == {"DEMO_MODE": "1", "REPLAY_MODE": "1"}
    assert not service.get("env_file")
    assert not service.get("volumes")
    assert not service.get("privileged")
    assert not service.get("network_mode")


def test_runtime_assets_are_in_the_wheel_manifest():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    manifest = config["tool"]["setuptools"]["package-data"]
    for directory, package, patterns in (
        ("assistant/replay", "assistant", ("*.json",)),
        ("assistant/frontend", "assistant", ("*.html", "*.js", "*.css")),
        ("assistant/frontend/assets", "assistant", ("*.png",)),
        ("assistant/frontend/fixtures", "assistant", ("*.json",)),
        ("assistant/demo_data", "assistant.demo_data", ("*.json",)),
        ("assistant/config", "assistant.config", ("defaults.yaml",)),
    ):
        files = [file for pattern in patterns for file in (ROOT / directory).glob(pattern)]
        assert files, directory
        base = ROOT / package.replace(".", "/")
        for file in files:
            relative = file.relative_to(base).as_posix()
            assert any(fnmatch.fnmatchcase(relative, item) for item in manifest[package]), relative


def test_ci_runs_checks_without_secrets_or_write_permissions():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"push", "pull_request", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["test"]
    assert job["strategy"]["matrix"]["python"] == ["3.12", "3.14"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "pytest -q -p no:cacheprovider" in commands
    assert "ruff check ." in commands
    assert "uv build --wheel" in commands
    assert "check_installed.py" in commands
    assert "secrets." not in (ROOT / ".github/workflows/ci.yml").read_text()
