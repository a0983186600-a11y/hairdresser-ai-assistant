"""設計師 AI 助理（BUILDMODE 2026 公開部分）。

這個套件是「匯出到公開 repo」的那一份，所以它**完全獨立**：
不 import 正式系統的 `app.*`、不連正式資料庫、不含任何金鑰或真實客人資料。
資料一律經由 `assistant.adapters.provider.SalonDataProvider` 取得；
公開版注入 `MockSalonDataProvider`（讀套件內的固定 seed 假資料），
正式版在私有 repo 裡注入唯讀的 production provider——兩邊共用同一份工具與 agent 程式。
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
