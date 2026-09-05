"""跑「模型當場寫出來的那一支工具」的地方。

助理會自己寫工具，所以「模型寫的程式碼被執行」這件事一定會發生。問題從來不是
要不要讓它發生，而是**它在哪裡跑、跑的時候手上有什麼**。這個檔就是那個答案：

1. **先讀，不執行**（`check_code`）。AST 白名單：只准 import 那八個純計算模組，
   不准碰底線開頭的屬性、不准 `open/eval/exec/getattr...`、不准 `global`。
   違規的程式**一行都不會跑**，回傳的是「第幾行、哪個節點、為什麼」——
   模型才改得動（跟 `registry` 回錯誤的規矩同一條）。
2. **再關進另一個行程**（`run_in_sandbox`）。`python -I -S`、空的環境變數、
   CPU 上限、記憶體上限、牆上時鐘上限、輸出上限。行程內載入的是
   `MockSalonDataProvider`（固定 seed 的示範資料），而且是包過一層的：
   模型寫的程式只碰得到 8 個方法，而且拿到的姓名電話**已經遮罩**。

兩層都不是為了「擋住壞人」而已——第一層擋掉的絕大多數其實是模型的手滑
（想 `import pandas`、想 `print` 除錯），第二層擋的是它寫出無窮迴圈。
兩層都會回一句人話，模型可以照著改一次再試。

## 為什麼不用 `exec` 在同一個行程裡跑

同一個行程裡沒有「上限」這回事：無窮迴圈會把伺服器卡死，`sys.setrecursionlimit`
之後的爆炸會把整台服務帶走。子行程死了就是死了，伺服器只是收到一個非零的
returncode。這是把「示範會不會當場掛掉」從祈禱換成機制。

## 記憶體上限在 macOS 上套不上，而我們照實說

Darwin 沒有實作 `RLIMIT_AS`／`RLIMIT_DATA`，`setrlimit` 直接回 EINVAL
（Linux 有，匯出版的 Dockerfile 也是 Linux）。套不上時 `limits.memory_limit_applied`
回 `False`，牆上時鐘仍然是硬上限。**寧可少一道防線，也不要在回報上撒謊。**
"""

from __future__ import annotations

import ast
import json
import signal
import subprocess
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from assistant.adapters.provider import TOOL_METHOD_NAMES
from assistant.adapters.schemas import DesignerScope

__all__ = [
    "ALLOWED_IMPORT_ROOTS",
    "BLOCKED_NAMES",
    "SANDBOX_LIMITS",
    "SANDBOX_PROVIDER_METHODS",
    "ENTRY_POINT",
    "check_code",
    "run_in_sandbox",
    "child_main",
]

#: 模型寫的工具只准 import 這幾個**純計算**模組。共同點：沒有 I/O、沒有行程、
#: 沒有網路。少一個都寫得出有用的統計，多一個就多一條出去的路。
ALLOWED_IMPORT_ROOTS = frozenset(
    {"datetime", "math", "statistics", "collections", "itertools", "re", "decimal", "json"}
)

#: 名字一出現就擋。分兩類：**逃逸用的內建**（拿得到任意物件或執行任意字串）
#: 與**會開門的模組**（檔案、行程、網路）。白名單擋 import，這一份擋「已經在
#: 命名空間裡」的那些——例如 `open` 根本不用 import。
BLOCKED_NAMES = frozenset(
    {
        "exec", "eval", "compile", "open", "input", "breakpoint",
        "globals", "locals", "vars", "getattr", "setattr", "delattr",
        "__import__", "__builtins__", "memoryview",
        "os", "sys", "subprocess", "socket", "pathlib", "shutil",
        "importlib", "ctypes", "shelve", "pickle", "marshal", "signal",
        "threading", "multiprocessing", "resource", "gc", "inspect", "code",
    }
)

#: 約定好的進入點。簽名固定，模型不用猜，卡片上也講得清楚。
ENTRY_POINT = "run"
ENTRY_SIGNATURE = ("provider", "as_of")

