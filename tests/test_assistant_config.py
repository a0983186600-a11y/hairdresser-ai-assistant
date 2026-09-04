"""config：defaults 進 repo、local.yaml 覆蓋、環境變數再覆蓋，全部深合併。

「邏輯公開、參數可換」——權重與話術是設定，工具實作與 agent 迴圈不是。
"""

from pathlib import Path

import pytest

from assistant.config.loader import DEFAULTS_PATH, Config, deep_merge, load_config


def test_defaults_yaml_is_in_the_repo():
    assert DEFAULTS_PATH.exists()
    assert DEFAULTS_PATH.name == "defaults.yaml"


def test_defaults_match_the_numbers_in_tools_md():
    cfg = load_config()
    assert isinstance(cfg, Config)
    assert cfg.retention.min_visits == 2
    assert cfg.retention.min_inactive_days == 45
    assert cfg.retention.weights.days == 0.4
    assert cfg.retention.weights.visits == 4
    assert cfg.retention.weights.spend_divisor == 1000
    assert cfg.retention.caps.days == 180
    assert cfg.retention.caps.visits == 10
    assert cfg.retention.caps.spend == 20000
    assert cfg.inactive_default_days == 60


def test_model_and_agent_defaults_point_at_env_var_names_not_secrets():
    cfg = load_config()
    assert cfg.model.provider == "openai_compatible"
    assert cfg.model.base_url_env == "QWEN_BASE_URL"
    assert cfg.model.api_key_env == "QWEN_API_KEY"
    assert cfg.model.model_env == "QWEN_MODEL"
    assert cfg.model.model_default == "qwen-plus"
    assert cfg.agent.max_iterations == 6
    assert cfg.agent.tool_result_limit == 200
    assert cfg.agent.replay_dir
    # 金鑰本身不准出現在設定檔裡，只准出現「去哪個環境變數拿」。
    raw = DEFAULTS_PATH.read_text(encoding="utf-8")
    assert "sk" "-" not in raw
    assert "http://" not in raw and "https://" not in raw


def test_persona_and_templates_and_labels_are_present():
    cfg = load_config()
    assert len(cfg.persona.strip()) >= 20
    assert len(cfg.follow_up_templates) == 2
    for tpl in cfg.follow_up_templates:
        assert "{name}" in tpl.text
        assert "{service}" in tpl.text
        assert "{days}" in tpl.text
        # 模板要能真的 format 出來，不能有多餘的佔位符。
        tpl.text.format(name="王○明", service="染髮", days=90)
    assert set(cfg.service_family_labels) == {
        "cut",
        "perm",
        "color",
        "treatment",
        "bleach",
        "scalp",
    }
    assert cfg.service_family_labels["color"] == "染髮"


def test_deep_merge_only_replaces_the_leaf_that_was_overridden():
    base = {"retention": {"weights": {"days": 0.4, "visits": 4}, "min_visits": 2}}
    override = {"retention": {"weights": {"days": 0.9}}}
    merged = deep_merge(base, override)
    assert merged == {"retention": {"weights": {"days": 0.9, "visits": 4}, "min_visits": 2}}
    # 不准就地改到 base（呼叫兩次會越疊越亂）。
    assert base["retention"]["weights"]["days"] == 0.4


def test_explicit_path_overrides_defaults(tmp_path: Path):
    override = tmp_path / "mine.yaml"
    override.write_text(
        "retention:\n  min_inactive_days: 90\n  weights:\n    days: 0.9\n",
        encoding="utf-8",
    )
    cfg = load_config(override)
    assert cfg.retention.min_inactive_days == 90
    assert cfg.retention.weights.days == 0.9
    # 沒被覆蓋的維持 defaults。
    assert cfg.retention.weights.visits == 4
    assert cfg.retention.min_visits == 2


def test_env_var_config_path_is_honoured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    override = tmp_path / "env.yaml"
    override.write_text("inactive_default_days: 120\n", encoding="utf-8")
    monkeypatch.setenv("ASSISTANT_CONFIG_PATH", str(override))
    assert load_config().inactive_default_days == 120


def test_explicit_path_wins_over_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_file = tmp_path / "env.yaml"
    env_file.write_text("inactive_default_days: 120\n", encoding="utf-8")
    arg_file = tmp_path / "arg.yaml"
    arg_file.write_text("inactive_default_days: 30\n", encoding="utf-8")
    monkeypatch.setenv("ASSISTANT_CONFIG_PATH", str(env_file))
    assert load_config(arg_file).inactive_default_days == 30


def test_local_yaml_next_to_defaults_overrides_defaults(tmp_path: Path):
    # 直接在 defaults 旁邊寫 local.yaml 會污染工作樹，所以複製一份 defaults 到 tmp 再試。
    from assistant.config import loader as loader_mod

    sandbox = tmp_path / "config"
    sandbox.mkdir()
    (sandbox / "defaults.yaml").write_text(
        DEFAULTS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (sandbox / "local.yaml").write_text("inactive_default_days: 45\n", encoding="utf-8")

    cfg = loader_mod.load_config(defaults_path=sandbox / "defaults.yaml")
    assert cfg.inactive_default_days == 45
    assert cfg.retention.min_visits == 2


def test_local_yaml_is_gitignored():
    root = Path(__file__).resolve().parents[1]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert "assistant/config/local.yaml" in ignored
    assert not (root / "assistant" / "config" / "local.yaml").exists()


def test_unknown_key_in_config_is_rejected(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("retention:\n  mystery_knob: 7\n", encoding="utf-8")
    with pytest.raises(Exception) as exc:
        load_config(bad)
    assert "mystery_knob" in str(exc.value)


def test_the_default_replay_dir_points_at_the_recordings_we_ship(monkeypatch, tmp_path):
    """defaults.yaml 的 replay_dir 曾寫成 replays（不存在），伺服器載到 0 段錄音卻自報可用。

    使用真正的 ReplayClient 解析，從任意 cwd 仍必須找到出貨錄音；不能在測試裡手動補 repo_root。
    """
    from assistant.agent.replay import REPLAY_DIR, ReplayClient
    from assistant.config.loader import load_config

    monkeypatch.chdir(tmp_path)
    configured = ReplayClient(load_config().agent.replay_dir).directory.resolve()
    assert configured == REPLAY_DIR.resolve(), configured
    assert len(list(configured.glob("*.json"))) >= 6
