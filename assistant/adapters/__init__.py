"""資料來源介面與實作。

公開 repo 只帶 `MockSalonDataProvider`（固定 seed 假資料）；
正式的唯讀 provider 留在私有 repo，實作同一個 `SalonDataProvider` 介面。
"""

from assistant.adapters.mock import MockSalonDataProvider
from assistant.adapters.provider import SalonDataProvider
from assistant.adapters.schemas import DesignerScope, ServiceFamily

__all__ = [
    "SalonDataProvider",
    "MockSalonDataProvider",
    "DesignerScope",
    "ServiceFamily",
]
