"""遮罩函式：姓名保留姓與末字、電話只留後四碼。

第二階段的 tools 層會用這兩個函式把 provider 回的原始資料遮起來；
正式 provider 走同一層，所以規則只能有一份。
規則對齊 docs/agent-bakeoff/answer-key.json 裡 SQL 用的遮罩式子。
"""

import pytest

from assistant.privacy import mask_name, phone_last4


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("王小明", "王○明"),
        ("陳怡", "陳○"),
        ("林", "○"),
        ("歐陽宇軒", "歐○○軒"),
        ("  王小明  ", "王○明"),
        ("", "未留姓名"),
        ("   ", "未留姓名"),
        (None, "未留姓名"),
    ],
)
def test_mask_name(raw, expected):
    assert mask_name(raw) == expected


def test_mask_name_keeps_length_so_designer_can_still_recognise():
    # 遮罩不是刪字：設計師要靠字數＋首尾認人，長度必須一樣。
    assert len(mask_name("張家豪")) == 3
    assert len(mask_name("司馬中原")) == 4


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0912345678", "5678"),
        ("09-1234-5678", "5678"),
        ("+886 912 345 678", "5678"),
        ("123", "123"),
        ("", None),
        (None, None),
        ("no digits here", None),
    ],
)
def test_phone_last4(raw, expected):
    assert phone_last4(raw) == expected
