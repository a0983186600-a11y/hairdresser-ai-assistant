"""沙盒：模型當場寫的一支只讀工具，在這裡跑或在這裡被擋下來。

助理會自己寫工具，所以「模型寫的程式」這件事一定會發生；問題只剩它在哪裡跑。
這一份把它關進一個**另外的行程**，先用 AST 白名單擋掉一整類寫法，再用作業系統
的資源上限擋住剩下的（燒 CPU、吃記憶體、印到天荒地老）。

這支測試守的是那兩層，逐條對應 `assistant/tools/sandbox.py` 的限制清單：

1. 合法的程式跑得出結果，而且結果是**遮罩過的**假資料。
2. `import os`／`open()`／`__class__`／`getattr` 一律不執行，錯誤要帶行號。
3. 無窮迴圈會被殺（CPU 上限或牆上時鐘，看哪個先到）。
4. 輸出爆量會被截斷，不會把整個伺服器的記憶體吃掉。
5. 使用者程式丟的例外變成結構化錯誤，伺服器不准跟著倒。
6. 跑完之後示範資料**一個字都沒變**——這是「只讀」三個字的實際定義。
"""

from __future__ import annotations

import json
import time

import pytest

from assistant.adapters.mock import MockSalonDataProvider
from assistant.adapters.schemas import DesignerScope
from assistant.demo_data.generate import ANCHOR
from assistant.tools import sandbox


@pytest.fixture(scope="module")
def scope() -> DesignerScope:
    return MockSalonDataProvider().designer_scopes()[0]


def _run(code: str, scope: DesignerScope) -> dict:
    return sandbox.run_in_sandbox(code, as_of=ANCHOR, scope=scope)


# --- 1. 合法的程式跑得出結果 ----------------------------------------------------


def test_a_legal_tool_runs_and_comes_back_with_rows(scope):
    result = _run(
        "def run(provider, as_of):\n"
        "    rows = provider.list_inactive_customers(inactive_days=60, limit=5, as_of=as_of)\n"
        "    return [{'who': r['masked_name'],\n"
        "             'days': r['days_since_last_visit']} for r in rows]\n",
        scope,
    )
    assert result["ok"] is True, result
    assert result["row_count"] == 5
    assert len(result["result"]) == 5
    assert all(set(row) == {"who", "days"} for row in result["result"])


def test_the_sandbox_only_ever_sees_masked_customers(scope):
    """沙盒拿到的是遮罩後的資料：`full_name` 與 `phone` 進不到模型寫的程式裡。"""
    result = _run(
        "def run(provider, as_of):\n"
        "    rows = provider.list_inactive_customers(inactive_days=1, limit=3, as_of=as_of)\n"
        "    return {'keys': sorted(rows[0]), 'sample': rows[0]}\n",
        scope,
    )
    assert result["ok"] is True, result
    keys = result["result"]["keys"]
    assert "full_name" not in keys and "phone" not in keys
    assert "masked_name" in keys
    assert "○" in result["result"]["sample"]["masked_name"]


def test_the_eight_provider_methods_are_the_only_door(scope):
    result = _run(
        "def run(provider, as_of):\n"
        "    return sorted(name for name in dir(provider) if not name.startswith('_'))\n",
        scope,
    )
    assert result["ok"] is True, result
    assert result["result"] == sorted(sandbox.SANDBOX_PROVIDER_METHODS)


def test_the_whitelisted_standard_library_still_works(scope):
    result = _run(
        "import statistics\n"
        "from datetime import timedelta\n"
        "def run(provider, as_of):\n"
        "    rows = provider.rank_customers_by_spend(days=90, limit=5, as_of=as_of)\n"
        "    amounts = [r['known_spend_twd'] for r in rows]\n"
        "    return {'median': statistics.median(amounts),\n"
        "            'window_start': (as_of - timedelta(days=90)).isoformat()}\n",
        scope,
    )
    assert result["ok"] is True, result
    assert result["result"]["median"] > 0
    assert result["result"]["window_start"].startswith("2026-")


# --- 2. AST 白名單：擋下來、講清楚是哪一行 --------------------------------------


