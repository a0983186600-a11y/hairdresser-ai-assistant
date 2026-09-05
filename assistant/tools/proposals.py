"""提案工具：模型講一句話，這一層把它翻成**一筆結構化的動作**——然後停手。

## 為什麼是兩個工具，不是一條寫入路

聊天那頭永遠不寫。這兩個工具跟另外九個一樣是**只讀**的：它們回一張「打算做什麼」
的單子（`action`），寫入只發生在設計師按下確認卡之後，由前端打**既有**的
`POST /api/workbench/actions`。所以整條路上只有一個寫入端點，它原本的 CSRF、
同源檢查與「正式唯讀 403」照樣守著這件新功能，不必再守第二次。

    模型講 → 程式驗 → 人按同意 → 才動

## 拆不出來的欄位一律進 `missing`，不補預設值

「空的服務 ≠ 剪髮」是這個專案用事故換來的規矩（Tai 案 2026-07-25）。所以：

- 客人找不到、找到多位 → `missing` 含 `customer`，`note` 說明要怎麼補（給末四碼）。
- 時間看不懂、只有時間沒有日期 → `missing` 含 `start`。**不准當作今天。**
- 服務不在項目表上 → `missing` 含 `service`，工時也跟著留白（不准補 60 分）。

`missing` 非空時 `action` 是 `None`——卡片上就沒有那顆「確認排入」可以按。

## `missing` 與 `unresolved` 是兩件事

`missing` 是**寫入需要、但拆不出來**的欄位；`unresolved` 是**拆不出來、但寫入
根本不需要**的欄位。示範排單只寫「誰、哪天、幾點、什麼項目」，價格不在 payload
裡（`BookingInput` 是 `extra="forbid"`），所以「這個項目還沒設定價格」要照實講，
但不該把一筆完整的預約擋在門外。分不清這兩件事，卡片就會一邊寫「還缺標價」
一邊亮著確認鍵，或者反過來——永遠按不下去。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from assistant.adapters.provider import SalonDataProvider
from assistant.adapters.schemas import DesignerScope
from assistant.privacy import mask_name, phone_last4
from assistant.workbench import Service, service_catalog

__all__ = [
    "PROPOSAL_TOOL_NAMES",
    "PROPOSAL_TOOL_DESCRIPTIONS",
    "PROPOSAL_INPUTS",
    "PROPOSAL_FIELD_DESCRIPTIONS",
    "BOOKING_TOOL_NAME",
    "SERVICE_PRICE_TOOL_NAME",
    "build_proposal",
    "parse_moment",
]

BOOKING_TOOL_NAME = "propose_booking"
SERVICE_PRICE_TOOL_NAME = "propose_service_price"

PROPOSAL_TOOL_NAMES: tuple[str, ...] = (BOOKING_TOOL_NAME, SERVICE_PRICE_TOOL_NAME)

#: 客人名單一次掃幾位。provider 那一層自己的上限就是 100（`SegmentSearchInput`），
#: 所以這裡不是「我們想掃多少」而是「一次最多拿得到多少」。
_LOOKUP_LIMIT = 100


class ProposeBookingInput(BaseModel):
    """排一筆預約的提案。五格都可以是空的——空的就會變成卡片上的「還缺」。"""

    model_config = ConfigDict(extra="forbid")

    customer: str | None = None
    start: str | None = None
    service: str | None = None
    price_twd: int | None = Field(default=None, ge=0, le=100000)
    duration_minutes: int | None = Field(default=None, ge=15, le=600)


class ProposeServicePriceInput(BaseModel):
    """改一個項目的價格或工時。只改給了的那一格，沒給的維持原樣。"""

    model_config = ConfigDict(extra="forbid")

    service: str
    duration_minutes: int | None = Field(default=None, ge=15, le=600)
    price_twd: int | None = Field(default=None, ge=0, le=100000)


PROPOSAL_INPUTS: dict[str, type[BaseModel]] = {
    BOOKING_TOOL_NAME: ProposeBookingInput,
    SERVICE_PRICE_TOOL_NAME: ProposeServicePriceInput,
}

PROPOSAL_TOOL_DESCRIPTIONS = {
    BOOKING_TOOL_NAME: (
        "把設計師講的「幫我排一筆」整理成一張待確認的排單卡。"
        "**這個工具不會排任何東西**：它只回一份整理好的欄位，設計師在卡片上按了確認才寫入。"
        "拆不出來的欄位會回在 missing，照著問回去就好，不要自己填一個看起來合理的值。"
    ),
    SERVICE_PRICE_TOOL_NAME: (
        "把設計師講的「這個項目改成多少錢／做多久」整理成一張待確認的設定卡。"
        "**這個工具不會改任何設定**：設計師在卡片上按了確認才寫入。"
        "只會改你有給的那一格，沒給的維持原樣。"
    ),
}

PROPOSAL_FIELD_DESCRIPTIONS = {
    "customer": "客人是誰：設計師講的姓名（會是遮罩過的樣子，例如「王○明」）或電話末四碼。",
    "start": "什麼時候：照設計師的原話寫，像「明天下午三點」「9/6 15:00」「2026-09-06T15:00」。",
    "service": "做什麼項目：項目名稱或代碼（例如「剪髮」或 cut）。",
    "price_twd": "設計師講的金額（新台幣整數）。他沒講就不要填。",
    "duration_minutes": "設計師講的工時（分鐘）。他沒講就不要填，工時會從項目表查。",
}


# --- 時間：看得懂就給，看不懂就留白 ---------------------------------------------

#: 中文數字的鐘點。只到十二——這是鐘面，不是數學。
_CJK_NUMBERS = {
    "零": 0, "〇": 0, "一": 1, "兩": 2, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
}

#: 相對日期。寫死這幾個是刻意的：「下週三」這種要先講清楚是哪一個星期三，
#: 猜錯的代價是客人白跑一趟，所以看不懂就回頭問。
_RELATIVE_DAYS = {
    "今天": 0, "今日": 0, "本日": 0,
    "明天": 1, "明日": 1,
    "後天": 2, "后天": 2,
    "大後天": 3, "大后天": 3,
}

_MERIDIEM_AM = ("凌晨", "清晨", "早上", "上午", "早")
_MERIDIEM_PM = ("下午", "午後", "傍晚", "晚上", "晚間", "夜間", "夜裡", "晚")

_ISO_DATE = re.compile(r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)")
_SHORT_DATE = re.compile(r"(?<!\d)(\d{1,2})\s*(?:/|月)\s*(\d{1,2})\s*(?:日|號)?(?!\d)")
_CLOCK = re.compile(r"(?<!\d)(\d{1,2})\s*[:：]\s*(\d{2})(?!\d)")
_CJK_CLOCK = re.compile(
    r"(?<![\d一二兩三四五六七八九十])([0-9]{1,2}|十[一二]?|[一兩二三四五六七八九])"
    r"\s*[點点時时]\s*(半|[0-9]{1,2}\s*分|[一兩二三四五六七八九十]{1,3}\s*分)?"
)


def _digits(raw: str) -> int | None:
    text = raw.strip()
    if text.isdigit():
        return int(text)
    return _CJK_NUMBERS.get(text)


def _minutes_part(raw: str | None) -> int | None:
    if raw is None:
        return 0
    text = raw.strip()
    if text == "半":
        return 30
    text = text.removesuffix("分").strip()
    value = _digits(text)
    if value is None or not 0 <= value <= 59:
        return None
    return value


def _find_date(text: str, as_of: datetime) -> date | None:
    for word, offset in _RELATIVE_DAYS.items():
        if word in text:
            return (as_of + timedelta(days=offset)).date()

    iso = _ISO_DATE.search(text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None

    short = _SHORT_DATE.search(text)
    if short:
        month, day = int(short.group(1)), int(short.group(2))
        # 沒寫年份才可以推年，而且只往前推：講 1/5 的那天多半不是去年的 1/5。
        for year in (as_of.year, as_of.year + 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                return None
            if candidate >= as_of.date():
                return candidate
        return None
    return None


def _find_time(text: str) -> tuple[int, int] | None:
    clock = _CLOCK.search(text)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        head = text[: clock.start()]
    else:
        cjk = _CJK_CLOCK.search(text)
        if not cjk:
            return None
        raw_hour = _digits(cjk.group(1))
        minute = _minutes_part(cjk.group(2))
        if raw_hour is None or minute is None:
            return None
        hour, head = raw_hour, text[: cjk.start()]

    if not 0 <= hour <= 24 or not 0 <= minute <= 59:
        return None

    if "中午" in head and hour == 12:
        pass
    elif any(word in head for word in _MERIDIEM_PM) and hour < 12:
        hour += 12
    elif any(word in head for word in _MERIDIEM_AM) and hour == 12:
        hour = 0

    if hour == 24 and minute == 0:
        hour = 0
    if not 0 <= hour <= 23:
        return None
    return hour, minute


def parse_moment(raw: str | None, as_of: datetime) -> tuple[str | None, str | None]:
    """把「明天下午三點」這種話解析成 (YYYY-MM-DD, HH:MM)。

    解析不出**日期**或**時間**其中任何一半，那一半就回 `None`——呼叫端會把它
    變成卡片上的「還缺」，不會自己補一個今天或一個整點。
    """
    if not raw or not raw.strip():
        return None, None
    text = unicodedata.normalize("NFKC", raw).strip()

    # 整串就是一個 ISO 時間戳時，交給標準函式庫，不要用底下的regex 再猜一次。
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        pass
    else:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return stamp.date().isoformat(), None
        return stamp.date().isoformat(), f"{stamp.hour:02d}:{stamp.minute:02d}"

    found_date = _find_date(text, as_of)
    found_time = _find_time(text)
    return (
        found_date.isoformat() if found_date else None,
        f"{found_time[0]:02d}:{found_time[1]:02d}" if found_time else None,
    )


# --- 項目表：工時與價格從這裡查，查不到就留白 -----------------------------------


def _find_service(raw: str | None) -> Service | None:
    if not raw or not raw.strip():
        return None
    wanted = unicodedata.normalize("NFKC", raw).strip().casefold()
    catalog = service_catalog()
    for item in catalog:
        if wanted in {item.id.casefold(), item.name.casefold()}:
            return item
    # 「幫我排剪髮」這種夾在句子裡的講法也認，但只認唯一命中的那一個。
    hits = [item for item in catalog if item.name and item.name.casefold() in wanted]
    return hits[0] if len(hits) == 1 else None


def _catalog_price(item: Service) -> int | None:
    """項目表上的價格。長度分級的項目沒有單一價格，那就是沒有——不准挑一個。"""
    return item.price if item.price_mode == "flat" else None


def _service_names() -> list[str]:
    return [item.name for item in service_catalog()]


# --- 客人：找得到唯一一位才算找到 -----------------------------------------------


def _scope_customers(
    provider: SalonDataProvider, scope: DesignerScope, as_of: datetime
) -> list[Any]:
    """這位設計師名下的客人。

    掃兩次是因為 provider 的單次上限是 100 筆，而兩種排序（來得最勤／最久沒來）
    的前 100 名合起來才蓋得住示範名單。找不到人時 `note` 會照實說掃了多少位，
    不會讓「沒掃到」看起來像「沒有這個人」。
    """
    found: dict[str, Any] = {}
    for extra in ({}, {"inactive_days_gte": 1}):
        rows = provider.search_customer_segment(
            scope, as_of=as_of, limit=_LOOKUP_LIMIT, **extra
        )
        for row in rows:
            found.setdefault(row.customer_ref, row)
    return list(found.values())


def _name_matches(query: str, full_name: str) -> bool:
    """姓名比對。模型看到的一律是遮罩後的樣子，所以 `○` 要當萬用字元。"""
    if query == full_name:
        return True
    masked = mask_name(full_name)
    if query == masked:
        return True
    if len(query) != len(masked):
        return False
    return all(q == "○" or q == m for q, m in zip(query, masked, strict=True))


def _customer_candidates(query: str, rows: list[Any]) -> list[Any]:
    text = unicodedata.normalize("NFKC", query).strip()
    last4 = re.search(r"(?<!\d)(\d{4})(?!\d)", text)
    name = text[: last4.start()] + text[last4.end():] if last4 else text
    name = name.strip(" ·、,，-—")

    hits = list(rows)
    if last4:
        wanted = last4.group(1)
        hits = [row for row in hits if phone_last4(row.phone) == wanted]
    if name:
        hits = [row for row in hits if _name_matches(name, row.full_name)]
    elif not last4:
        return []
    return hits


def _customer_label(row: Any) -> str:
    tail = phone_last4(row.phone)
    return f"{mask_name(row.full_name)}（末四碼 {tail}）" if tail else mask_name(row.full_name)


# --- 組單 -----------------------------------------------------------------------


def _proposal_id(kind: str, fields: dict[str, Any]) -> str:
    """同樣的欄位得到同樣的編號：這個 id 是給前端配對用的，不是流水號。"""
    digest = hashlib.sha256(
        json.dumps(fields, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"{kind}-{digest[:12]}"


def _sentence(parts: list[str | None]) -> str:
    return " ".join(part for part in parts if part)


def _booking_proposal(
    arguments: ProposeBookingInput,
    provider: SalonDataProvider,
    scope: DesignerScope,
    as_of: datetime,
) -> dict[str, Any]:
    notes: list[str] = []
    missing: list[str] = []
    unresolved: list[str] = []

    customer_ref: str | None = None
    customer_label: str | None = None
    if not arguments.customer or not arguments.customer.strip():
        missing.append("customer")
        notes.append("還不知道要排給誰，請給姓名或電話末四碼。")
    else:
        rows = _scope_customers(provider, scope, as_of)
        hits = _customer_candidates(arguments.customer, rows)
        if len(hits) == 1:
            customer_ref = hits[0].customer_ref
            customer_label = _customer_label(hits[0])
        elif not hits:
            missing.append("customer")
            notes.append(
                f"在你名下的 {len(rows)} 位客人裡找不到「{arguments.customer}」，"
                "請換個寫法或直接給電話末四碼。"
            )
        else:
            shown = "、".join(_customer_label(row) for row in hits[:5])
            missing.append("customer")
            notes.append(
                f"有 {len(hits)} 位對得上「{arguments.customer}」（{shown}），"
                "請直接給電話末四碼。"
            )

    day, clock = parse_moment(arguments.start, as_of)
    if day is None or clock is None:
        missing.append("start")
        if arguments.start and clock and day is None:
            notes.append(f"「{arguments.start}」只講了時間，還缺日期（例如「明天 {clock}」）。")
        elif arguments.start and day and clock is None:
            notes.append(f"「{arguments.start}」只講了日期，還缺幾點（例如「{day} 15:00」）。")
        elif arguments.start:
            notes.append(f"看不懂「{arguments.start}」是哪一天幾點，請給日期與時間。")
        else:
            notes.append("還不知道要排哪一天幾點，請給日期與時間。")

    item = _find_service(arguments.service)
    if item is None:
        missing.append("service")
        notes.append(
            (
                f"「{arguments.service}」不在項目表上。"
                if arguments.service
                else "還不知道要做什麼項目。"
            )
            + "目前有這些項目："
            + "、".join(_service_names())
            + "。"
        )

    duration = arguments.duration_minutes or (item.duration if item else None)
    price = arguments.price_twd if arguments.price_twd is not None else (
        _catalog_price(item) if item else None
    )
    if item is not None and price is None:
        unresolved.append("price_twd")
        notes.append(f"項目表上「{item.name}」還沒有填價格，這張卡不會替它填一個。")

    fields = {
        "customer_ref": customer_ref,
        "customer_label": customer_label,
        "date": day,
        "time": clock,
        "service_id": item.id if item else None,
        "service_label": item.name if item else None,
        "duration_minutes": duration,
        "price_twd": price,
    }

    action = None
    if not missing:
        action = {
            "kind": "book",
            "data": {
                "customer_ref": customer_ref,
                "date": day,
                "time": clock,
                "services": [item.id],  # type: ignore[union-attr]
            },
        }

    summary = _sentence(
        [
            f"{day[5:].replace('-', '/')} {clock}" if day and clock else None,
            customer_label.split("（")[0] if customer_label else None,
            item.name if item else None,
            f"{duration} 分" if duration else None,
            f"NT${price}" if price is not None else None,
        ]
    )
    if missing:
        summary = _sentence([summary, f"（還缺 {'、'.join(_MISSING_LABELS[m] for m in missing)}）"])

    return {
        "proposal_id": _proposal_id("book", fields),
        "kind": "book",
        "fields": fields,
        "missing": missing,
        "unresolved": unresolved,
        "action": action,
        "summary": summary or "（還沒有東西可以排）",
        "note": " ".join(notes) or None,
    }


def _service_price_proposal(arguments: ProposeServicePriceInput) -> dict[str, Any]:
    notes: list[str] = []
    missing: list[str] = []

    item = _find_service(arguments.service)
    if item is None:
        missing.append("service")
        notes.append(
            f"「{arguments.service}」不在項目表上。目前有這些項目："
            + "、".join(_service_names())
            + "。"
        )
    if arguments.duration_minutes is None and arguments.price_twd is None:
        missing.extend(["price_twd", "duration_minutes"])
        notes.append("要改成多少錢、或做多久？兩個至少講一個。")

    fields = {
        "service_id": item.id if item else None,
        "service_label": item.name if item else None,
        "duration_minutes": arguments.duration_minutes,
        "price_twd": arguments.price_twd,
    }

    action = None
    if not missing and item is not None:
        change: dict[str, Any] = {"id": item.id}
        if arguments.duration_minutes is not None:
            change["duration"] = arguments.duration_minutes
        if arguments.price_twd is not None:
            change["price"] = arguments.price_twd
        # `settings` 那個端點收的是**整份設定**，所以這裡只能給「要改哪一格」，
        # 由畫面上那份現行設定合併之後再送出。把伺服器這邊的預設值當成現行設定
        # 湊一份完整 payload 出來，會把設計師剛剛改的其他設定一起洗掉。
        action = {"kind": "settings", "merge": "service", "data": {"service": change}}

    summary = _sentence(
        [
            item.name if item else arguments.service,
            f"工時 {arguments.duration_minutes} 分" if arguments.duration_minutes else None,
            f"NT${arguments.price_twd}" if arguments.price_twd is not None else None,
        ]
    )
    if missing:
        summary = _sentence(
            [summary, f"（還缺 {'、'.join(_MISSING_LABELS[m] for m in dict.fromkeys(missing))}）"]
        )

    return {
        "proposal_id": _proposal_id("settings", fields),
        "kind": "settings",
        "fields": fields,
        "missing": missing,
        "unresolved": [],
        "action": action,
        "summary": summary,
        "note": " ".join(notes) or None,
    }


#: `missing` 裡的欄位名要給人看時換成這幾個字（卡片與 summary 共用一份）。
_MISSING_LABELS = {
    "customer": "客人",
    "start": "日期與時間",
    "service": "項目",
    "price_twd": "價格",
    "duration_minutes": "工時",
}


def build_proposal(
    name: str,
    arguments: BaseModel,
    *,
    provider: SalonDataProvider,
    scope: DesignerScope,
    as_of: datetime,
) -> dict[str, Any]:
    """跑一個提案工具，回「打算做什麼」那張單子。這裡不寫任何狀態。"""
    if name == BOOKING_TOOL_NAME:
        assert isinstance(arguments, ProposeBookingInput)
        return _booking_proposal(arguments, provider, scope, as_of)
    assert isinstance(arguments, ProposeServicePriceInput)
    return _service_price_proposal(arguments)
