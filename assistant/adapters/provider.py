"""`SalonDataProvider`：8 個工具唯一的資料入口。

公開 repo 附 `MockSalonDataProvider`（固定 seed 假資料），私有 repo 另外注入唯讀的
正式 provider。上面的 tools 層與 agent 迴圈兩邊完全共用——「公開的不是空殼」就靠這條界線。

三條不准破的規矩：

1. **第一個參數永遠是 `scope: DesignerScope`**，由登入工作階段注入。
   模型的 tool schema 裡看不到 designer_ref，所以它要不到別人的客人。
2. **`as_of` 是「現在」**，由呼叫端傳進來。實作內不准 `datetime.now()`：
   示範要能重現、測試不能隨當天飄。
3. **回未遮罩的原始資料**。遮罩（`assistant.privacy`）在上層 tools 做，
   正式與 Mock 才只有一份遮罩實作。

`customer_ref` / `conversation_ref` 只是識別碼，不是授權：實作必須再用 scope 查一次，
不在自己範圍內就回空 list / `None`——**不是報錯**，報錯等於洩漏「這個 ref 存在」。
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from assistant.adapters.schemas import (
    ConversationSummary,
    ConversationTranscript,
    CustomerHistory,
    CustomerSpendRow,
    DesignerScope,
    InactiveCustomerRow,
    RetentionRow,
    SegmentCustomerRow,
    ServiceFamily,
    ServiceMetrics,
)

__all__ = ["SalonDataProvider", "TOOL_METHOD_NAMES"]

#: tools.md 的 8 個工具，順序照文件。tools 層與契約測試都以這份為準。
TOOL_METHOD_NAMES: tuple[str, ...] = (
    "rank_customers_by_spend",
    "list_inactive_customers",
    "search_customer_segment",
    "get_customer_history",
    "list_recent_conversations",
    "get_conversation_transcript",
    "get_retention_watchlist",
    "get_service_metrics",
)


@runtime_checkable
class SalonDataProvider(Protocol):
    """設計師助理能碰到的全部資料。除了這 8 個方法，沒有別的門。"""

    def rank_customers_by_spend(
        self,
        scope: DesignerScope,
        *,
        days: int,
        limit: int,
        as_of: datetime,
    ) -> list[CustomerSpendRow]:
        """最近 `days` 天消費金額排行。金額只算有金額的那幾次，缺的另計。"""
        ...

    def list_inactive_customers(
        self,
        scope: DesignerScope,
        *,
        inactive_days: int,
        limit: int,
        as_of: datetime,
    ) -> list[InactiveCustomerRow]:
        """至少 `inactive_days` 天沒回來的客人，久的排前面。"""
        ...

    def search_customer_segment(
        self,
        scope: DesignerScope,
        *,
        as_of: datetime,
        inactive_days_gte: int | None = None,
        visits_gte: int | None = None,
        visits_since: datetime | None = None,
        visits_gte_in_period: int | None = None,
        service_families: Sequence[ServiceFamily] | None = None,
        has_recent_conversation: bool | None = None,
        limit: int = 20,
    ) -> list[SegmentCustomerRow]:
        """條件組合查詢（沒回來多久、來過幾次、做過哪些服務、最近有沒有對話）。"""
        ...

    def get_customer_history(
        self,
        scope: DesignerScope,
        *,
        customer_ref: str,
        as_of: datetime,
        limit: int = 100,
    ) -> CustomerHistory | None:
        """單一客人的到店明細。不在 scope 內或查無此人一律回 `None`。"""
        ...

    def list_recent_conversations(
        self,
        scope: DesignerScope,
        *,
        as_of: datetime,
        customer_refs: Sequence[str] | None = None,
        limit: int = 20,
    ) -> list[ConversationSummary]:
        """最近有動靜的對話，新的排前面。"""
        ...

    def get_conversation_transcript(
        self,
        scope: DesignerScope,
        *,
        conversation_ref: str,
        message_limit: int = 30,
    ) -> ConversationTranscript | None:
        """單一對話的逐字稿（最後 `message_limit` 則，時間順）。不在 scope 內回 `None`。"""
        ...

    def get_retention_watchlist(
        self,
        scope: DesignerScope,
        *,
        as_of: datetime,
        minimum_inactive_days: int = 45,
        limit: int = 20,
    ) -> list[RetentionRow]:
        """快流失名單。分數與門檻是**固定算法**（見 config.retention），模型不准自己換一套。"""
        ...

    def get_service_metrics(
        self,
        scope: DesignerScope,
        *,
        service_families: Sequence[ServiceFamily],
        start_at: datetime,
        end_at: datetime,
    ) -> ServiceMetrics:
        """某幾種服務在一段期間的人數／次數／已知金額，附「金額涵蓋範圍」說明。"""
        ...