@pytest.mark.parametrize(
    ("label", "code"),
    [
        ("import os", "import os\ndef run(provider, as_of):\n    return os.listdir('.')\n"),
        (
            "from pathlib import",
            "from pathlib import Path\ndef run(provider, as_of):\n    return str(Path('.'))\n",
        ),
        ("open()", "def run(provider, as_of):\n    return open('/etc/passwd').read()\n"),
        ("__class__", "def run(provider, as_of):\n    return str(provider.__class__)\n"),
        ("getattr", "def run(provider, as_of):\n    return getattr(provider, 'x', 1)\n"),
        ("eval", "def run(provider, as_of):\n    return eval('1+1')\n"),
        ("__import__", "def run(provider, as_of):\n    return __import__('os').getcwd()\n"),
        ("global", "x = 1\ndef run(provider, as_of):\n    global x\n    x = 2\n    return x\n"),
        (
            "private attribute",
            "def run(provider, as_of):\n    return str(provider._provider)\n",
        ),
    ],
)
def test_forbidden_code_never_runs_and_says_which_line(label: str, code: str, scope):
    result = _run(code, scope)
    assert result["ok"] is False, label
    assert result["error"]["code"] == "forbidden_code", (label, result)
    violations = result["error"]["violations"]
    assert violations, label
    for violation in violations:
        assert violation["line"] >= 1
        assert violation["node"]
        assert violation["detail"]


def test_the_checker_refuses_before_anything_is_executed(tmp_path, scope):
    """被擋下來的程式**不執行**：連寫檔案這種副作用都不該發生。"""
    target = tmp_path / "written-by-sandbox.txt"
    result = _run(
        "import os\n"
        "def run(provider, as_of):\n"
        f"    open({str(target)!r}, 'w').write('x')\n"
        "    return []\n",
        scope,
    )
    assert result["ok"] is False
    assert not target.exists(), "被擋下來的程式不准留下任何副作用"


def test_code_without_the_agreed_entry_point_is_rejected(scope):
    result = _run("def helper(a):\n    return a\n", scope)
    assert result["ok"] is False
    assert result["error"]["code"] == "forbidden_code"
    assert any("run(provider, as_of)" in v["detail"] for v in result["error"]["violations"])


def test_syntax_errors_come_back_as_a_line_number_not_a_traceback(scope):
    result = _run("def run(provider, as_of)\n    return []\n", scope)
    assert result["ok"] is False
    assert result["error"]["code"] == "syntax_error"
    assert result["error"]["line"] >= 1


# --- 3. 跑太久會被殺 ------------------------------------------------------------


def test_an_endless_loop_is_killed_within_the_wall_clock_budget(scope):
    started = time.monotonic()
    result = _run("def run(provider, as_of):\n    while True:\n        pass\n", scope)
    elapsed = time.monotonic() - started

    assert result["ok"] is False
    assert result["error"]["code"] in {"cpu_limit", "timeout"}, result
    assert elapsed < sandbox.SANDBOX_LIMITS["wall_seconds"] + 4, elapsed


# --- 4. 輸出爆量被截斷 ----------------------------------------------------------


def test_a_list_longer_than_the_row_cap_is_truncated_and_says_so(scope):
    cap = sandbox.SANDBOX_LIMITS["max_rows"]
    result = _run(
        "def run(provider, as_of):\n"
        f"    return [{{'i': i}} for i in range({cap * 3})]\n",
        scope,
    )
    assert result["ok"] is True, result
    assert result["truncated"] is True
    assert len(result["result"]) == cap
    assert result["row_count"] == cap * 3


def test_a_huge_payload_never_comes_back_whole(scope):
    result = _run(
        "def run(provider, as_of):\n"
        "    return {'blob': 'x' * 4000000}\n",
        scope,
    )
    assert result["ok"] is True, result
    assert result["truncated"] is True
    assert len(json.dumps(result["result"])) <= sandbox.SANDBOX_LIMITS["max_bytes"] + 4096


# --- 5. 例外變成結構化錯誤 ------------------------------------------------------


def test_an_exception_inside_the_tool_is_reported_not_raised(scope):
    result = _run("def run(provider, as_of):\n    return 1 / 0\n", scope)
    assert result["ok"] is False
    assert result["error"]["code"] == "runtime_error"
    assert result["error"]["exception"] == "ZeroDivisionError"
    assert "division" in result["error"]["message"]
    assert result["error"]["traceback_tail"]


