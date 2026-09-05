"""Isolated, in-memory rehearsal of the designer workbench, never a POS adapter.

The server injects masked fixtures, the clock and identifiers. Changes only belong
to one browser session; agent analytics continue to use the disclosed fixed dataset.
No network, database, filesystem, credentials or production imports live here.
"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from assistant.privacy import mask_name


class WorkbenchError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Service(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=30)
    duration: int = Field(ge=15, le=600)
    price: int | None = Field(default=None, ge=0, le=100000)
    price_from: bool = False
    price_mode: Literal["flat", "length"] = "flat"
    short: int | None = Field(default=None, ge=0, le=100000)
    medium: int | None = Field(default=None, ge=0, le=100000)
    long: int | None = Field(default=None, ge=0, le=100000)


def minutes(value: str) -> int:
    try:
        hour, minute = value.split(":")
        if len(hour) != 2 or len(minute) != 2 or not hour.isdigit() or not minute.isdigit():
            raise ValueError
        h, m = int(hour), int(minute)
        if not 0 <= h <= 23 or not 0 <= m <= 59:
            raise ValueError
        return h * 60 + m
    except (ValueError, AttributeError):
        raise WorkbenchError("時間請填有效的時與分。") from None


def clock(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def _text(data: dict, key: str, message: str, *, status: int = 400, optional: bool = False) -> str:
    """讀一個字串欄位：不是字串就擋下來。

    不用 `str()` 硬轉（dict 會被收成「{○○○○○○}」這種假姓名），也不拿預設值頂替。
    這些欄位接下來會被當 dict key、set 成員或拿去 `.strip()`，
    list／dict 進去就是 TypeError → 500，客人只會看到白畫面。
    """
    value = data.get(key, "" if optional else None)
    if not isinstance(value, str):
        raise WorkbenchError(message, status)
    return value


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["daily", "weekday", "weekend"] = "daily"
    start: str
    end: str
    mode: Literal["none", "only"] = "none"
    services: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def valid_range(self):
        if minutes(self.start) >= minutes(self.end):
            raise ValueError("結束時間要晚於開始時間。")
        if self.mode == "only" and not self.services:
            raise ValueError("請至少選一個可接的項目。")
        return self


class Faq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=160)
    keywords: str = Field(default="", max_length=160)
    answer: str = Field(min_length=1, max_length=2000)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    services: list[Service] = Field(min_length=1, max_length=30)
    duration_mode: Literal["service", "fixed"] = "service"
    fixed_duration: int = Field(default=60, ge=15, le=600)
    step: Literal[15, 30, 60, 90, 120] = 30
    same_day: bool = True
    open_time: str = "11:00"
    close_time: str = "20:00"
    open_through: str
    rules: list[Rule] = Field(default_factory=list, max_length=30)
    faqs: list[Faq] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_services(self):
        if len({s.id for s in self.services}) != len(self.services):
            raise ValueError("項目代碼不能重複。")
        if len({s.name.strip() for s in self.services}) != len(self.services):
            raise ValueError("項目名稱不能重複。")
        if minutes(self.open_time) >= minutes(self.close_time):
            raise ValueError("打烊要晚於開店。")
        datetime.strptime(self.open_through, "%Y-%m-%d")
        known = {s.id for s in self.services}
        if any(set(r.services) - known for r in self.rules):
            raise ValueError("規則包含已刪除的項目。")
        return self


class BookingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    customer_ref: str
    date: str
    time: str
    services: list[str] = Field(min_length=1, max_length=30)

    @field_validator("services")
    @classmethod
    def no_duplicate(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("項目不能重複。")
        return value


DEFAULT_SERVICES = [
    ("wash", "洗髮", 30),
    ("cut", "剪髮", 60),
    ("fringe_cut", "修劉海", 30),
    ("fringe_perm", "燙劉海", 60),
    ("perm", "燙髮", 180),
    ("color", "染髮", 120),
    ("treatment", "護髮", 60),
    ("bleach", "漂髮", 300),
    ("scalp", "頭皮護理", 60),
]


class Workbench:
    def __init__(
        self,
        fixtures: dict,
        dataset: dict,
        *,
        as_of: datetime,
        designer_ref: str,
        calendar_key: str,
    ):
        self.as_of = as_of
        self.calendar_key = calendar_key
        self.serial = 0
        allowed = {
            c["customer_ref"] for c in dataset["customers"] if c["designer_ref"] == designer_ref
        }
        self.customers = [
            deepcopy(c) for c in fixtures["customers"]["rows"] if c["customer_ref"] in allowed
        ]
        self.days = deepcopy(fixtures["schedule"]["days"])
        appointments = {
            a["appointment_ref"]: a
            for a in dataset["appointments"]
            if a["designer_ref"] == designer_ref
        }
        self.bookings = []
        self.blocks = []
        for row in fixtures["bookings"]["rows"]:
            source = appointments.get(row["appointment_ref"])
            if source is None:
                continue
            date = source["starts_at"][:10]
            start = row["time_label"]
            matching = [
                slot
                for day in self.days
                if day["date"] == date
                for slot in day["slots"]
                if slot["kind"] == "booking"
                and slot["starts_at"] == start
                and slot["masked_name"] == row["masked_name"]
                and slot["service_label"] == row["service_label"]
            ]
            duration = (minutes(matching[0]["ends_at"]) - minutes(start)) if matching else 60
            self.bookings.append(
                {
                    "id": row["appointment_ref"],
                    "customer_ref": source["customer_ref"],
                    "date": date,
                    "time": start,
                    "duration": duration,
                    "services": [row["service"]],
                    "service_label": row["service_label"],
                    "masked_name": row["masked_name"],
                    "phone_last4": row["phone_last4"],
                    "status": row["status"],
                    "seeded": True,
                }
            )
        for day in self.days:
            for index, slot in enumerate(day["slots"]):
                if slot["kind"] != "booking":
                    self.blocks.append(
                        {
                            "id": f"block-{day['date']}-{index}",
                            "date": day["date"],
                            "start": slot["starts_at"],
                            "end": slot["ends_at"],
                            "reason": slot.get("reason", "不接客"),
                        }
                    )
        self.settings = Settings(
            services=[
                Service(
                    id=i,
                    name=n,
                    duration=d,
                    price_mode="length" if i in {"perm", "color", "treatment"} else "flat",
                )
                for i, n, d in DEFAULT_SERVICES
            ],
            open_through=fixtures["schedule"]["booking_open_through"],
        )
        self.takeovers: dict[str, bool] = {}
        self.messages: dict[str, list] = {}
        self.notes: dict[str, str] = {}

    def snapshot(self) -> dict:
        days = [{**d, "slots": []} for d in self.days]
        for day in days:
            day["slots"] = [
                {
                    **b,
                    "kind": "booking",
                    "start": b["time"],
                    "end": clock(minutes(b["time"]) + b["duration"]),
                }
                for b in self.bookings
                if b["date"] == day["date"] and b["status"] != "cancelled"
            ] + [{**b, "kind": "block"} for b in self.blocks if b["date"] == day["date"]]
            day["slots"].sort(key=lambda s: s["start"])
        return deepcopy(
            {
                "as_of": self.as_of.isoformat(),
                "days": days,
                "bookings": self.bookings,
                "blocks": self.blocks,
                "customers": self.customers,
                "settings": self.settings.model_dump(),
                "takeovers": self.takeovers,
                "messages": self.messages,
                "notes": self.notes,
                "calendar_url": f"/api/workbench/calendar/{self.calendar_key}.ics",
            }
        )

    def _id(self, prefix):
        self.serial += 1
        return f"demo-{prefix}-{self.serial}"

    def _booking(self, identity):
        row = next((b for b in self.bookings if b["id"] == identity), None)
        if row is None:
            raise WorkbenchError("找不到這筆預約。", 404)
        if row["status"] == "cancelled":
            raise WorkbenchError("這筆已取消，不能再送出或修改。", 409)
        return row

    def _day(self, value):
        # 非字串不能拿去比對集合成員（list／dict 不可雜湊，會變成 500）。
        if not isinstance(value, str) or value not in {d["date"] for d in self.days}:
            raise WorkbenchError("這天還沒有載入班表，請選畫面上的日期。")
        return datetime.strptime(value, "%Y-%m-%d").date()

    def _check_slot(self, date, start, end, *, exclude=None, services=()):
        chosen = self._day(date)
        if not minutes(self.settings.open_time) <= start < end <= minutes(self.settings.close_time):
            raise WorkbenchError("這個服務會超過營業時間，請換一個開始時間。", 409)
        # Booking start intervals are anchored to opening time. Blocks are not
        # appointments, so they can start between the appointment grid points.
        if services and (start - minutes(self.settings.open_time)) % self.settings.step:
            raise WorkbenchError(f"請依每 {self.settings.step} 分鐘的預約間隔選擇開始時間。", 409)
        if not self.settings.same_day and date == self.as_of.date().isoformat():
            raise WorkbenchError("目前設定不接當天預約。", 409)
        for block in self.blocks:
            if block["date"] == date and block["id"] != exclude:
                if start < minutes(block["end"]) and end > minutes(block["start"]):
                    raise WorkbenchError("這一段設定不接客，請換個時間。", 409)
        for b in self.bookings:
            if b["date"] == date and b["id"] != exclude and b["status"] != "cancelled":
                if start < minutes(b["time"]) + b["duration"] and end > minutes(b["time"]):
                    raise WorkbenchError("這個時間和現有預約重疊了，請換個時間。", 409)
        for rule in self.settings.rules:
            applies = (
                rule.scope == "daily"
                or rule.scope == "weekend"
                and chosen.weekday() >= 5
                or rule.scope == "weekday"
                and chosen.weekday() < 5
            )
            if applies and start < minutes(rule.end) and end > minutes(rule.start):
                if rule.mode == "none" or not set(services).issubset(rule.services):
                    raise WorkbenchError("這個時段的預約規則不接受所選項目。", 409)

    def act(self, kind: str, data: dict, *, calendar_key: str | None = None) -> dict:
        output: dict = {
            "simulated": True,
            "sent": False,
            "notice": "已更新本次示範，不會更動 POS、LINE 或正式帳號。",
        }
        if kind in {"book", "update_booking"}:
            parsed = BookingInput(**data)
            customer = next(
                (c for c in self.customers if c["customer_ref"] == parsed.customer_ref), None
            )
            if not customer:
                raise WorkbenchError("請從本次示範名單選一位客人。", 404)
            services = {s.id: s for s in self.settings.services}
            if set(parsed.services) - services.keys():
                raise WorkbenchError("有項目已移除，請重新選擇。")
            duration = (
                self.settings.fixed_duration
                if self.settings.duration_mode == "fixed"
                else sum(services[i].duration for i in parsed.services)
            )
            original = self._booking(parsed.id) if kind == "update_booking" else None
            start = minutes(parsed.time)
            self._check_slot(
                parsed.date,
                start,
                start + duration,
                exclude=parsed.id if original else None,
                services=parsed.services,
            )
            row = {
                "id": original["id"] if original else self._id("booking"),
                "customer_ref": parsed.customer_ref,
                "masked_name": customer["masked_name"],
                "phone_last4": customer.get("phone_last4"),
                "date": parsed.date,
                "time": parsed.time,
                "duration": duration,
                "services": parsed.services,
                "service_label": "＋".join(services[i].name for i in parsed.services),
                "status": "pending",
                "seeded": False,
            }
            if original:
                original.update(row)
            else:
                self.bookings.append(row)
            output["booking"] = deepcopy(row)
        elif kind in {"cancel_booking", "sync_booking"}:
            row = self._booking(_text(data, "id", "請指定一筆預約。"))
            row["status"] = "cancelled" if kind == "cancel_booking" else "confirmed"
        elif kind in {"block", "update_block"}:
            original = None
            if kind == "update_block":
                identity = _text(data, "id", "請指定一個不接客時段。")
                original = next((b for b in self.blocks if b["id"] == identity), None)
                if original is None:
                    raise WorkbenchError("找不到這個不接客時段。", 404)
            date = _text(data, "date", "這天還沒有載入班表，請選畫面上的日期。")
            opening = _text(data, "start", "時間請填有效的時與分。")
            closing = _text(data, "end", "時間請填有效的時與分。")
            start, end = minutes(opening), minutes(closing)
            self._check_slot(date, start, end, exclude=original["id"] if original else None)
            row = {
                "id": original["id"] if original else self._id("block"),
                "date": date,
                "start": opening,
                "end": closing,
                "reason": "不接客",
            }
            if original:
                original.update(row)
            else:
                self.blocks.append(row)
        elif kind == "remove_block":
            identity = _text(data, "id", "請指定一個不接客時段。")
            if not any(b["id"] == identity for b in self.blocks):
                raise WorkbenchError("找不到這個不接客時段。", 404)
            self.blocks = [b for b in self.blocks if b["id"] != identity]
        elif kind == "settings":
            self.settings = Settings(**data)
        elif kind == "customer":
            wrong = "請填示範姓名與電話末四碼；不要輸入完整個資。"
            name = _text(data, "name", wrong, optional=True).strip()
            last4 = _text(data, "phone_last4", wrong, optional=True)
            if (
                not 1 <= len(name) <= 30
                or len(last4) != 4
                or not last4.isascii()
                or not last4.isdigit()
            ):
                raise WorkbenchError("請填示範姓名與電話末四碼；不要輸入完整個資。")
            row = {
                "customer_ref": self._id("customer"),
                "masked_name": mask_name(name),
                "phone_last4": last4,
                "visit_count": 0,
                "known_spend_twd": 0,
                "unknown_amount_visits": 0,
                # 沒來過的客人沒有「上次服務」。空的服務 ≠ 剪髮：這裡填任何項目
                # 都是替他編一次到店紀錄，之後排預約還會被當成預設值帶出去。
                "last_service": None,
                "last_service_label": "尚無紀錄",
                "last_visit_label": "未到店",
                "days_since_last_visit": None,
            }
            self.customers.append(row)
            output["customer"] = row
        elif kind in {"takeover", "message"}:
            # The server verifies ownership with the existing provider before this call.
            ref = _text(data, "conversation_ref", "請先選一段對話。")
            if not ref:
                raise WorkbenchError("請先選一段對話。")
            if kind == "takeover":
                if not isinstance(data.get("enabled"), bool):
                    raise WorkbenchError("請選擇接手或交回助理。")
                self.takeovers[ref] = data["enabled"]
            else:
                message = "請填 1 到 2000 字的示範訊息。"
                text = _text(data, "text", message, optional=True).strip()
                if not text or len(text) > 2000:
                    raise WorkbenchError(message)
                self.messages.setdefault(ref, []).append(
                    {"role": "designer", "redacted_content": text, "simulated": True}
                )
        elif kind == "note":
            ref = _text(data, "customer_ref", "請從本次示範名單選一位客人。")
            if ref not in {c["customer_ref"] for c in self.customers}:
                raise WorkbenchError("找不到這位客人。", 404)
            text = _text(data, "text", "釘選內容請填文字。", optional=True).strip()
            if len(text) > 2000:
                raise WorkbenchError("釘選內容最多 2000 字。")
            self.notes[ref] = text
        elif kind == "rotate_calendar":
            if not calendar_key:
                raise WorkbenchError("目前無法更換連結，請重試。")
            self.calendar_key = calendar_key
        else:
            raise WorkbenchError("這個操作還沒接上，本次沒有更動任何資料。")
        return output

    def calendar(self) -> str:
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Got You//Demo//ZH-TW",
            "X-WR-CALNAME:got you 示範班表（非正式預約）",
        ]
        for b in self.bookings:
            if b["status"] == "cancelled":
                continue
            start = datetime.fromisoformat(f"{b['date']}T{b['time']}:00+08:00")
            end = start + timedelta(minutes=b["duration"])
            lines += [
                "BEGIN:VEVENT",
                f"UID:{b['id']}@demo",
                f"DTSTAMP:{self.as_of.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}",
                f"DTSTART;TZID=Asia/Taipei:{start.strftime('%Y%m%dT%H%M%S')}",
                f"DTEND;TZID=Asia/Taipei:{end.strftime('%Y%m%dT%H%M%S')}",
                "SUMMARY:示範預約（不會寫入公司系統）",
                "END:VEVENT",
            ]
        return "\r\n".join([*lines, "END:VCALENDAR", ""])