#: 沙盒裡那顆 provider 的全部方法。就是 tools.md 的 8 個，一個不多。
SANDBOX_PROVIDER_METHODS: tuple[str, ...] = TOOL_METHOD_NAMES

#: 四道上限。CPU 與牆上時鐘各擋一種卡住的方式（燒 CPU vs. 睡著不動），
#: 筆數與位元組各擋一種爆量的方式（很多列 vs. 一個很大的字串）。
SANDBOX_LIMITS: dict[str, int] = {
    "cpu_seconds": 5,
    "wall_seconds": 8,
    "memory_bytes": 256 * 1024 * 1024,
    "max_rows": 200,
    "max_bytes": 64 * 1024,
}

#: 子行程把結果印在這個前綴後面。模型寫的程式如果自己 `print`，那些字會被收進
#: 一個有上限的緩衝區、不會混進結果；這個前綴是第二道保險。
RESULT_MARKER = "__GOTYOU_SANDBOX_RESULT__ "

#: 子行程的開場白：先把 `sys.path` 換成父行程的，才 import 得到 assistant。
#: `-I` 會忽略 PYTHONPATH、`-S` 不載入 site，所以路徑只能從 stdin 進來。
_CHILD_BOOTSTRAP = (
    "import sys, json\n"
    "payload = json.loads(sys.stdin.read())\n"
    "sys.path[:] = payload['sys_path']\n"
    "from assistant.tools.sandbox import child_main\n"
    "child_main(payload)\n"
)


# --- 第一層：讀程式碼，不執行 ---------------------------------------------------


def _violation(node: ast.AST, detail: str) -> dict[str, Any]:
    return {
        "line": getattr(node, "lineno", 0) or 0,
        "node": type(node).__name__,
        "detail": detail,
    }


def _entry_point_violations(tree: ast.Module) -> list[dict[str, Any]]:
    """一定要有 `def run(provider, as_of)`，而且參數名就是這兩個。

    參數名也管，是因為卡片上會把這個簽名寫給人看；名字對不上，看的人就得去猜
    第一個參數到底是什麼。約定死一次，兩邊都省事。
    """
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == ENTRY_POINT:
            names = tuple(arg.arg for arg in node.args.args)
            if names == ENTRY_SIGNATURE:
                return []
            return [
                _violation(
                    node,
                    f"進入點的簽名必須是 def {ENTRY_POINT}(provider, as_of)，這裡是 {names}",
                )
            ]
    return [
        {
            "line": 1,
            "node": "Module",
            "detail": f"缺少進入點：要有一個 def {ENTRY_POINT}(provider, as_of) 的函式",
        }
    ]


def check_code(code: str) -> list[dict[str, Any]]:
    """讀一遍，把違規列出來。回空 list 代表可以進沙盒（**不代表結果正確**）。

    只做靜態判斷，不執行任何東西——被擋下來的程式連 import 都不會發生。
    """
    tree = ast.parse(code)
    violations: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORT_ROOTS:
                    violations.append(
                        _violation(node, f"不准 import {alias.name}；只准 "
                                         f"{'、'.join(sorted(ALLOWED_IMPORT_ROOTS))}")
                    )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level != 0 or root not in ALLOWED_IMPORT_ROOTS:
                target = ("." * node.level) + (node.module or "")
                violations.append(
                    _violation(node, f"不准 from {target} import；只准 "
                                     f"{'、'.join(sorted(ALLOWED_IMPORT_ROOTS))}")
                )
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                violations.append(_violation(node, f"不准存取 dunder 屬性 {node.attr}"))
            elif node.attr.startswith("_"):
                violations.append(
                    _violation(node, f"不准存取底線開頭的屬性 {node.attr}（那是內部零件）")
                )
        elif isinstance(node, ast.Name):
            if node.id in BLOCKED_NAMES:
                violations.append(_violation(node, f"不准使用 {node.id}"))
            elif node.id.startswith("__") and node.id.endswith("__"):
                violations.append(_violation(node, f"不准使用 dunder 名稱 {node.id}"))
        elif isinstance(node, ast.Global | ast.Nonlocal):
            violations.append(_violation(node, "不准 global／nonlocal"))

    violations.extend(_entry_point_violations(tree))
    violations.sort(key=lambda item: (item["line"], item["detail"]))
    return violations


