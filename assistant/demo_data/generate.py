"""固定 seed 的假資料產生器——公開 repo 預設就是靠它跑起來。

## 為什麼「錨點時間」是寫死的

`ANCHOR` 釘在 2026-09-01T00:00+08:00，資料集因此是**固定的**：同一個 seed 永遠
產出逐 byte 相同的五個 JSON。這是刻意的，不是偷懶——

- README 的截圖、考卷（`docs/agent-bakeoff/exam.md`）算出來的答案、REPLAY 逐字稿
  三邊要對得起來。資料每天飄就沒有一個能對。
- 評審 clone 下來跑，看到的必須跟影片裡一模一樣。
- 隨機抽樣的示範資料等於「每次跑答案都不同」，那就沒辦法用測試釘住行為。

代價是所有查詢都要**明講 `as_of`**（通常就是 ANCHOR）。整個 `assistant/` 底下
沒有一行 `datetime.now()`，這是同一個決定的另一半。

## 人名與電話全是假的

姓名由這個檔案裡自己寫的姓氏表與名字音節表組合而成，**沒有抄任何真實客人名單**；
電話是 09 開頭的十碼假號碼。任何與真人的雷同純屬組合上的巧合。

## 只用 `random()` 取亂數

不用 `choices`／`sample`／`shuffle`——那些的內部實作在不同 Python 版本之間換過。
全部從 `rng.random()` 導出來，`generate(seed=42)` 才在任何機器上都給同一份檔案。
識別碼更進一步不吃亂數狀態：用 uuid5 從 `(kind, seed, key)` 算出來。
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

__all__ = [
    "ANCHOR",
    "DEFAULT_SEED",
    "DATA_DIR",
    "FILENAMES",
    "SERVICE_FAMILIES",
    "generate",
    "load_dataset",
]

TAIPEI = ZoneInfo("Asia/Taipei")

#: 示範資料集的「現在」。見模組說明：固定是刻意的。
ANCHOR = datetime(2026, 9, 1, 0, 0, tzinfo=TAIPEI)

DEFAULT_SEED = 42
DATA_DIR = Path(__file__).resolve().parent
FILENAMES = (
    "designers.json",
    "customers.json",
    "visits.json",
    "appointments.json",
    "conversations.json",
)

SERVICE_FAMILIES = ("cut", "perm", "color", "treatment", "bleach", "scalp")

#: 服務家族的價格帶（新台幣）。跟 `assistant/config/defaults.yaml` 的中文名同一組 key。
PRICE_BANDS: dict[str, tuple[int, int]] = {
    "cut": (800, 1500),
    "perm": (2500, 6000),
    "color": (2000, 5500),
    "treatment": (1200, 3000),
    "bleach": (3000, 7000),
    "scalp": (1000, 2000),
}

#: 服務出現比例：剪最多，漂最少——跟一般沙龍的形狀對得上。
_SERVICE_MIX: tuple[tuple[str, float], ...] = (
    ("cut", 0.34),
    ("color", 0.24),
    ("treatment", 0.16),
    ("perm", 0.12),
    ("scalp", 0.09),
    ("bleach", 0.05),
)

#: 高單價客人偏好的服務（VIP 用這組抽，金額才拉得起來）。
_PREMIUM_MIX: tuple[tuple[str, float], ...] = (
    ("color", 0.32),
    ("bleach", 0.22),
    ("perm", 0.20),
    ("treatment", 0.16),
    ("cut", 0.06),
    ("scalp", 0.04),
)

# --- 假名字表（自己寫的，沒有抄任何名單） -------------------------------------

_SURNAMES = (
    "林", "陳", "黃", "張", "李", "王", "吳", "劉", "蔡", "楊",
    "許", "鄭", "謝", "郭", "洪", "曾", "廖", "賴", "徐", "周",
    "葉", "蘇", "莊", "呂", "江", "何", "蕭", "羅", "高", "潘",
)

_GIVEN_SYLLABLES = (
    "宸", "昀", "柏", "晴", "筱", "彥", "舒", "翊", "靖", "恩",
    "苡", "家", "宥", "品", "丞", "雅", "湘", "軒", "妤", "凱",
    "宜", "承", "祐", "薇", "皓", "淇", "睿", "蓁", "禹", "喬",
    "澤", "亭", "宏", "禾", "澐", "岑", "頎", "瑄", "琁", "沛",
)

_DESIGNER_STORES = ("示範分店・松江", "示範分店・民生", "示範分店・古亭")

# --- 假對話（全部是編的，沒有一句來自真實逐字稿） -----------------------------

_USER_OPENERS = (
    "設計師好，想預約染髮，這週有時段嗎？",
    "哈囉～請問這禮拜六下午還有位子嗎？",
    "上次那個護髮想再做一次，最近可以約嗎？",
    "想剪短一點，順便看看要不要換顏色，何時方便？",
    "請問燙髮大概要多久？想找一天下午過去。",
    "頭皮最近有點癢，想約頭皮護理。",
    "想預約，時間比較彈性，看你哪天方便。",
    "上次剪完長長了，想再修一次。",
)

_USER_FOLLOW_UPS = (
    "那我下午三點可以嗎？",
    "好，那就約那天，謝謝！",
    "價位大概多少？想先抓一下預算。",
    "我可能會晚十分鐘到，沒問題吧？",
    "那先幫我留著，我再確認一下班表。",
    "想順便加護髮，一起做時間會拉很長嗎？",
    "了解，那我再想想，週末前回覆你。",
    "麻煩了，謝謝～",
)

_ASSISTANT_REPLIES = (
    "好的，我看一下時段。方便先跟我說您想約哪一天嗎？",
    "有的，那天下午還有兩個時段，您比較想早一點還是晚一點？",
    "了解，這個服務大概要兩個半小時，我先幫您抓這個長度。",
    "沒問題，已經幫您記下來了，等設計師確認後再回覆您。",
    "價格會依髮長與狀況調整，這邊先不報死，到店評估後跟您說明。",
    "好的，那我先把時段留著，晚點跟您確認。",
    "收到，我把需求記下來了：時間您再回覆我一下就可以。",
)

_DESIGNER_NOTES = (
    "我接手一下，這位客人上次的顏色要對一下紀錄。",
    "這個時段我剛好有空，直接跟客人講。",
    "先不要報價，等我看過髮況再說。",
    "幫我把她排在下午第一個，比較不會等。",
)


def _weighted(rng: random.Random, items: tuple[tuple[str, float], ...]) -> str:
    total = sum(weight for _, weight in items)
    target = rng.random() * total
    running = 0.0
    for value, weight in items:
        running += weight
        if target < running:
            return value
    return items[-1][0]


def _pick(rng: random.Random, seq: tuple[str, ...]) -> str:
    return seq[int(rng.random() * len(seq))]


def _int_between(rng: random.Random, low: int, high: int) -> int:
    """含頭含尾的整數亂數，只用 `random()` 導出來。"""
    return low + int(rng.random() * (high - low + 1))


_DEMO_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "demo-salon.invalid")


def _ref(kind: str, seed: int, key: object) -> str:
    """識別碼不吃亂數狀態：同一個 (kind, seed, key) 永遠是同一個 uuid。"""
    return str(uuid.uuid5(_DEMO_NAMESPACE, f"{kind}:{seed}:{key}"))


def _fake_name(rng: random.Random, used: set[str]) -> str:
    for _ in range(200):
        length = 1 if rng.random() < 0.18 else 2
        name = _pick(rng, _SURNAMES) + "".join(
            _pick(rng, _GIVEN_SYLLABLES) for _ in range(length)
        )
        if name not in used:
            used.add(name)
            return name
    # 名字表撞完了（真的走到這裡代表表太小），補一個一定不重複的。
    fallback = _pick(rng, _SURNAMES) + _pick(rng, _GIVEN_SYLLABLES) + str(len(used))
    used.add(fallback)
    return fallback


def _fake_phone(rng: random.Random, used: set[str]) -> str:
    while True:
        phone = "09" + "".join(str(_int_between(rng, 0, 9)) for _ in range(8))
        if phone not in used:
            used.add(phone)
            return phone


def _visit_moment(rng: random.Random, days_before_anchor: int) -> datetime:
    """把到店時間放在營業時間內（10:00–19:30），半小時為單位。"""
    hour = _int_between(rng, 10, 19)
    minute = 0 if rng.random() < 0.5 else 30
    return (ANCHOR - timedelta(days=days_before_anchor)).replace(hour=hour, minute=minute)


def _price(rng: random.Random, family: str) -> int | None:
    if rng.random() < 0.08:
        # 約 8% 的到店沒有金額：POS 沒帶回來、或當場沒登記。
        # 這是真的會發生的事，工具必須把它跟「已知金額」分開報。
        return None
    low, high = PRICE_BANDS[family]
    return _int_between(rng, low // 50, high // 50) * 50


#: 客人分佈：常客、一般、開始拖、幾乎不回、新客。
#: (名稱, 佔比, 到店次數範圍, 回訪間隔範圍, 距今最後一次到店的天數範圍, 是否高單價)
_Profile = tuple[str, float, tuple[int, int], tuple[int, int], tuple[int, int], bool]

_PROFILES: tuple[_Profile, ...] = (
    ("vip", 0.08, (9, 16), (28, 45), (3, 35), True),
    ("regular", 0.40, (3, 9), (30, 70), (5, 45), False),
    ("slipping", 0.24, (2, 7), (45, 90), (46, 119), False),
    ("dormant", 0.18, (2, 5), (60, 110), (120, 400), False),
    ("newcomer", 0.10, (1, 1), (40, 80), (3, 60), False),
)


def _pick_profile(rng: random.Random) -> tuple[str, tuple[int, int], tuple[int, int], tuple[int, int], bool]:  # noqa: E501
    target = rng.random()
    running = 0.0
    for name, share, visits, interval, recency, premium in _PROFILES:
        running += share
        if target < running:
            return name, visits, interval, recency, premium
    name, _, visits, interval, recency, premium = _PROFILES[-1]
    return name, visits, interval, recency, premium


def _build(seed: int) -> dict[str, list[dict]]:
    rng = random.Random(seed)
    used_names: set[str] = set()
    used_phones: set[str] = set()

    designers = []
    for index in range(3):
        designers.append(
            {
                "designer_ref": _ref("designer", seed, index),
                "display_name": _fake_name(rng, used_names),
                "store_name": _DESIGNER_STORES[index],
                "joined_at": (ANCHOR - timedelta(days=_int_between(rng, 400, 1400)))
                .replace(hour=10, minute=0)
                .isoformat(),
            }
        )

    # 三位設計師的客量不一樣（新人手上人少），比較像真的。
    per_designer = (120, 105, 75)

    customers: list[dict] = []
    visits: list[dict] = []
    profiles: dict[str, str] = {}
    last_seen: dict[str, datetime] = {}

    customer_index = 0
    for designer, count in zip(designers, per_designer, strict=True):
        for _ in range(count):
            customer_ref = _ref("customer", seed, customer_index)
            profile, visit_range, interval_range, recency_range, premium = _pick_profile(rng)
            visit_count = _int_between(rng, *visit_range)
            days_since_last = _int_between(rng, *recency_range)

            moments: list[datetime] = []
            cursor = days_since_last
            for _ in range(visit_count):
                if cursor > 400:
                    break
                moments.append(_visit_moment(rng, cursor))
                cursor += _int_between(rng, *interval_range)

            first_visit = min(moments) if moments else ANCHOR - timedelta(days=days_since_last)
            customers.append(
                {
                    "customer_ref": customer_ref,
                    "designer_ref": designer["designer_ref"],
                    "full_name": _fake_name(rng, used_names),
                    "phone": _fake_phone(rng, used_phones),
                    "created_at": (first_visit - timedelta(days=_int_between(rng, 0, 20)))
                    .replace(hour=12, minute=0)
                    .isoformat(),
                    "line_user_ref": f"demo-line-user-{customer_index:04d}",
                }
            )
            profiles[customer_ref] = profile
            if moments:
                last_seen[customer_ref] = max(moments)

            mix = _PREMIUM_MIX if premium else _SERVICE_MIX
            for visit_no, moment in enumerate(sorted(moments)):
                family = _weighted(rng, mix)
                visits.append(
                    {
                        "visit_ref": _ref("visit", seed, f"{customer_index}:{visit_no}"),
                        "customer_ref": customer_ref,
                        "designer_ref": designer["designer_ref"],
                        "visited_at": moment.isoformat(),
                        "service_family": family,
                        "amount_twd": _price(rng, family),
                    }
                )
            customer_index += 1

    visits.sort(key=lambda row: (row["visited_at"], row["visit_ref"]))

    # --- 未來的預約：挑最近還有回來的人 --------------------------------------
    recent_refs = sorted(
        ref
        for ref, moment in last_seen.items()
        if (ANCHOR - moment).days <= 60
    )
    appointments = []
    for slot in range(28):
        if not recent_refs:
            break
        chosen = recent_refs[int(rng.random() * len(recent_refs))]
        owner = next(c for c in customers if c["customer_ref"] == chosen)
        starts_at = (ANCHOR + timedelta(days=_int_between(rng, 1, 21))).replace(
            hour=_int_between(rng, 10, 18),
            minute=0 if rng.random() < 0.5 else 30,
        )
        appointments.append(
            {
                "appointment_ref": _ref("appointment", seed, slot),
                "customer_ref": chosen,
                "designer_ref": owner["designer_ref"],
                "starts_at": starts_at.isoformat(),
                "service_family": _weighted(rng, _SERVICE_MIX),
                "status": "confirmed" if rng.random() < 0.8 else "pending",
            }
        )
    appointments.sort(key=lambda row: (row["starts_at"], row["appointment_ref"]))

    # --- 對話：每位設計師幾十段 ----------------------------------------------
    by_designer: dict[str, list[str]] = {}
    for customer in customers:
        by_designer.setdefault(customer["designer_ref"], []).append(customer["customer_ref"])

    conversations = []
    conversation_index = 0
    for designer, wanted in zip(designers, (34, 28, 22), strict=True):
        pool = by_designer[designer["designer_ref"]]
        picked: list[str] = []
        seen: set[str] = set()
        while len(picked) < wanted and len(seen) < len(pool):
            candidate = pool[int(rng.random() * len(pool))]
            if candidate in seen:
                continue
            seen.add(candidate)
            picked.append(candidate)

        for customer_ref in picked:
            # 從 ANCHOR 往回減，不是先減天數再 replace 鐘點——後者在 days_ago=0
            # 那一格會把時間推到 ANCHOR 之後，整段對話就跑到「未來」去了。
            days_ago = _int_between(rng, 1, 120)
            start = ANCHOR - timedelta(
                days=days_ago,
                hours=_int_between(rng, 0, 12),
                minutes=_int_between(rng, 0, 59),
            )
            turns = _int_between(rng, 3, 9)
            messages = []
            cursor = start
            for turn in range(turns):
                if turn == 0:
                    role, content = "user", _pick(rng, _USER_OPENERS)
                elif turn % 2 == 1:
                    role, content = "assistant", _pick(rng, _ASSISTANT_REPLIES)
                elif rng.random() < 0.15:
                    role, content = "designer", _pick(rng, _DESIGNER_NOTES)
                else:
                    role, content = "user", _pick(rng, _USER_FOLLOW_UPS)
                messages.append(
                    {
                        "role": role,
                        "created_at": cursor.isoformat(),
                        "content": content,
                    }
                )
                cursor = cursor + timedelta(minutes=_int_between(rng, 1, 90))

            roll = rng.random()
            if roll < 0.12:
                state = "human_takeover"
            elif roll < 0.52:
                state = "closed"
            else:
                state = "active"

            draft: dict[str, object] = {"service_family": _weighted(rng, _SERVICE_MIX)}
            if rng.random() < 0.7:
                draft["preferred_date"] = (
                    start + timedelta(days=_int_between(rng, 1, 14))
                ).date().isoformat()
            if rng.random() < 0.5:
                draft["preferred_time"] = f"{_int_between(rng, 10, 18):02d}:00"

            conversations.append(
                {
                    "conversation_ref": _ref("conversation", seed, conversation_index),
                    "customer_ref": customer_ref,
                    "designer_ref": designer["designer_ref"],
                    "state": state,
                    "identity_ambiguity": rng.random() < 0.08,
                    "safe_draft_fields": draft,
                    "updated_at": messages[-1]["created_at"],
                    "messages": messages,
                }
            )
            conversation_index += 1

    conversations.sort(key=lambda row: (row["updated_at"], row["conversation_ref"]))

    return {
        "designers": designers,
        "customers": customers,
        "visits": visits,
        "appointments": appointments,
        "conversations": conversations,
    }


def _dump(path: Path, payload: list[dict]) -> None:
    # sort_keys=True：欄位順序不靠 dict 插入順序，換個人改程式也不會整份 diff。
    # ensure_ascii=False：中文要看得懂，公開 repo 的假資料本來就是給人讀的。
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def generate(seed: int = DEFAULT_SEED, out_dir: Path | str | None = None) -> dict[str, Path]:
    """產生五個 JSON。同一個 seed 一定產出逐 byte 相同的檔。"""
    target = Path(out_dir) if out_dir is not None else DATA_DIR
    target.mkdir(parents=True, exist_ok=True)
    dataset = _build(seed)
    written: dict[str, Path] = {}
    for name in FILENAMES:
        key = name.removesuffix(".json")
        path = target / name
        _dump(path, dataset[key])
        written[name] = path
    return written


def load_dataset(data_dir: Path | str | None = None) -> dict[str, list[dict]]:
    """讀回五個 JSON（Mock provider 與測試共用這一個入口）。"""
    source = Path(data_dir) if data_dir is not None else DATA_DIR
    dataset: dict[str, list[dict]] = {}
    for name in FILENAMES:
        dataset[name.removesuffix(".json")] = json.loads(
            (source / name).read_text(encoding="utf-8")
        )
    return dataset


if __name__ == "__main__":  # pragma: no cover - 手動重新產生資料用
    for filename, written_path in generate().items():
        print(f"{filename} -> {written_path}")
