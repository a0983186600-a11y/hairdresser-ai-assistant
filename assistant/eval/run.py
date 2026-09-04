"""跑一次考卷：`python -m assistant.eval.run --model qwen-plus`。

把三塊接起來——`answer_key`（標準答案）、`assistant.agent.run_chat`（受測的那一輪）、
`scorer`（打分）——然後寫一份 JSON 與一份 Markdown。

## 每一題都是新的一輪

不帶 session、不共用歷史。上一題查到的名單留在上下文裡，下一題就可能「記得」
一位客人而不再查工具——那會讓分數變成在測記憶力，不是測工具使用。

## 受測的是模型，不是資料層

provider 永遠是 Mock、`as_of` 永遠是答案檔裡那一刻。所以同一題兩個模型看到的
資料一模一樣，差別只剩「它怎麼挑工具、怎麼講話」。

## 一題炸掉不算整場垮

端點會 429、會逾時、會回一個沒有 choices 的東西。那一題記成 `error` 拿 0 分，
**但不記成鐵律違規**——連不上不是「編客人」，混在一起會讓報告失去意義。

## 兩種跑法

- `--replay`：用 `assistant/replay/` 錄好的逐字稿跑，**零金鑰**。錄音只有六句，
  沒錄到的題目會拿到「這句話沒有錄音」——這是 smoke test，不是成績。
- `--model <名字>`：打真端點。模型名釘在 client 上，不讓 `QWEN_MODEL` 插隊
  （見 `assistant.eval.client.MeteredClient`）。

金鑰只從環境變數讀，一路上不會進報告：端點回的錯誤訊息在丟例外前就先洗過一次。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from assistant.adapters.provider import SalonDataProvider
from assistant.adapters.schemas import DesignerScope
from assistant.agent.loop import run_chat
from assistant.config.loader import Config
from assistant.eval.answer_key import AnswerKey, AnswerKeyItem, load_answer_key
from assistant.eval.client import (
    DEFAULT_TIMEOUT_SECONDS,
    MeteredClient,
    MissingModelCredentials,
    build_client,
)
from assistant.eval.scorer import (
    ItemScore,
    ModelReport,
    collect_tool_payloads,
    score_item,
    summarise,
    to_markdown,
)

__all__ = [
    "REPORTS_DIR",
    "REPLAY_MODE_LABEL",
    "LIVE_MODE_LABEL",
    "run_exam",
    "write_report",
    "main",
]

#: 對決的原始輸出住這裡（`assistant/eval/reports/<模型>.json`），跟著公開 repo 走。
REPORTS_DIR = Path(__file__).resolve().parent / "reports"

REPLAY_MODE_LABEL = "replay"
LIVE_MODE_LABEL = "live"

#: `--replay` 時報告上的模型名。寫成真模型名會是謊話：那一輪根本沒有連模型。
REPLAY_MODEL_LABEL = "replay"

_SLUG = re.compile(r"[^A-Za-z0-9._-]+")

#: 一題炸掉時記多長的錯誤訊息。端點的錯誤內文可以很長，報告要看得完。
_ERROR_LIMIT = 300


def _slug(name: str) -> str:
    return _SLUG.sub("-", name).strip("-") or "model"


def _one_question(
    item: AnswerKeyItem,
    *,
    metered: MeteredClient,
    provider: SalonDataProvider,
    scope: DesignerScope,
    config: Config,
    as_of: datetime,
) -> ItemScore:
    """問一題、打一次分。這裡是唯一會吞例外的地方。"""
    metered.reset()
    reply = ""
    called: list[str] = []
    payloads: list[dict[str, Any]] = []
    error: str | None = None

    try:
        result = run_chat(
            item.question,
            provider=provider,
            scope=scope,
            config=config,
            as_of=as_of,
            # session=None：每一題都是全新的一輪，不讓上一題的名單留在上下文裡。
            client=metered,
        )
    except Exception as exc:  # noqa: BLE001 - 端點什麼都可能丟，一題不該帶走整場
        error = f"{type(exc).__name__}: {exc}"[:_ERROR_LIMIT]
    else:
        reply = result.reply
        called = [record.name for record in result.tool_calls]
        payloads = collect_tool_payloads(result.transcript)

    return score_item(
        item,
        reply=reply,
        tool_calls=called,
        tool_payloads=payloads,
        seconds=metered.seconds(),
        usage=metered.usage(),
        error=error,
    )


def run_exam(
    *,
    client: Any,
    model: str,
    mode: str,
    key: AnswerKey,
    provider: SalonDataProvider,
    scope: DesignerScope,
    config: Config,
    only: Sequence[str] | None = None,
) -> ModelReport:
    """對一個 client 跑整份考卷（或 `only` 指定的那幾題），回一份成績單。

    `as_of` 一律取自答案檔——標準答案跟受測那一輪必須看同一個「現在」，
    差一天所有「幾天沒回來」就整欄對不上。
    """
    metered = client if isinstance(client, MeteredClient) else MeteredClient(client)
    as_of = datetime.fromisoformat(key.as_of)
    wanted = set(only) if only is not None else None
    items = [item for item in key.items if wanted is None or item.id in wanted]

    scores = [
        _one_question(
            item,
            metered=metered,
            provider=provider,
            scope=scope,
            config=config,
            as_of=as_of,
        )
        for item in items
    ]
    return summarise(model, mode, key.as_of, scores)


def write_report(report: ModelReport, out: Path | str) -> tuple[Path, Path]:
    """寫 JSON ＋ 同名的 Markdown。回 (json 路徑, md 路徑)。"""
    json_path = Path(out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path = json_path.with_suffix(".md")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="對一個 OpenAI 相容端點跑 10 題考卷")
    parser.add_argument(
        "--model",
        default=None,
        help="要考哪個模型（例如 qwen-plus）。不給就用設定裡的預設。",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="用錄好的逐字稿跑，零金鑰。只有錄過的題目答得出來，是 smoke test 不是成績。",
    )
    parser.add_argument("--replay-dir", default=None, help="錄音資料夾（預設 assistant/replay）")
    parser.add_argument("--only", action="append", default=None, help="只跑這幾題（可重複給）")
    parser.add_argument("--out", default=None, help="報告寫到哪（.md 會寫在同名旁邊）")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="單次端點呼叫的秒數上限",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    from assistant.adapters.mock import MockSalonDataProvider
    from assistant.config.loader import load_config

    args = _parse(argv)
    config = load_config()
    key = load_answer_key()
    provider = MockSalonDataProvider(config=config)
    scope = provider.designer_scopes()[0]

    if args.replay:
        from assistant.agent.replay import REPLAY_DIR, ReplayClient

        directory = Path(args.replay_dir) if args.replay_dir else REPLAY_DIR
        client: Any = MeteredClient(ReplayClient(directory))
        model, mode = REPLAY_MODEL_LABEL, REPLAY_MODE_LABEL
    else:
        model = args.model or os.environ.get(config.model.model_env) or config.model.model_default
        mode = LIVE_MODE_LABEL
        try:
            client = build_client(config, model=model, timeout=args.timeout)
        except MissingModelCredentials as exc:
            # 訊息裡只有變數名，沒有值——那是 build_client 的規矩，這裡照抄不加料。
            print(f"跑不了：{exc}")
            return 2

    report = run_exam(
        client=client,
        model=model,
        mode=mode,
        key=key,
        provider=provider,
        scope=scope,
        config=config,
        only=args.only,
    )
    out = Path(args.out) if args.out else REPORTS_DIR / f"{_slug(model)}.json"
    json_path, md_path = write_report(report, out)

    print(
        f"{model}（{mode}）：工具序列 {report.tool_sequence_correct}/{report.item_count}"
        f"、數字 {report.number_rate:.0%}、鐵律違規 {report.violation_count} 次"
        f"、平均 {report.avg_seconds:.1f} 秒 / {report.avg_total_tokens:.0f} token"
    )
    print(f"報告：{json_path}、{md_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - 命令列進入點
    raise SystemExit(main())