# --- 沙盒裡那顆 provider：8 個方法、scope 已注入、姓名電話已遮罩 -----------------


def _shape(name: str, result: Any) -> Any:
    """provider 回的 pydantic 物件 → 遮罩過、可 JSON 序列化的 dict／list。

    遮罩用的是 `assistant.tools.registry` 那一份（全系統只有一份實作）：
    Mock 與正式共用同一個遮罩，沙盒也不例外。
    """
    from assistant.tools.registry import _mask_row, _redact_text

    if result is None:
        return None
    if isinstance(result, list):
        return [_mask_row(row.model_dump(mode="json")) for row in result]

    payload = _mask_row(result.model_dump(mode="json"))
    if name == "get_conversation_transcript":
        payload["messages"] = [
            {
                "role": message["role"],
                "created_at": message["created_at"],
                "redacted_content": _redact_text(message["content"]),
            }
            for message in payload.get("messages", [])
        ]
    return payload


def _bind(provider: Any, scope: DesignerScope, name: str):
    method = getattr(provider, name)

    def call(**kwargs: Any) -> Any:
        return _shape(name, method(scope, **kwargs))

    call.__name__ = name
    return call


class SandboxProvider:
    """模型寫的程式碼唯一碰得到的東西。

    底層 provider 與 scope 只活在 closure 裡，**不是屬性**：拿不到 `_provider`
    這種東西，也就繞不過遮罩。（`__closure__` 是 dunder，第一層就擋掉了。）
    """

    def __init__(self, provider: Any, scope: DesignerScope) -> None:
        for name in SANDBOX_PROVIDER_METHODS:
            object.__setattr__(self, name, _bind(provider, scope, name))

    def __setattr__(self, name: str, value: Any) -> None:  # pragma: no cover - 防呆
        raise AttributeError("沙盒裡的 provider 是唯讀的")


# --- 第二層：另一個行程 ---------------------------------------------------------


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    raise TypeError(f"這個型別不能放進結果：{type(value).__name__}")


def _truncate(value: Any, cap: int, flag: list[bool]) -> Any:
    """把每一層的 list 都夾到 `cap` 筆，並記下有沒有夾過。"""
    if isinstance(value, list):
        if len(value) > cap:
            flag[0] = True
            value = value[:cap]
        return [_truncate(item, cap, flag) for item in value]
    if isinstance(value, dict):
        return {key: _truncate(item, cap, flag) for key, item in value.items()}
    return value


class _CappedWriter:
    """模型寫的程式自己 print 的東西進這裡，滿了就丟掉。不進結果、不進 log。"""

    def __init__(self, cap: int) -> None:
        self._cap = cap
        self._size = 0

    def write(self, text: str) -> int:
        if self._size < self._cap:
            self._size += len(text)
        return len(text)

    def flush(self) -> None:
        return None


def _safe_builtins() -> dict[str, Any]:
    """給模型寫的程式用的內建函式。白名單，不是黑名單。"""
    import builtins

    names = (
        "abs", "all", "any", "bool", "dict", "dir", "divmod", "enumerate", "filter",
        "float", "format", "frozenset", "hasattr", "int", "isinstance", "issubclass",
        "iter", "len", "list", "map", "max", "min", "next", "print", "range", "repr",
        "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip",
        "abs", "ArithmeticError", "AttributeError", "Exception", "IndexError",
        "KeyError", "LookupError", "StopIteration", "TypeError", "ValueError",
        "ZeroDivisionError", "True", "False", "None",
    )
    safe = {name: getattr(builtins, name) for name in names if hasattr(builtins, name)}

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        """`import` 在 exec 裡走的是這一條。白名單再擋一次（AST 已經擋過）。"""
        if name.split(".")[0] not in ALLOWED_IMPORT_ROOTS:
            raise ImportError(f"沙盒不准 import {name}")
        return builtins.__import__(name, *args, **kwargs)

    safe["__import__"] = guarded_import
    return safe