def test_a_wrong_argument_to_a_provider_method_is_reported_not_raised(scope):
    result = _run(
        "def run(provider, as_of):\n"
        "    return provider.list_inactive_customers(\n"
        "        inactive_days=99999999, limit=5, as_of=as_of)\n",
        scope,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "runtime_error"
    assert result["error"]["exception"]


def test_a_result_that_is_not_json_shaped_is_reported(scope):
    result = _run("def run(provider, as_of):\n    return provider\n", scope)
    assert result["ok"] is False
    assert result["error"]["code"] in {"bad_result", "runtime_error"}


# --- 6. 只讀：跑完之後示範資料一個字都沒變 --------------------------------------


def test_the_sandbox_cannot_change_the_demo_data(scope):
    """跑之前跟跑之後拍同一張快照，必須一模一樣。"""
    provider = MockSalonDataProvider()

    def snapshot() -> str:
        rows = provider.list_inactive_customers(
            scope, inactive_days=1, limit=100, as_of=ANCHOR
        )
        return json.dumps([row.model_dump(mode="json") for row in rows], sort_keys=True)

    before = snapshot()
    result = _run(
        "def run(provider, as_of):\n"
        "    rows = provider.list_inactive_customers(inactive_days=1, limit=50, as_of=as_of)\n"
        "    for row in rows:\n"
        "        row['masked_name'] = 'HACKED'\n"
        "        row['days_since_last_visit'] = -1\n"
        "    return {'touched': len(rows)}\n",
        scope,
    )
    assert result["ok"] is True, result
    assert snapshot() == before, "沙盒改到了示範資料"


def test_the_sandbox_runs_in_another_process(scope):
    """同一個行程裡跑就沒有上限可言：這裡確認它真的是另一個 pid。"""
    import os

    result = _run("def run(provider, as_of):\n    return {'ok': True}\n", scope)
    assert result["ok"] is True
    assert result["pid"] != os.getpid()


def test_the_limit_sheet_is_reported_with_every_run(scope):
    result = _run("def run(provider, as_of):\n    return []\n", scope)
    assert result["ok"] is True
    limits = result["limits"]
    assert limits["cpu_seconds"] == sandbox.SANDBOX_LIMITS["cpu_seconds"]
    assert limits["wall_seconds"] == sandbox.SANDBOX_LIMITS["wall_seconds"]
    assert limits["max_rows"] == sandbox.SANDBOX_LIMITS["max_rows"]
    assert limits["max_bytes"] == sandbox.SANDBOX_LIMITS["max_bytes"]
    # macOS 的 Darwin 核心沒有實作 RLIMIT_AS/RLIMIT_DATA，設了會 EINVAL。
    # 所以這一格是「有沒有真的套上」，不是「我們宣稱套上了」——
    # 套不上時牆上時鐘仍然是硬上限，但畫面與回報不准說謊。
    assert isinstance(limits["memory_limit_applied"], bool)
    assert limits["memory_bytes"] == sandbox.SANDBOX_LIMITS["memory_bytes"]


def test_the_allow_list_is_exactly_what_the_docs_promise():
    assert sandbox.ALLOWED_IMPORT_ROOTS == frozenset(
        {
            "datetime",
            "math",
            "statistics",
            "collections",
            "itertools",
            "re",
            "decimal",
            "json",
        }
    )
    for banned in ("os", "sys", "subprocess", "socket", "pathlib", "shutil",
                   "importlib", "ctypes"):
        assert banned not in sandbox.ALLOWED_IMPORT_ROOTS
        assert banned in sandbox.BLOCKED_NAMES
    for builtin in ("exec", "eval", "compile", "open", "input", "breakpoint",
                    "globals", "locals", "vars", "getattr", "setattr", "delattr",
                    "__import__"):
        assert builtin in sandbox.BLOCKED_NAMES


def test_check_code_is_usable_on_its_own_without_running_anything():
    violations = sandbox.check_code("import socket\ndef run(provider, as_of):\n    return []\n")
    assert [v["line"] for v in violations] == [1]
    assert sandbox.check_code("def run(provider, as_of):\n    return []\n") == []


def test_the_sandbox_leaves_no_bytecode_behind(tmp_path, monkeypatch):
    """跑一次模型寫的工具，不准在磁碟上留下任何東西——包含 .pyc。

    子行程拿的是**空的**環境，所以 `PYTHONDONTWRITEBYTECODE` 進不去；少了 `-B`
    它會在 `assistant/` 底下寫一整排 `__pycache__`。匯出驗收就是這樣抓到的：
    匯出目錄跑完測試之後多出十三個 .pyc，`package_data` 那條守衛立刻紅。
    """
    import subprocess

    seen: dict[str, list[str]] = {}
    real = subprocess.Popen

    def spy(argv, *args, **kwargs):
        seen["argv"] = list(argv)
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)
    monkeypatch.chdir(tmp_path)

    designer = MockSalonDataProvider().designer_scopes()[0]
    outcome = sandbox.run_in_sandbox(
        "def run(provider, as_of):\n    return {'ok': 1}\n", as_of=ANCHOR, scope=designer
    )

    assert outcome["ok"] is True
    assert "-B" in seen["argv"], f"少了 -B，子行程會寫 .pyc：{seen['argv']}"
    assert list(tmp_path.rglob("*")) == []
