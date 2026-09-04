"""固定 seed 的示範資料集（人名、電話、對話全是編的）。

`generate.py` 產生五個 JSON，並且**跟著進版控**：評審 clone 下來不必先跑產生器，
`docker compose up` 或 `uv run` 就有東西可問。
"""

from assistant.demo_data.generate import ANCHOR, DATA_DIR, generate, load_dataset

__all__ = ["ANCHOR", "DATA_DIR", "generate", "load_dataset"]