def _apply_limits(limits: dict[str, int]) -> bool:
    """套上作業系統層級的上限。回傳「記憶體上限有沒有真的套上」。"""
    import resource

    resource.setrlimit(
        resource.RLIMIT_CPU, (limits["cpu_seconds"], limits["cpu_seconds"])
    )
    for which in ("RLIMIT_AS", "RLIMIT_DATA"):
        target = getattr(resource, which, None)
        if target is None:
            continue
        try:
            resource.setrlimit(target, (limits["memory_bytes"], limits["memory_bytes"]))
        except (ValueError, OSError):
            # Darwin 不支援這兩個，會回 EINVAL。照實回 False，不假裝有套上。
            continue
        return True
    return False


def child_main(payload: dict[str, Any]) -> None:
    """子行程的本體。結果一定印在 `RESULT_MARKER` 後面，例外一律變成結構化錯誤。"""
    import os
    import traceback

    limits = payload["limits"]
    answer: dict[str, Any] = {"pid": os.getpid()}

    try:
        from assistant.adapters.mock import MockSalonDataProvider
        from assistant.adapters.schemas import TAIPEI

        scope = DesignerScope(**payload["scope"])
        as_of = datetime.fromisoformat(payload["as_of"]).astimezone(TAIPEI)
        provider = SandboxProvider(MockSalonDataProvider(), scope)
    except Exception as exc:  # noqa: BLE001 - 連載入示範資料都失敗，照實說
        answer |= {
            "ok": False,
            "error": {
                "code": "sandbox_setup_failed",
                "exception": type(exc).__name__,
                "message": str(exc)[:400],
            },
        }
        print(RESULT_MARKER + json.dumps(answer, ensure_ascii=False))
        return

    # 上限最後才套：pydantic 與示範資料先載完，省下來的額度全部留給模型寫的那段。
    answer["memory_limit_applied"] = _apply_limits(limits)

    real_stdout = sys.stdout
    sys.stdout = _CappedWriter(64 * 1024)  # type: ignore[assignment]
    try:
        namespace: dict[str, Any] = {"__builtins__": _safe_builtins()}
        exec(compile(payload["code"], "<沙盒工具>", "exec"), namespace)  # noqa: S102
        entry = namespace.get(ENTRY_POINT)
        if not callable(entry):
            raise TypeError(f"沒有可以呼叫的 {ENTRY_POINT}()")
        raw = entry(provider, as_of)
    except BaseException as exc:  # noqa: BLE001 - 什麼都不准漏出去，包含 SystemExit
        tail = traceback.format_exc().strip().splitlines()
        answer |= {
            "ok": False,
            "error": {
                "code": "runtime_error",
                "exception": type(exc).__name__,
                "message": str(exc)[:400],
                "traceback_tail": tail[-1][:400] if tail else "",
            },
        }
    else:
        answer |= _package(raw, limits)
    finally:
        sys.stdout = real_stdout

    print(RESULT_MARKER + json.dumps(answer, ensure_ascii=False, default=str))


