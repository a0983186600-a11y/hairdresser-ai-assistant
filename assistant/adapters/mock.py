"""`MockSalonDataProvider`：公開 repo 的預設資料來源。

讀套件內固定 seed 的假資料（`assistant/demo_data`），把 tools.md 那 8 個工具的語意
完整實作一遍。評審 clone 下來不用金鑰、不用資料庫，直接就能問問題。

三件跟正式 provider 一模一樣、不是「示範版打折」的事：

1. **每一次查詢都先用 scope 過濾**。`customer_ref`／`conversation_ref` 只是識別碼，
   不是授權：拿到別的設計師的 ref，回空 list／`None`，**不報錯**——報錯等於
   承認那個 ref 存在。
2. **`as_of` 是唯一的「現在」**。這個檔案裡沒有 `datetime.now()`。
3. **門檻與權重從 config 讀**（`assistant/config/defaults.yaml`），
   預設值等於 tools.md 寫的數字。算式本身不可換，換了模型就各講各的分數。

輸入一律先過 `schemas` 的 input 模型：封閉 enum、上下限、naive datetime 全部在
這一關擋掉，Mock 與正式因此擋在同一個地方。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from assistant.adapters.schemas import (
    TAIPEI,
    ConversationMessage,
    ConversationState,
    ConversationSummary,
    ConversationTranscript,
    CustomerHistory,
    CustomerHistoryInput,
    CustomerSpendRow,
    CustomerVisit,
    DesignerScope,
    InactiveCustomerRow,
    InactiveCustomersInput,
    MessageRole,
    RankBySpendInput,
    RecentConversationsInput,
    RetentionRow,
    RetentionWatchlistInput,
    SegmentCustomerRow,
    SegmentSearchInput,
    ServiceFamily,
    ServiceMetrics,
    ServiceMetricsInput,
    TranscriptInput,
)
from assistant.config.loader import Config, load_config
from assistant.demo_data.generate import DATA_DIR, load_dataset

__all__ = ["MockSalonDataProvider"]

_FAMILY_ORDER = {family: index for index, family in enumerate(ServiceFamily)}


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).astimezone(TAIPEI)


class _CustomerRecord:
    """一位客人＋他所有的到店紀錄，先算好、查的時候不用再翻整份 visits。"""

    __slots__ = ("ref", "designer_ref", "full_name", "phone", "visits")

    def __init__(self, row: dict) -> None:
        self.ref: str = row["customer_ref"]
        self.designer_ref: str = row["designer_ref"]
        self.full_name: str = row["full_name"]
        self.phone: str | None = row.get("phone")
        self.visits: list[tuple[datetime, ServiceFamily, int | None]] = []

    def up_to(self, as_of: datetime) -> list[tuple[datetime, ServiceFamily, int | None]]:
        return [visit for visit in self.visits if visit[0] <= as_of]


class MockSalonDataProvider:
    """`SalonDataProvider` 的假資料實作。介面與語意跟正式版同一份規格。"""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        config: Config | None = None,
    ) -> None:
        self._data_dir = Path(data_dir) if data_dir is not None else DATA_DIR
        self._config = config if config is not None else load_config()
        dataset = load_dataset(self._data_dir)

        self._designers: list[dict] = dataset["designers"]
        self._customers: dict[str, _CustomerRecord] = {}
        for row in dataset["customers"]:
            self._customers[row["customer_ref"]] = _CustomerRecord(row)
        for row in dataset["visits"]:
            record = self._customers.get(row["customer_ref"])
            if record is None:
                continue
            record.visits.append(
                (
                    _parse(row["visited_at"]),
                    ServiceFamily(row["service_family"]),
                    row["amount_twd"],
                )
            )
        for record in self._customers.values():
            record.visits.sort(key=lambda visit: visit[0])

        self._conversations: dict[str, dict] = {
            row["conversation_ref"]: row for row in dataset["conversations"]
        }
        self._appointments: list[dict] = dataset["appointments"]

    # --- 給示範程式與測試用的小工具 ------------------------------------------

    @property
    def config(self) -> Config:
        return self._config

    def designer_scopes(self) -> list[DesignerScope]:
        """假資料裡的三位設計師。**正式版沒有這個方法**——正式的 scope 來自登入工作階段。"""
        return [
            DesignerScope(designer_ref=row["designer_ref"], display_name=row["display_name"])
            for row in self._designers
        ]

    # --- 內部 ----------------------------------------------------------------

    def _mine(self, scope: DesignerScope) -> list[_CustomerRecord]:
        return [
            record
            for record in self._customers.values()
            if record.designer_ref == scope.designer_ref
        ]

    def _owned(self, scope: DesignerScope, customer_ref: str) -> _CustomerRecord | None:
        record = self._customers.get(customer_ref)
        if record is None or record.designer_ref != scope.designer_ref:
            return None
        return record

    @staticmethod
    def _days_since(as_of: datetime, moment: datetime) -> int:
        return (as_of - moment).days

    def _conversation_updated(self, scope: DesignerScope, as_of: datetime) -> dict[str, datetime]:
        latest: dict[str, datetime] = {}
        for row in self._conversations.values():
            if row["designer_ref"] != scope.designer_ref:
                continue
            updated = _parse(row["updated_at"])
            if updated > as_of:
                continue
            current = latest.get(row["customer_ref"])
            if current is None or updated > current:
                latest[row["customer_ref"]] = updated
        return latest

    # --- 1. rank_customers_by_spend ------------------------------------------

    def rank_customers_by_spend(
        self,
        scope: DesignerScope,
        *,
        days: int,
        limit: int,
        as_of: datetime,
    ) -> list[CustomerSpendRow]:
        args = RankBySpendInput(days=days, limit=limit, as_of=as_of)
        window_start = args.as_of - timedelta(days=args.days)

        rows: list[CustomerSpendRow] = []
        for record in self._mine(scope):
            in_window = [
                visit for visit in record.visits if window_start <= visit[0] <= args.as_of
            ]
            if not in_window:
                continue
            known = sum(amount for _, _, amount in in_window if amount is not None)
            blanks = sum(1 for _, _, amount in in_window if amount is None)
            rows.append(
                CustomerSpendRow(
                    customer_ref=record.ref,
                    full_name=record.full_name,
                    phone=record.phone,
                    known_spend_twd=known,
                    visit_count=len(in_window),
                    unknown_amount_visits=blanks,
                    last_visit_at=max(visit[0] for visit in in_window),
                )
            )

        rows.sort(key=lambda row: (-row.known_spend_twd, -row.visit_count, row.customer_ref))
        return rows[: args.limit]

    # --- 2. list_inactive_customers ------------------------------------------

    def list_inactive_customers(
        self,
        scope: DesignerScope,
        *,
        inactive_days: int,
        limit: int,
        as_of: datetime,
    ) -> list[InactiveCustomerRow]:
        args = InactiveCustomersInput(inactive_days=inactive_days, limit=limit, as_of=as_of)

        rows: list[InactiveCustomerRow] = []
        for record in self._mine(scope):
            seen = record.up_to(args.as_of)
            if not seen:
                continue
            last_at, last_family, _ = seen[-1]
            gap = self._days_since(args.as_of, last_at)
            if gap < args.inactive_days:
                continue
            rows.append(
                InactiveCustomerRow(
                    customer_ref=record.ref,
                    full_name=record.full_name,
                    phone=record.phone,
                    days_since_last_visit=gap,
                    last_visit_at=last_at,
                    visit_count=len(seen),
                    last_service=last_family,
                )
            )

        rows.sort(key=lambda row: (-row.days_since_last_visit, row.customer_ref))
        return rows[: args.limit]

    # --- 3. search_customer_segment -------------------------------------------

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
        args = SegmentSearchInput(
            as_of=as_of,
            inactive_days_gte=inactive_days_gte,
            visits_gte=visits_gte,
            visits_since=visits_since,
            visits_gte_in_period=visits_gte_in_period,
            service_families=list(service_families) if service_families is not None else None,
            has_recent_conversation=has_recent_conversation,
            limit=limit,
        )
        wanted = set(args.service_families or [])
        recent_window = args.as_of - timedelta(days=self._config.recent_conversation_days)
        latest_conversation = (
            self._conversation_updated(scope, args.as_of)
            if args.has_recent_conversation is not None
            else {}
        )

        scored: list[tuple[int, SegmentCustomerRow]] = []
        for record in self._mine(scope):
            seen = record.up_to(args.as_of)
            if not seen:
                continue
            last_at = seen[-1][0]
            gap = self._days_since(args.as_of, last_at)

            if args.inactive_days_gte is not None and gap < args.inactive_days_gte:
                continue
            if args.visits_gte is not None and len(seen) < args.visits_gte:
                continue

            period_visits = 0
            if args.visits_since is not None:
                period_visits = sum(1 for visit in seen if visit[0] >= args.visits_since)
                if (
                    args.visits_gte_in_period is not None
                    and period_visits < args.visits_gte_in_period
                ):
                    continue

            families = {visit[1] for visit in seen}
            matched = sorted(families & wanted, key=lambda f: _FAMILY_ORDER[f]) if wanted else []
            if wanted and not matched:
                continue

            if args.has_recent_conversation is not None:
                updated = latest_conversation.get(record.ref)
                is_recent = updated is not None and updated >= recent_window
                if is_recent is not args.has_recent_conversation:
                    continue

            scored.append(
                (
                    period_visits,
                    SegmentCustomerRow(
                        customer_ref=record.ref,
                        full_name=record.full_name,
                        phone=record.phone,
                        visit_count=len(seen),
                        last_visit_at=last_at,
                        days_since_last_visit=gap,
                        matched_service_families=matched,
                    ),
                )
            )

        # 排序看設計師問的是什麼：
        # 問「期間內來幾次」就照期間次數排（AH-08「按次數排序」），
        # 問「多久沒回來」就照天數排，都沒問就照總到店次數排。熟客／久沒回的排前面。
        if args.visits_since is not None:
            scored.sort(key=lambda pair: (-pair[0], -pair[1].visit_count, pair[1].customer_ref))
        elif args.inactive_days_gte is not None:
            scored.sort(
                key=lambda pair: (-pair[1].days_since_last_visit, pair[1].customer_ref)
            )
        else:
            scored.sort(key=lambda pair: (-pair[1].visit_count, pair[1].customer_ref))

        return [row for _, row in scored[: args.limit]]

    # --- 4. get_customer_history ----------------------------------------------

    def get_customer_history(
        self,
        scope: DesignerScope,
        *,
        customer_ref: str,
        as_of: datetime,
        limit: int = 100,
    ) -> CustomerHistory | None:
        args = CustomerHistoryInput(customer_ref=customer_ref, as_of=as_of, limit=limit)
        record = self._owned(scope, args.customer_ref)
        if record is None:
            return None

        seen = record.up_to(args.as_of)
        known = sum(amount for _, _, amount in seen if amount is not None)
        blanks = sum(1 for _, _, amount in seen if amount is None)
        # 明細只給最近 limit 筆，但次數與金額算的是全部——
        # 截斷的是「看幾筆」，不是「算幾筆」，否則設計師會以為客人只消費了這些。
        newest_first = sorted(seen, key=lambda visit: visit[0], reverse=True)[: args.limit]

        return CustomerHistory(
            customer_ref=record.ref,
            full_name=record.full_name,
            phone=record.phone,
            visit_count=len(seen),
            known_spend_twd=known,
            unknown_amount_visits=blanks,
            visits=[
                CustomerVisit(visited_at=moment, service=family, amount_twd=amount)
                for moment, family, amount in newest_first
            ],
        )

    # --- 5. list_recent_conversations -----------------------------------------

    def list_recent_conversations(
        self,
        scope: DesignerScope,
        *,
        as_of: datetime,
        customer_refs: Sequence[str] | None = None,
        limit: int = 20,
    ) -> list[ConversationSummary]:
        args = RecentConversationsInput(
            as_of=as_of,
            customer_refs=list(customer_refs) if customer_refs is not None else None,
            limit=limit,
        )
        # 過濾清單也要先過 scope：別人的 customer_ref 傳進來當篩選條件同樣要落空。
        wanted = (
            {ref for ref in args.customer_refs if self._owned(scope, ref) is not None}
            if args.customer_refs is not None
            else None
        )
        if wanted is not None and not wanted:
            return []

        rows: list[ConversationSummary] = []
        for row in self._conversations.values():
            if row["designer_ref"] != scope.designer_ref:
                continue
            if wanted is not None and row["customer_ref"] not in wanted:
                continue
            updated = _parse(row["updated_at"])
            if updated > args.as_of:
                continue
            record = self._customers.get(row["customer_ref"])
            rows.append(
                ConversationSummary(
                    conversation_ref=row["conversation_ref"],
                    customer_ref=row["customer_ref"],
                    full_name=record.full_name if record else "",
                    updated_at=updated,
                    state=ConversationState(row["state"]),
                    message_count=len(row["messages"]),
                )
            )

        rows.sort(key=lambda row: (row.updated_at, row.conversation_ref), reverse=True)
        return rows[: args.limit]

    # --- 6. get_conversation_transcript ---------------------------------------

    def get_conversation_transcript(
        self,
        scope: DesignerScope,
        *,
        conversation_ref: str,
        message_limit: int = 30,
    ) -> ConversationTranscript | None:
        args = TranscriptInput(conversation_ref=conversation_ref, message_limit=message_limit)
        row = self._conversations.get(args.conversation_ref)
        if row is None or row["designer_ref"] != scope.designer_ref:
            return None

        tail = row["messages"][-args.message_limit :]
        return ConversationTranscript(
            conversation_ref=row["conversation_ref"],
            customer_ref=row["customer_ref"],
            state=ConversationState(row["state"]),
            safe_draft_fields=dict(row.get("safe_draft_fields") or {}),
            identity_ambiguity=bool(row.get("identity_ambiguity")),
            messages=[
                ConversationMessage(
                    role=MessageRole(message["role"]),
                    created_at=_parse(message["created_at"]),
                    content=message["content"],
                )
                for message in tail
            ],
        )

    # --- 7. get_retention_watchlist -------------------------------------------

    def get_retention_watchlist(
        self,
        scope: DesignerScope,
        *,
        as_of: datetime,
        minimum_inactive_days: int = 45,
        limit: int = 20,
    ) -> list[RetentionRow]:
        args = RetentionWatchlistInput(
            as_of=as_of, minimum_inactive_days=minimum_inactive_days, limit=limit
        )
        rules = self._config.retention
        labels = self._config.service_family_labels
        # config 的門檻是**地板**：呼叫端只能把門檻往上拉，不能把它調鬆。
        # 「快流失」要是每次問都換一套標準，設計師就沒辦法比較兩天的名單。
        threshold = max(args.minimum_inactive_days, rules.min_inactive_days)

        rows: list[RetentionRow] = []
        for record in self._mine(scope):
            seen = record.up_to(args.as_of)
            if len(seen) < rules.min_visits:
                continue
            last_at, last_family, _ = seen[-1]
            gap = self._days_since(args.as_of, last_at)
            if gap < threshold:
                continue

            known = sum(amount for _, _, amount in seen if amount is not None)
            blanks = sum(1 for _, _, amount in seen if amount is None)
            score = (
                min(gap, rules.caps.days) * rules.weights.days
                + min(len(seen), rules.caps.visits) * rules.weights.visits
                + min(known, rules.caps.spend) / rules.weights.spend_divisor
            )

            reasons = [
                f"已經 {gap} 天沒回來",
                f"累積到店 {len(seen)} 次",
                f"已知消費 {known:,} 元",
                f"上次做的是{labels.get(last_family.value, last_family.value)}",
            ]
            if blanks:
                reasons.append(f"其中 {blanks} 次沒有金額紀錄，實際消費可能更高")

            rows.append(
                RetentionRow(
                    customer_ref=record.ref,
                    full_name=record.full_name,
                    phone=record.phone,
                    risk_score=round(score, 2),
                    days_since_last_visit=gap,
                    visit_count=len(seen),
                    known_spend_twd=known,
                    reasons=reasons,
                )
            )

        rows.sort(key=lambda row: (-row.risk_score, row.customer_ref))
        return rows[: args.limit]

    # --- 8. get_service_metrics -----------------------------------------------

    def get_service_metrics(
        self,
        scope: DesignerScope,
        *,
        service_families: Sequence[ServiceFamily],
        start_at: datetime,
        end_at: datetime,
    ) -> ServiceMetrics:
        args = ServiceMetricsInput(
            service_families=list(service_families), start_at=start_at, end_at=end_at
        )
        wanted = set(args.service_families)

        people: set[str] = set()
        visit_count = 0
        known = 0
        blanks = 0
        for record in self._mine(scope):
            for moment, family, amount in record.visits:
                if family not in wanted:
                    continue
                if not (args.start_at <= moment <= args.end_at):
                    continue
                visit_count += 1
                people.add(record.ref)
                if amount is None:
                    blanks += 1
                else:
                    known += amount

        labels = self._config.service_family_labels
        names = "、".join(
            labels.get(family.value, family.value)
            for family in sorted(wanted, key=lambda f: _FAMILY_ORDER[f])
        )
        note = (
            f"{names}：這是**已知金額**的合計，不含 {blanks} 筆沒有金額紀錄的到店；"
            "實際實收可能更高，不要當成完整營收。"
        )

        return ServiceMetrics(
            linked_customer_count=len(people),
            visit_count=visit_count,
            known_spend_twd=known,
            unknown_amount_visits=blanks,
            coverage_note=note,
        )
