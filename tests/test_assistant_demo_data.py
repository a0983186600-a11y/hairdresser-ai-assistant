"""假資料產生器：固定 seed 一定產出逐 byte 相同的檔。

⚠ 這支刻意寫死絕對日期，理由與 `assistant/demo_data/generate.py` 的 ANCHOR 同一個：
**示範資料集是固定的**。README 截圖、考卷答案、REPLAY 逐字稿三邊都要對得上，
所以資料的「現在」被釘在 ANCHOR，測試也一律明講 as_of，沒有一行讀系統時鐘。
日曆走過去這支不會變紅——它量的是「同一個 seed 產出同一份檔案」，跟今天幾號無關。
"""

import json
import re
from datetime import timedelta
from pathlib import Path

from assistant.adapters.schemas import TAIPEI, ConversationState, MessageRole, ServiceFamily
from assistant.demo_data.generate import (
    ANCHOR,
    DATA_DIR,
    DEFAULT_SEED,
    FILENAMES,
    generate,
    load_dataset,
)

CHINESE_NAME = re.compile(r"^[一-鿿]{2,4}$")
FAKE_PHONE = re.compile(r"^09\d{8}$")


def _read_all(directory: Path) -> dict[str, bytes]:
    return {name: (directory / name).read_bytes() for name in FILENAMES}


def test_anchor_is_pinned_and_taipei():
    assert ANCHOR.isoformat() == "2026-09-01T00:00:00+08:00"
    assert ANCHOR.tzinfo is not None
    assert ANCHOR.utcoffset() == timedelta(hours=8)
    assert DEFAULT_SEED == 42


