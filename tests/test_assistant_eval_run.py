"""考卷跑一輪：`python -m assistant.eval.run`。

這一支不打真模型（真模型有它自己的 skip-if-no-key 測試）。要釘的是**跑法本身**：

- 十題都會被問到，一題炸掉不會整場垮
- `--replay` 沒有任何金鑰也跑得完（評審 clone 下來的第一步）
- 報告寫成 JSON ＋ Markdown，而且**裡面不准有金鑰**
- 計時與 token 從端點回的 usage 來，不是猜的
"""

from __future__ import annotations

import json

import pytest

from assistant.adapters.mock import MockSalonDataProvider
from assistant.config.loader import load_config
from assistant.demo_data.generate import ANCHOR
from assistant.eval.answer_key import load_answer_key
from assistant.eval.client import MeteredClient, Usage
from assistant.eval.run import main, run_exam

AS_OF = ANCHOR


@pytest.fixture(scope="module")
def config():
    return load_config()


@pytest.fixture(scope="module")
def provider(config):
    return MockSalonDataProvider(config=config)


@pytest.fixture(scope="module")
def scope(provider):
    return provider.designer_scopes()[0]


@pytest.fixture(scope="module")
def key():
    return load_answer_key()


class PerfectClient:
    """照答案檔叫工具、照答案檔講數字的假模型。用來驗滿分那一條路是通的。"""

    is_replay = False

    def __init__(self, answer_key) -> None:
        self.items = {item.question: item for item in answer_key.items}
        self.calls = 0

    def complete(self, messages: list[dict], tools: list[dict], *, model: str) -> dict:
        self.calls += 1
        question = next(
            entry.get("content", "")
            for entry in reversed(messages)
            if entry.get("role") == "user"
        )
        item = self.items[question]
        already = sum(1 for entry in messages if entry.get("role") == "tool")
        if already < len(item.tool_arguments):
            step = item.tool_arguments[already]
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call-{already}",
                        "type": "function",
                        "function": {
                            "name": step.tool,
                            "arguments": json.dumps(step.arguments, ensure_ascii=False),
                        },
                    }
                ],
            }
        numbers = "、".join(str(value) for value in item.key_numbers)
        names = "、".join(item.result_summary.top_masked_names)
        if item.expects_empty:
            body = "沒有符合條件的資料。"
        else:
            body = f"已知金額與人數：{numbers}。名單：{names}。"
        unknown = item.result_summary.unknown_amount_visits or 0
        if unknown:
            body += f"另有 {unknown} 筆沒有金額紀錄。"
        return {"role": "assistant", "content": body, "tool_calls": None}


class BrokenClient:
    """第一題就炸的假模型。一題死掉不該把整場考試帶走。"""

    is_replay = False

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict], tools: list[dict], *, model: str) -> dict:
        self.calls += 1
        raise RuntimeError("端點掛了")


class CountingClient:
    """回一句話就結束，順便回報 usage——量 token 的那一條路要走得通。"""

    is_replay = False

    def __init__(self) -> None:
        self.last_usage = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

    def complete(self, messages: list[dict], tools: list[dict], *, model: str) -> dict:
        return {"role": "assistant", "content": "我沒有查。", "tool_calls": None}


# --- 1. 一輪完整的考試 --------------------------------------------------------


def test_a_client_that_follows_the_answer_key_scores_close_to_full_marks(
    provider, scope, config, key
):
    report = run_exam(
        client=PerfectClient(key),
        model="fake-perfect",
        mode="test",
        key=key,
        provider=provider,
        scope=scope,
        config=config,
    )

    assert report.item_count == 10
    assert [item.id for item in report.items] == [f"AH-{n:02d}" for n in range(1, 11)]
    assert report.tool_sequence_correct == 10
    assert report.tool_sequence_rate == 1.0
    assert report.violation_count == 0
    assert report.number_rate == 1.0


def test_the_trap_question_is_answered_as_empty_not_as_a_name(provider, scope, config, key):
    report = run_exam(
        client=PerfectClient(key),
        model="fake-perfect",
        mode="test",
        key=key,
        provider=provider,
        scope=scope,
        config=config,
    )
    trap = next(item for item in report.items if item.id == "AH-08")

    assert trap.violations == []
    assert trap.passed is True


