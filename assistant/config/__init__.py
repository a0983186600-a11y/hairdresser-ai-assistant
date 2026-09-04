"""設定：defaults.yaml 進 repo（示範值），local.yaml 留給實際營運者（.gitignore）。"""

from assistant.config.loader import Config, load_config

__all__ = ["Config", "load_config"]
