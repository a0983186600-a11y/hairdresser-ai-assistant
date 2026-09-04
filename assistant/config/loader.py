"""設定載入：defaults → local.yaml → 環境變數指定的檔 → 明確傳進來的路徑。

為什麼要有這一層：公開 repo 必須「邏輯公開、參數可換」。工具實作、agent 迴圈、
考卷與測試都在 repo 裡看得見；只有每家店會不一樣的數字與話術（流失權重、門檻、
人設語氣、回訪模板、服務中文名、模型端點）抽成設定。抽的界線就在這個檔案裡——
再往裡抽就變空殼，會被看穿。

合併是**深合併**：local.yaml 只寫要改的那一格，其餘照 defaults，
不必整份複製（整份複製的下場是 defaults 改了它不會跟）。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Config",
    "ModelConfig",
    "AgentConfig",
    "RetentionConfig",
    "RetentionWeights",
    "RetentionCaps",
    "FollowUpTemplate",
    "DEFAULTS_PATH",
    "LOCAL_FILENAME",
    "CONFIG_PATH_ENV",
    "deep_merge",
    "load_config",
]

CONFIG_DIR = Path(__file__).resolve().parent
DEFAULTS_PATH = CONFIG_DIR / "defaults.yaml"
LOCAL_FILENAME = "local.yaml"
CONFIG_PATH_ENV = "ASSISTANT_CONFIG_PATH"


class _Base(BaseModel):
    # extra="forbid"：設定檔打錯字要當場紅，不要安靜地沿用預設值跑一整場 demo。
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class ModelConfig(_Base):
    provider: str
    base_url_env: str
    api_key_env: str
    model_env: str
    model_default: str


class AgentConfig(_Base):
    max_iterations: int = Field(ge=1, le=20)
    tool_result_limit: int = Field(ge=1)
    replay_dir: str


class RetentionWeights(_Base):
    days: float
    visits: float
    spend_divisor: float = Field(gt=0)


class RetentionCaps(_Base):
    days: int = Field(ge=1)
    visits: int = Field(ge=1)
    spend: int = Field(ge=1)


class RetentionConfig(_Base):
    min_visits: int = Field(ge=1)
    min_inactive_days: int = Field(ge=1)
    weights: RetentionWeights
    caps: RetentionCaps


class FollowUpTemplate(_Base):
    id: str
    label: str
    text: str


class Config(_Base):
    model: ModelConfig
    agent: AgentConfig
    retention: RetentionConfig
    inactive_default_days: int = Field(ge=1)
    recent_conversation_days: int = Field(ge=1)
    persona: str
    follow_up_templates: list[FollowUpTemplate]
    service_family_labels: dict[str, str]


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """把 override 疊到 base 上，只換被寫到的那一格；不修改任何一邊。"""
    merged: dict[str, Any] = deepcopy(dict(base))
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise ValueError(f"設定檔最外層必須是對應表：{path}")
    return dict(data)


def load_config(path: str | Path | None = None, *, defaults_path: Path | None = None) -> Config:
    """依序疊起來：defaults → 同目錄 local.yaml → `ASSISTANT_CONFIG_PATH` → `path`。

    後面的贏。`path` 放最後，因為那是呼叫端當場指定的，意圖最明確
    （測試就靠這個把一份設定餵進去，不必碰工作樹裡的檔案）。
    """
    base_file = Path(defaults_path) if defaults_path is not None else DEFAULTS_PATH
    data = _read_yaml(base_file)

    local_file = base_file.parent / LOCAL_FILENAME
    if local_file.exists():
        data = deep_merge(data, _read_yaml(local_file))

    env_path = os.environ.get(CONFIG_PATH_ENV)
    if env_path:
        data = deep_merge(data, _read_yaml(Path(env_path)))

    if path is not None:
        data = deep_merge(data, _read_yaml(Path(path)))

    return Config.model_validate(data)
