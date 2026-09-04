"""工具層：模型看得到的 9 個工具，以及執行它們的唯一入口。

`registry` 是這一層全部的內容——schema 產生、參數夾住、scope/as_of 注入、遮罩、
結構化錯誤。agent 迴圈只認得 `tool_schemas()` 與 `dispatch()` 這兩個名字。
"""

from assistant.tools.registry import DRAFT_TOOL_NAME, TOOL_NAMES, dispatch, tool_schemas

__all__ = ["tool_schemas", "dispatch", "TOOL_NAMES", "DRAFT_TOOL_NAME"]
