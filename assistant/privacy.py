"""遮罩：provider 回原始資料，對外的每一條路都要先經過這裡。

為什麼遮罩不放在 provider：正式 provider 與 Mock provider 都會被同一層 tools 呼叫，
遮罩寫在 provider 裡就會有兩份、也就會有一份忘記更新。這裡是唯一的一份。

規則對齊 `docs/agent-bakeoff/answer-key.json` 裡標準答案 SQL 用的遮罩式子，
公開文件與程式碼才不會各遮各的。
"""

import re

__all__ = ["mask_name", "phone_last4", "NO_NAME_PLACEHOLDER"]

NO_NAME_PLACEHOLDER = "未留姓名"

_NON_DIGIT = re.compile(r"\D")


def mask_name(raw: str | None) -> str:
    """姓名遮罩：保留姓與末字，中間換成 ○。

    王小明 → 王○明；陳怡 → 陳○；林 → ○；歐陽宇軒 → 歐○○軒；空白 → 未留姓名。
    字數保持一樣，設計師才認得出是誰（刪字就認不出來了）。
    """
    name = (raw or "").strip()
    if not name:
        return NO_NAME_PLACEHOLDER
    if len(name) == 1:
        return "○"
    if len(name) == 2:
        return name[0] + "○"
    return name[0] + "○" * (len(name) - 2) + name[-1]


def phone_last4(raw: str | None) -> str | None:
    """電話只留後四碼；沒有數字就回 None（不要回空字串，空字串在 UI 上看起來像有值）。"""
    digits = _NON_DIGIT.sub("", raw or "")
    return digits[-4:] or None
