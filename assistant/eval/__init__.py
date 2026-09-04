"""考卷評分器：同一份考卷、同一份標準答案，換模型跑一遍就知道誰穩。

三個檔，各自只做一件事：

- `answer_key`：對假資料直接呼叫工具算出 10 題的標準答案（**不經過模型**）。
- `scorer`：拿一句回覆對答案打分——工具序列、關鍵數字、鐵律、秒數與 token。
- `run`：把上面兩個接起來的 CLI。`--replay` 沒有金鑰也跑得完。

跟 `assistant/` 其他地方同一組規矩：不讀系統時鐘（「現在」由呼叫端給）、
相依只有 pydantic 與 pyyaml（打端點用標準函式庫的 urllib，不多開一個 httpx 的洞）。
"""

from __future__ import annotations

__all__: list[str] = []
