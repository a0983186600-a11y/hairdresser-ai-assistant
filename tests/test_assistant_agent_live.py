"""端到端打真模型。**沒有金鑰就整支 skip**，CI 與評審 clone 下來都不會紅。

只驗一件事：接上真的 OpenAI 相容端點時，這一圈（提示詞 → tool call → 執行 →
回覆）真的轉得動。答案品質是考卷（`docs/agent-bakeoff/`）的事，不是這裡的事。
"""

from __future__ import annotations

import os

import pytest

from assistant.adapters.mock import MockSalonDataProvider
from assistant.agent import run_chat
from assistant.config.loader import load_config
from assistant.demo_data.generate import ANCHOR

# 「今天」＝示範資料集的錨點，從資料那邊 import，不在測試裡抄一份字面日期。
AS_OF = ANCHOR

config = load_config()

#: `tests/conftest.py` 在沒有 `.env` 時會塞一把**假的** QWEN_API_KEY 進環境
#: （值刻意寫得一看就知道是假的），所以「有沒有這個變數」不足以判斷能不能打真模型：
#: 照著那把假金鑰跑會拿到 401，而那不是這支測試要證明的事。
#: 判斷改成兩條都要成立：端點有設定，而且金鑰不是那把假的。
_PLACEHOLDER_KEYS = {"test-not-a-real-key", ""}


def _has_real_credentials() -> bool:
    return bool(os.environ.get(config.model.base_url_env)) and (
        os.environ.get(config.model.api_key_env, "") not in _PLACEHOLDER_KEYS
    )


pytestmark = pytest.mark.skipif(
    not _has_real_credentials(),
    reason=(
        f"沒有真的 {config.model.base_url_env} / {config.model.api_key_env}，"
        "跳過打真模型的測試"
    ),
)


def test_one_real_question_goes_all_the_way_through_the_loop():
    provider = MockSalonDataProvider(config=config)
    scope = provider.designer_scopes()[0]

    result = run_chat(
        "幫我看看誰快流失了，先抓 3 位最值得我主動關心的。",
        provider=provider,
        scope=scope,
        config=config,
        as_of=AS_OF,
    )

    assert result.reply.strip()
    assert result.model
    # 名單只能來自工具（鐵律 4）：沒打工具就代表它在憑記憶編人。
    assert [record.name for record in result.tool_calls]
    assert "get_retention_watchlist" in {record.name for record in result.tool_calls}

    record = next(r for r in result.tool_calls if r.name == "get_retention_watchlist")
    assert record.arguments.get("limit") is None or record.arguments["limit"] >= 1