def test_same_seed_produces_byte_identical_files(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate(seed=DEFAULT_SEED, out_dir=first)
    generate(seed=DEFAULT_SEED, out_dir=second)
    assert _read_all(first) == _read_all(second)


def test_committed_json_matches_a_fresh_run(tmp_path: Path):
    """repo 裡的 JSON 就是 `generate(seed=42)` 的產物，不是手改過的。"""
    fresh = tmp_path / "fresh"
    generate(seed=DEFAULT_SEED, out_dir=fresh)
    assert _read_all(fresh) == _read_all(DATA_DIR)


def test_a_different_seed_produces_a_different_dataset(tmp_path: Path):
    other = tmp_path / "other"
    generate(seed=7, out_dir=other)
    assert _read_all(other) != _read_all(DATA_DIR)


def test_generate_reports_the_files_it_wrote(tmp_path: Path):
    written = generate(seed=DEFAULT_SEED, out_dir=tmp_path / "out")
    assert set(written) == set(FILENAMES)
    for name, path in written.items():
        assert path.name == name
        assert path.exists()


# --- 形狀 --------------------------------------------------------------------


def test_three_designers_each_with_their_own_customers():
    data = load_dataset()
    designers = data["designers"]
    assert len(designers) == 3
    refs = [d["designer_ref"] for d in designers]
    assert len(set(refs)) == 3
    for designer in designers:
        assert CHINESE_NAME.match(designer["display_name"])
    owned = {ref: 0 for ref in refs}
    for customer in data["customers"]:
        assert customer["designer_ref"] in owned
        owned[customer["designer_ref"]] += 1
    assert all(count >= 60 for count in owned.values())


def test_about_three_hundred_fake_customers_with_fake_phones():
    customers = load_dataset()["customers"]
    assert 280 <= len(customers) <= 320
    assert len({c["customer_ref"] for c in customers}) == len(customers)
    assert len({c["phone"] for c in customers}) == len(customers)
    for customer in customers:
        assert FAKE_PHONE.match(customer["phone"]), customer["phone"]
        assert CHINESE_NAME.match(customer["full_name"]), customer["full_name"]


def test_visits_use_the_closed_enum_and_never_run_past_the_anchor():
    data = load_dataset()
    known_refs = {c["customer_ref"] for c in data["customers"]}
    families = {f.value for f in ServiceFamily}
    for visit in data["visits"]:
        assert visit["customer_ref"] in known_refs
        assert visit["service_family"] in families
        assert visit["visited_at"].endswith("+08:00")
        assert visit["visited_at"] <= ANCHOR.isoformat()


def test_amounts_look_like_real_prices_and_about_eight_percent_are_unknown():
    visits = load_dataset()["visits"]
    bands = {
        "cut": (800, 1500),
        "perm": (2500, 6000),
        "color": (2000, 5500),
        "treatment": (1200, 3000),
        "bleach": (3000, 7000),
        "scalp": (1000, 2000),
    }
    unknown = 0
    for visit in visits:
        amount = visit["amount_twd"]
        if amount is None:
            unknown += 1
            continue
        low, high = bands[visit["service_family"]]
        assert low <= amount <= high, visit
    ratio = unknown / len(visits)
    assert 0.05 <= ratio <= 0.12, ratio


def test_return_intervals_are_mostly_thirty_to_ninety_days_with_a_long_tail():
    data = load_dataset()
    by_customer: dict[str, list[str]] = {}
    for visit in data["visits"]:
        by_customer.setdefault(visit["customer_ref"], []).append(visit["visited_at"])

    gaps = []
    for stamps in by_customer.values():
        ordered = sorted(stamps)
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            from datetime import datetime

            gaps.append(
                (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).days
            )
    assert gaps
    in_band = sum(1 for gap in gaps if 30 <= gap <= 90)
    assert in_band / len(gaps) >= 0.55

    # 長尾：一批人 120～400 天沒回來，流失名單才有東西可抓。
    last_seen = {ref: max(stamps) for ref, stamps in by_customer.items()}
    from datetime import datetime

    lapsed = [
        (ANCHOR - datetime.fromisoformat(stamp)).days for stamp in last_seen.values()
    ]
    assert sum(1 for days in lapsed if 120 <= days <= 400) >= 15
    assert max(lapsed) <= 400


def test_a_handful_of_high_spending_regulars_exist():
    data = load_dataset()
    spend: dict[str, int] = {}
    counts: dict[str, int] = {}
    for visit in data["visits"]:
        counts[visit["customer_ref"]] = counts.get(visit["customer_ref"], 0) + 1
        if visit["amount_twd"] is not None:
            spend[visit["customer_ref"]] = spend.get(visit["customer_ref"], 0) + visit["amount_twd"]
    big = [ref for ref, total in spend.items() if total >= 20000 and counts.get(ref, 0) >= 8]
    assert len(big) >= 5


def test_upcoming_appointments_are_all_after_the_anchor():
    data = load_dataset()
    appointments = data["appointments"]
    assert len(appointments) >= 15
    known_refs = {c["customer_ref"] for c in data["customers"]}
    families = {f.value for f in ServiceFamily}
    for appointment in appointments:
        assert appointment["customer_ref"] in known_refs
        assert appointment["service_family"] in families
        assert appointment["starts_at"] > ANCHOR.isoformat()


def test_every_designer_has_dozens_of_conversations_and_a_few_takeovers():
    data = load_dataset()
    conversations = data["conversations"]
    assert len(conversations) >= 60
    per_designer: dict[str, int] = {}
    states = {s.value for s in ConversationState}
    roles = {r.value for r in MessageRole}
    for conversation in conversations:
        per_designer[conversation["designer_ref"]] = (
            per_designer.get(conversation["designer_ref"], 0) + 1
        )
        assert conversation["state"] in states
        assert conversation["messages"]
        assert conversation["updated_at"] <= ANCHOR.isoformat()
        assert conversation["updated_at"] == conversation["messages"][-1]["created_at"]
        for message in conversation["messages"]:
            assert message["role"] in roles
            assert message["content"].strip()
    assert len(per_designer) == 3
    assert all(count >= 20 for count in per_designer.values())
    takeovers = [c for c in conversations if c["state"] == ConversationState.HUMAN_TAKEOVER.value]
    assert 3 <= len(takeovers) <= len(conversations) // 3


def test_a_conversation_only_ever_belongs_to_its_customers_designer():
    data = load_dataset()
    owner = {c["customer_ref"]: c["designer_ref"] for c in data["customers"]}
    for conversation in data["conversations"]:
        assert owner[conversation["customer_ref"]] == conversation["designer_ref"]


def test_json_is_utf8_readable_and_sorted_so_diffs_stay_small():
    for name in FILENAMES:
        raw = (DATA_DIR / name).read_text(encoding="utf-8")
        assert raw.endswith("\n")
        # ensure_ascii=False：中文要看得懂，不是 \uXXXX。
        assert "\\u" not in raw
        json.loads(raw)


def test_demo_data_readme_explains_the_seed_and_the_anchor():
    readme = (DATA_DIR / "README.md").read_text(encoding="utf-8")
    assert "42" in readme
    assert ANCHOR.isoformat() in readme
    for name in FILENAMES:
        assert name in readme
    assert TAIPEI.key in readme