def test_one_exploding_question_does_not_take_the_whole_exam_down(provider, scope, config, key):
    report = run_exam(
        client=BrokenClient(),
        model="fake-broken",
        mode="test",
        key=key,
        provider=provider,
        scope=scope,
        config=config,
    )

    assert report.item_count == 10
    assert all(item.error for item in report.items)
    assert report.tool_sequence_correct == 0
    assert report.violation_count == 0


def test_token_usage_comes_from_the_endpoints_own_numbers(provider, scope, config, key):
    inner = CountingClient()
    report = run_exam(
        client=MeteredClient(inner),
        model="fake-counting",
        mode="test",
        key=key,
        provider=provider,
        scope=scope,
        config=config,
    )

    assert report.avg_total_tokens == pytest.approx(18.0)
    assert all(item.prompt_tokens == 11 for item in report.items)
    assert report.avg_seconds >= 0.0


def test_the_metered_client_resets_between_questions():
    inner = CountingClient()
    metered = MeteredClient(inner)

    metered.complete([], [], model="x")
    metered.complete([], [], model="x")
    doubled = metered.usage()
    metered.reset()
    metered.complete([], [], model="x")

    assert doubled == Usage(prompt_tokens=22, completion_tokens=14, total_tokens=36, rounds=2)
    assert metered.usage().rounds == 1
    assert metered.is_replay is False


class EchoModelClient:
    """把收到的 model 名字回出來。用來驗「對決指名的模型真的被打到」。"""

    is_replay = False

    def __init__(self) -> None:
        self.seen: list[str] = []

    def complete(self, messages: list[dict], tools: list[dict], *, model: str) -> dict:
        self.seen.append(model)
        return {"role": "assistant", "content": "沒有符合條件的資料。", "tool_calls": None}


def test_the_model_named_on_the_command_line_is_the_model_that_gets_called(
    provider, scope, config, key, monkeypatch
):
    """對決的命脈：`--model qwen-turbo` 卻打到 QWEN_MODEL 指的那個，整張表就是假的。"""
    monkeypatch.setenv("QWEN_MODEL", "some-other-model")
    inner = EchoModelClient()

    run_exam(
        client=MeteredClient(inner, model="qwen-turbo"),
        model="qwen-turbo",
        mode="test",
        key=key,
        provider=provider,
        scope=scope,
        config=config,
        only=["AH-01"],
    )

    assert inner.seen == ["qwen-turbo"]


def test_an_endpoint_error_never_carries_the_key_into_the_report():
    """端點把整個請求回吐是有前例的。金鑰不准跟著進報告——所以在丟例外前就洗掉。"""
    from assistant.eval.client import OpenAICompatibleClient

    client = OpenAICompatibleClient("https://example.invalid/v1", "super-secret-key-value")

    scrubbed = client._scrub("InvalidApiKey: super-secret-key-value is not valid")

    assert "super-secret-key-value" not in scrubbed
    assert "[已遮罩金鑰]" in scrubbed


# --- 2. 命令列 ----------------------------------------------------------------


def test_replay_mode_runs_the_whole_exam_with_no_credentials_at_all(tmp_path, monkeypatch):
    """評審 clone 下來會做的第一件事：沒有金鑰也要跑得完。"""
    for name in ("QWEN_API_KEY", "QWEN_BASE_URL", "QWEN_MODEL"):
        monkeypatch.delenv(name, raising=False)
    out = tmp_path / "replay.json"

    assert main(["--replay", "--out", str(out)]) == 0

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["mode"] == "replay"
    assert len(payload["items"]) == 10
    assert (tmp_path / "replay.md").exists()


def test_the_report_never_carries_the_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "definitely-not-a-real-key-1234567890")
    out = tmp_path / "replay.json"

    assert main(["--replay", "--out", str(out)]) == 0

    assert "definitely-not-a-real-key" not in out.read_text(encoding="utf-8")
    assert "definitely-not-a-real-key" not in (tmp_path / "replay.md").read_text(encoding="utf-8")


def test_running_live_without_credentials_says_which_variable_is_missing(tmp_path, monkeypatch):
    for name in ("QWEN_API_KEY", "QWEN_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    assert main(["--model", "qwen-plus", "--out", str(tmp_path / "x.json")]) == 2


def test_a_single_question_can_be_run_on_its_own(tmp_path, monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    out = tmp_path / "one.json"

    assert main(["--replay", "--only", "AH-06", "--out", str(out)]) == 0

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [item["id"] for item in payload["items"]] == ["AH-06"]