def _package(raw: Any, limits: dict[str, int]) -> dict[str, Any]:
    """把 `run()` 回的東西夾成「一定送得出去」的大小與形狀。"""
    if not isinstance(raw, dict | list):
        return {
            "ok": False,
            "error": {
                "code": "bad_result",
                "message": f"run() 要回 dict 或 list，這次回的是 {type(raw).__name__}",
            },
        }

    row_count = len(raw)
    flag = [False]
    fitted = _truncate(raw, limits["max_rows"], flag)

    try:
        text = json.dumps(fitted, ensure_ascii=False, default=_jsonable)
    except TypeError as exc:
        return {
            "ok": False,
            "error": {"code": "bad_result", "message": f"結果沒辦法轉成 JSON：{exc}"},
        }

    if len(text.encode("utf-8")) > limits["max_bytes"]:
        flag[0] = True
        fitted = {
            "output_truncated": True,
            "preview": text[: limits["max_bytes"] // 4],
        }
        text = json.dumps(fitted, ensure_ascii=False)

    return {
        "ok": True,
        "result": json.loads(text),
        "row_count": row_count,
        "truncated": flag[0],
    }


def run_in_sandbox(
    code: str,
    *,
    as_of: datetime,
    scope: DesignerScope,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """檢查 → 另開一個行程跑 → 回一個一定能 JSON 序列化的結果。

    不丟例外：語法錯、違規、逾時、崩潰全部變成 `{"ok": False, "error": {...}}`，
    因為呼叫端是一台正在服務的伺服器，它不該因為模型寫壞一支工具就倒。
    """
    budget = {**SANDBOX_LIMITS, **(limits or {})}
    started = time.monotonic()

    def finish(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            **payload,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "limits": {
                **{key: budget[key] for key in SANDBOX_LIMITS},
                "memory_limit_applied": bool(payload.get("memory_limit_applied", False)),
            },
        }

    try:
        violations = check_code(code)
    except SyntaxError as exc:
        return finish(
            {
                "ok": False,
                "error": {
                    "code": "syntax_error",
                    "line": exc.lineno or 1,
                    "message": (exc.msg or "語法錯誤")[:400],
                },
            }
        )
    if violations:
        return finish(
            {
                "ok": False,
                "error": {
                    "code": "forbidden_code",
                    "message": "這段程式碼用到沙盒不准的東西，改掉下面幾行再試一次。",
                    "violations": violations,
                },
            }
        )

    payload = json.dumps(
        {
            "sys_path": [entry for entry in sys.path if isinstance(entry, str)],
            "code": code,
            "as_of": as_of.isoformat(),
            "scope": scope.model_dump(),
            "limits": budget,
        },
        ensure_ascii=False,
    )

    # `-B`：不准寫 .pyc。子行程拿的是空的環境，所以 `PYTHONDONTWRITEBYTECODE`
    # 進不去，得用旗標講。跑一次模型寫的工具不該在套件目錄裡留下任何東西——
    # 匯出驗收就是這樣抓到的：匯出目錄跑完測試之後多了一堆 __pycache__。
    process = subprocess.Popen(  # noqa: S603 - 固定的 argv，沒有 shell
        [sys.executable, "-I", "-S", "-B", "-c", _CHILD_BOOTSTRAP],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # 空的環境：子行程連模型金鑰長什麼樣子都看不到。
        env={},
    )
    pid = process.pid
    try:
        out, err = process.communicate(payload, timeout=budget["wall_seconds"])
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return finish(
            {
                "ok": False,
                "pid": pid,
                "error": {
                    "code": "timeout",
                    "message": f"這支工具跑超過 {budget['wall_seconds']} 秒，已經中止。",
                },
            }
        )

    for line in reversed(out.splitlines()):
        if line.startswith(RESULT_MARKER):
            answer = json.loads(line[len(RESULT_MARKER) :])
            answer.setdefault("pid", pid)
            return finish(answer)

    killed_by_cpu = process.returncode == -int(signal.SIGXCPU)
    return finish(
        {
            "ok": False,
            "pid": pid,
            "error": {
                "code": "cpu_limit" if killed_by_cpu else "crashed",
                "message": (
                    f"這支工具用掉超過 {budget['cpu_seconds']} 秒 CPU，已經中止。"
                    if killed_by_cpu
                    else f"沙盒行程沒有跑完（returncode={process.returncode}）。"
                ),
                "stderr_tail": (err or "").strip().splitlines()[-1][:400]
                if (err or "").strip()
                else "",
            },
        }
    )
