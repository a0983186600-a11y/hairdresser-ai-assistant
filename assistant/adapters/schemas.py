"""8 個工具的輸入／輸出型別（pydantic v2）。

欄位照 `docs/agent-bakeoff/tools.md`，只有一處刻意不同：

**provider 回未遮罩的原始資料**（`full_name`／`phone`），tools.md 寫的
`masked_name`／`phone_last4` 是第二階段 tools 層用 `assistant.privacy` 轉出來的。
理由：正式 provider 與 Mock provider 走同一層 tools，遮罩只能有一份實作。

兩條共同規則：

- 時間一律 timezone-aware，且正規化成 Asia/Taipei。naive datetime 直接拒收——
  「現在幾點」在這個系統裡永遠是台北時間，不能靠呼叫端記得轉。
- `as_of` 是所有查詢的「現在」，由呼叫端傳；provider 內部不准 `datetime.now()`，
  否則測試會隨著跑的當天飄，示範也重現不了。
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "TAIPEI",
    "ServiceFamily",
    "MessageRole",
    "ConversationState",
    "TaipeiDatetime",
    "DesignerScope",
    "RankBySpendInput",
    "InactiveCustomersInput",
    "SegmentSearchInput",
    "CustomerHistoryInput",
    "RecentConversationsInput",
    "TranscriptInput",
    "RetentionWatchlistInput",
    "ServiceMetricsInput",
    "CustomerSpendRow",
    "InactiveCustomerRow",
    "SegmentCustomerRow",
    "CustomerVisit",
    "CustomerHistory",
    "ConversationSummary",
    "ConversationMessage",
    "ConversationTranscript",
    "RetentionRow",
    "ServiceMetrics",
]

TAIPEI = ZoneInfo("Asia/Taipei")


class ServiceFamily(StrEnum):
    """封閉 enum：模型只能從這六個裡挑，不准自己造一個服務名。"""

    CUT = "cut"
    PERM = "perm"
    COLOR = "color"
    TREATMENT = "treatment"
    BLEACH = "bleach"
    SCALP = "scalp"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    DESIGNER = "designer"


class ConversationState(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    HUMAN_TAKEOVER = "human_takeover"


def _require_taipei(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime 必須帶時區（Asia/Taipei）；naive datetime 不收")
    return value.astimezone(TAIPEI)


TaipeiDatetime = Annotated[datetime, AfterValidator(_require_taipei)]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class DesignerScope(_Base):
    """由登入工作階段注入的查詢範圍。

    刻意**不是**任何 input 模型的欄位：模型填不到 designer_ref，就沒辦法要求
    看別人的客人。每個 provider 方法的第一個參數都是它。
    """

    designer_ref: str
    display_name: str


# --- 輸入（這些就是給模型看的 tool schema 來源） -------------------------------


class RankBySpendInput(_Base):
    days: int = Field(ge=1, le=3650)
    limit: int = Field(ge=1, le=50)
    as_of: TaipeiDatetime


class InactiveCustomersInput(_Base):
    inactive_days: int = Field(ge=1, le=3650)
    limit: int = Field(ge=1, le=100)
    as_of: TaipeiDatetime


class SegmentSearchInput(_Base):
    as_of: TaipeiDatetime
    inactive_days_gte: int | None = Field(default=None, ge=1, le=3650)
    visits_gte: int | None = Field(default=None, ge=1, le=1000)
    visits_since: TaipeiDatetime | None = None
    visits_gte_in_period: int | None = Field(default=None, ge=1, le=1000)
    service_families: list[ServiceFamily] | None = None
    has_recent_conversation: bool | None = None
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def _period_threshold_needs_a_window(self) -> "SegmentSearchInput":
        """「期間內來幾次」得先講清楚「期間」是從哪天算。

        provider 的判斷是 `if visits_since is not None:` 包住 visits_gte_in_period
        的比較——只填門檻、不填起點時，門檻整段被跳過，靜默回全部客人。與其在
        provider 補猜一個起點（違反「確定性層不准猜」），不如在這裡就拒收。
        """
        if self.visits_gte_in_period is not None and self.visits_since is None:
            raise ValueError(
                "visits_gte_in_period 需要同時給 visits_since（期間起點）："
                "少了它這個門檻會被靜默忽略、回全部客人"
            )
        return self


class CustomerHistoryInput(_Base):
    customer_ref: str
    as_of: TaipeiDatetime
    limit: int = Field(default=100, ge=1, le=100)


class RecentConversationsInput(_Base):
    as_of: TaipeiDatetime
    customer_refs: list[str] | None = None
    limit: int = Field(default=20, ge=1, le=50)


class TranscriptInput(_Base):
    conversation_ref: str
    message_limit: int = Field(default=30, ge=1, le=30)


class RetentionWatchlistInput(_Base):
    as_of: TaipeiDatetime
    minimum_inactive_days: int = Field(default=45, ge=1, le=3650)
    limit: int = Field(default=20, ge=1, le=50)


class ServiceMetricsInput(_Base):
    service_families: list[ServiceFamily] = Field(min_length=1)
    start_at: TaipeiDatetime
    end_at: TaipeiDatetime

    @model_validator(mode="after")
    def _window_must_not_run_backwards(self) -> "ServiceMetricsInput":
        """參數寫反時，provider 的 `start_at <= moment <= end_at` 永遠為假，
        回全 0 卻附一句「已知金額合計」很肯定的 coverage_note。與其回一個看似真實
        的空結果，不如當場拒收。相等（零寬視窗）視為合法邊界。"""
        if self.start_at > self.end_at:
            raise ValueError(
                "start_at 必須早於或等於 end_at；"
                f"收到的區間是反的（start_at={self.start_at.isoformat()} "
                f"晚於 end_at={self.end_at.isoformat()}）"
            )
        return self


# --- 輸出 --------------------------------------------------------------------


#: 錢是從哪裡來的一句話（POS 實收／預約報價）。
#:
#: 選填，而且**只有正式 provider 會填**：Mock 的金額本來就是自己造的，
#: 沒有「來源」這個問題。加在這裡而不是各自造一個欄位，是因為報價與實收在
#: `known_spend_twd` 底下長得一模一樣——分不出來的那一刻，設計師會把報價當營收。
DataSourceNote = str | None


class CustomerSpendRow(_Base):
    customer_ref: str
    full_name: str
    phone: str | None = None
    known_spend_twd: int = Field(ge=0)
    visit_count: int = Field(ge=0)
    unknown_amount_visits: int = Field(ge=0)
    last_visit_at: TaipeiDatetime | None = None
    data_source_note: DataSourceNote = None


class InactiveCustomerRow(_Base):
    customer_ref: str
    full_name: str
    phone: str | None = None
    days_since_last_visit: int = Field(ge=0)
    last_visit_at: TaipeiDatetime | None = None
    visit_count: int = Field(ge=0)
    last_service: ServiceFamily | None = None


class SegmentCustomerRow(_Base):
    customer_ref: str
    full_name: str
    phone: str | None = None
    visit_count: int = Field(ge=0)
    last_visit_at: TaipeiDatetime | None = None
    days_since_last_visit: int = Field(ge=0)
    matched_service_families: list[ServiceFamily] = Field(default_factory=list)


class CustomerVisit(_Base):
    visited_at: TaipeiDatetime
    #: 六個家族之一，或**空的**。空不是「沒查」，是「這筆看不出是哪一種服務」——
    #: POS 的消費紀錄沒有服務欄位，只有備註與消費內容可以推。
    #: 挑一個看起來合理的填進去，就是替設計師編一次服務（Tai 案 2026-07-25）。
    service: ServiceFamily | None = None
    amount_twd: int | None = Field(default=None, ge=0)
    #: POS 的「消費內容」（沒有的話退回 POS 備註）。判不出服務家族時，
    #: 這一格就是那筆錢做了什麼的唯一線索——所以那種列照樣列出來，只是 `service` 空著。
    items_text: str | None = None
    payment_method: str | None = None
    store_name: str | None = None


class CustomerHistory(_Base):
    customer_ref: str
    full_name: str
    phone: str | None = None
    visit_count: int = Field(ge=0)
    known_spend_twd: int = Field(ge=0)
    unknown_amount_visits: int = Field(ge=0)
    visits: list[CustomerVisit] = Field(default_factory=list)
    data_source_note: DataSourceNote = None


class ConversationSummary(_Base):
    conversation_ref: str
    customer_ref: str
    full_name: str
    updated_at: TaipeiDatetime
    state: ConversationState
    message_count: int = Field(ge=0)


class ConversationMessage(_Base):
    role: MessageRole
    created_at: TaipeiDatetime
    content: str


class ConversationTranscript(_Base):
    conversation_ref: str
    customer_ref: str
    state: ConversationState
    safe_draft_fields: dict[str, Any] = Field(default_factory=dict)
    identity_ambiguity: bool = False
    messages: list[ConversationMessage] = Field(default_factory=list)


class RetentionRow(_Base):
    customer_ref: str
    full_name: str
    phone: str | None = None
    risk_score: float = Field(ge=0)
    days_since_last_visit: int = Field(ge=0)
    visit_count: int = Field(ge=0)
    known_spend_twd: int = Field(ge=0)
    reasons: list[str] = Field(default_factory=list)


class ServiceMetrics(_Base):
    linked_customer_count: int = Field(ge=0)
    visit_count: int = Field(ge=0)
    known_spend_twd: int = Field(ge=0)
    unknown_amount_visits: int = Field(ge=0)
    coverage_note: str
