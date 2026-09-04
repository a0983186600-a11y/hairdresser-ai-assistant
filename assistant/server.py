"""示範伺服器：把 agent 與四個資料頁端到瀏覽器上。

## 這個檔存在的理由

公開 repo 的第一印象不是 README，是「clone 下來能不能跑起來」。所以這裡刻意
只有一個檔、只依賴 FastAPI，`uvicorn assistant.server:app` 之後打開瀏覽器就有東西可以問，
不需要金鑰、不需要資料庫、不需要 npm install。

## 它是「外面」與「裡面」的邊界

`assistant/` 底下其他每一個檔都是純的：不讀環境變數、不讀系統時鐘、不開連線。
那些髒事全部集中在這裡，因為總得有一層負責把外面的世界翻譯成裡面的參數：

- 環境變數 → `mode` / `provider` / `client` / `scope`
- 系統時鐘 → `as_of`（**唯一**讀牆上時鐘的地方，見 `now()`）
- HTTP 請求 → `run_chat` 的一次呼叫

`tests/test_assistant_open_source_hygiene.py` 對這個檔開了兩個具名豁免
（`datetime.now()` 與 FastAPI／httpx 相依），而且豁免本身也被測試綁住：
時鐘只能出現在 `now()` 裡面。邊界是一個看得見的洞，不是一片模糊地帶。

## 徽章代表助理讀誰，資料頁自己講自己讀誰

這兩件事本來就可以不一樣（正式唯讀連線＋沒設後台位址＝助理讀真的、四頁是假的），
所以**不准用一顆徽章代表兩件事**。規矩是：

- `mode = demo` → 四頁一律 fixture，即使設了 `BACKOFFICE_API_BASE`。
  （之前只看有沒有設那個環境變數，於是徽章寫 DEMO、畫面上卻是正式後台的
  全名與完整電話。）
- `mode = production` → 有 `BACKOFFICE_API_BASE` 才轉發；沒有就照樣 fixture，
  但回應與 `/health` 都要標明 `data_source=demo_fixtures`。

## 兩個模式，同一份程式

`DEMO_MODE=1`（預設）注入 `MockSalonDataProvider`＋固定錨點；設好
`PRODUCTION_READ_URL` 且這一份帶得動 `assistant.adapters.production` 時才切得到
正式唯讀連線。**切不過去就 400，不准安靜地退回 demo 卻把徽章寫成 PRODUCTION**——
影片右上角那顆徽章讀的就是這裡，它說謊等於整支影片說謊。

## 環境變數

| 名字 | 預設 | 做什麼 |
|---|---|---|
| `DEMO_MODE` | `1` | 開著就用固定 seed 假資料 |
| `PRODUCTION_READ_URL` | 無 | 正式唯讀連線；沒有它就切不到 production |
| `REPLAY_MODE` | 無 | 用錄好的逐字稿重播，不呼叫模型 |
| `ASSISTANT_DESIGNER_REF` | 假資料第一位 | 誰登入了（`scope`） |
| `BACKOFFICE_API_BASE` | 無 | 四個資料頁改成轉發正式後台（**只在 production 模式生效**） |
| `ASSISTANT_AS_OF` | 無 | 蓋掉「現在」（測試與錄影用）；沒帶時區一律當台北時間 |
"""

from __future__ import annotations

import importlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, field_validator

import assistant.agent as agent
from assistant.adapters.mock import MockSalonDataProvider
from assistant.adapters.schemas import TAIPEI, DesignerScope
from assistant.config.loader import Config, load_config
from assistant.demo_data.generate import ANCHOR, DATA_DIR

__all__ = ["app", "create_app", "now", "FRONTEND_DIR", "DEMO_PAGES"]

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
FIXTURES_DIR = FRONTEND_DIR / "fixtures"

#: 四個資料頁。名字同時是路由、fixture 檔名與轉發時的上游路徑。
DEMO_PAGES = ("bookings", "schedule", "customers", "settings")

DEMO_MODE_ENV = "DEMO_MODE"
PRODUCTION_URL_ENV = "PRODUCTION_READ_URL"
REPLAY_MODE_ENV = "REPLAY_MODE"
DESIGNER_REF_ENV = "ASSISTANT_DESIGNER_REF"
BACKOFFICE_ENV = "BACKOFFICE_API_BASE"
AS_OF_ENV = "ASSISTANT_AS_OF"

MODE_DEMO = "demo"
MODE_PRODUCTION = "production"

#: 四個資料頁的來源。這個字會同時出現在 `/health`、`/api/mode` 與資料頁的回應裡，
#: 三個地方只有一份真相。
PAGES_FIXTURES = "demo_fixtures"
PAGES_BACKOFFICE = "backoffice_forward"

#: 講給人看的那一版，給徽章旁邊用。
PAGES_LABEL = {
    PAGES_FIXTURES: "資料頁：示範",
    PAGES_BACKOFFICE: "資料頁：正式後台",
}

#: 正式模式卻沒有後台位址時，資料頁退回 fixture 的那句話。
#: 這一格最會騙人：助理讀的是正式唯讀資料（徽章沒說謊），但畫面上那幾筆是假的。
PAGES_FALLBACK_NOTE = (
    f"資料頁：示範。徽章上的 PRODUCTION 說的是助理讀正式唯讀資料；"
    f"四個資料頁沒有設定 {BACKOFFICE_ENV}，讀的還是固定 seed 的假資料。"
)

#: 沒有 replay 逐字稿也沒有金鑰時，假 client 回的那一句。
#: 回一句看得懂的話，比讓 agent 去建真 client 然後炸在缺金鑰上好。
REPLAY_STAND_IN_REPLY = "（REPLAY_MODE 開著，但這一份沒有帶錄好的逐字稿，所以只回這一句。）"

#: 同時記得幾段對話。示範伺服器不接資料庫，多的就丟掉最舊的。
SESSION_LIMIT = 200

_FORWARD_TIMEOUT_SECONDS = 8.0


def now() -> datetime:
    """**整個 `assistant/` 唯一讀系統時鐘的地方。**

    其他每一層的「現在」都是呼叫端傳進來的 `as_of`；那條規矩由
    `tests/test_assistant_open_source_hygiene.py` 的 AST 掃描守著，而這個函式是
    它唯一放行的洞——連放行都被綁死在「時鐘只能出現在 `now()` 裡面」這條斷言上。

    為什麼非得有這個洞：`DEMO_MODE` 用固定錨點，但接上正式唯讀連線之後，
    「最近 60 天沒回來」問的必須是真的今天。時間得從某個地方進來，
    那就讓它只從這一個看得見的門進來。
    """
    return datetime.now(tz=TAIPEI)


def _parse_as_of(raw: str) -> tuple[datetime | None, str | None]:
    """`ASSISTANT_AS_OF` 的兩個坑都在這裡處理掉，回傳 (值, 要講給人聽的話)。

    1. **沒帶時區的值一律當台北時間**。交給 `astimezone()` 去猜，猜的是**主機**
       的本地時間；正式主機是 UTC，於是 `09:00` 會被讀成台北 17:00——
       整份「今天」偏八個小時，而畫面上完全看不出來。
       （CLAUDE.md 那張排程表整張都在講同一個坑。）
    2. **看不懂的值不准把伺服器炸掉**。`create_app()` 在 `import` 這個模組時就跑了，
       格式錯一個字元就是一整串裸 traceback，錄影前五分鐘沒有人讀得完。
       退回預設（示範錨點／真的現在），把理由掛在 `/health` 上讓人看得見。
    """
    if not raw:
        return None, None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None, (
            f"{AS_OF_ENV} 看不懂（要 ISO 8601，像 YYYY-MM-DDTHH:MM+08:00），"
            f"這次先當作沒有設定，用預設的「現在」：{raw!r}"
        )
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TAIPEI), (
            f"{AS_OF_ENV} 沒有帶時區，當作台北時間讀"
            "（主機是 UTC，不講清楚會差八小時）"
        )
    return parsed.astimezone(TAIPEI), None


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _load_demo_designers() -> list[dict[str, Any]]:
    return json.loads((DATA_DIR / "designers.json").read_text(encoding="utf-8"))


def _resolve_scope() -> DesignerScope:
    """誰登入了。

    `scope` 刻意不是 `/api/chat` 收得到的欄位：前端送得出去的只有一句話與
    session_id。設計師要不到別人的客人，是因為他根本沒有那個旋鈕。
    """
    wanted = (os.environ.get(DESIGNER_REF_ENV) or "").strip()
    designers = _load_demo_designers()
    if not wanted:
        first = designers[0]
        return DesignerScope(
            designer_ref=first["designer_ref"], display_name=first["display_name"]
        )
    for row in designers:
        if row["designer_ref"] == wanted:
            return DesignerScope(
                designer_ref=row["designer_ref"], display_name=row["display_name"]
            )
    # 正式模式下 ref 不在假資料裡是正常的。顯示名不從外面猜，就寫「設計師」。
    return DesignerScope(designer_ref=wanted, display_name="設計師")


def _load_production_provider(read_url: str) -> tuple[Any | None, str | None]:
    """試著帶起正式唯讀 provider；帶不起來就說清楚為什麼。

    公開版（B 版）**沒有** `assistant/adapters/production.py`，所以這裡走不通是
    預期中的事，不是錯誤——回一句人話讓 `/health` 與 `/api/mode` 照實說。
    """
    if not read_url:
        return None, f"沒有設定 {PRODUCTION_URL_ENV}，這一份只跑得動示範資料"
    try:
        module = importlib.import_module("assistant.adapters.production")
    except Exception as exc:  # noqa: BLE001 - 缺檔、缺相依、匯入時炸掉都算「帶不起來」
        return None, f"這一份沒有正式 provider（{type(exc).__name__}），只跑得動示範資料"
    factory = getattr(module, "build_provider", None)
    if factory is None:
        return None, "assistant.adapters.production 沒有 build_provider()"
    try:
        return factory(read_url), None
    except Exception as exc:  # noqa: BLE001 - 連線參數錯就退回 demo，不要讓伺服器起不來
        return None, f"正式 provider 建不起來：{type(exc).__name__}: {exc}"


class _StandInReplayClient:
    """`assistant.agent.replay` 還沒合進來時的替身，形狀符合 `ChatClient`。"""

    def complete(self, messages: list[dict], tools: list[dict], *, model: str) -> dict:
        return {"role": "assistant", "content": REPLAY_STAND_IN_REPLY, "tool_calls": None}


def _load_replay_client(config: Config) -> tuple[Any, bool, str | None]:
    """REPLAY_MODE 的 client。模組缺席不准讓伺服器掛掉——影片 1:08 那段就靠它。"""
    try:
        module = importlib.import_module("assistant.agent.replay")
        replay_client = module.ReplayClient
    except Exception as exc:  # noqa: BLE001
        return (
            _StandInReplayClient(),
            False,
            f"這一份沒有 assistant.agent.replay（{type(exc).__name__}），先用替身回一句固定的話",
        )
    for arguments in ((config.agent.replay_dir,), ()):
        try:
            client = replay_client(*arguments)
            if client.recording_count == 0:
                return client, False, "載入 0 段錄音，請檢查 replay_dir 與安裝的錄音資料。"
            return client, True, None
        except TypeError:
            continue
        except Exception as exc:  # noqa: BLE001
            return (
                _StandInReplayClient(),
                False,
                f"ReplayClient 建不起來：{type(exc).__name__}: {exc}",
            )
    return _StandInReplayClient(), False, "ReplayClient 的建構參數對不上，先用替身"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    session_id: str | None = None

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("要問點什麼才有得答")
        return text


class ModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str


class _Runtime:
    """一個 app 的全部可變狀態。放在 app 上而不是模組上，測試才不會互相污染。"""

    def __init__(self) -> None:
        self.config: Config = load_config()
        self.scope: DesignerScope = _resolve_scope()
        self.backoffice_base: str = (os.environ.get(BACKOFFICE_ENV) or "").strip()

        raw_as_of = (os.environ.get(AS_OF_ENV) or "").strip()
        self.as_of_override, self.as_of_note = _parse_as_of(raw_as_of)

        read_url = (os.environ.get(PRODUCTION_URL_ENV) or "").strip()
        production, self.production_note = _load_production_provider(read_url)
        self._production_provider = production
        self.production_available: bool = production is not None

        demo_requested = _truthy(os.environ.get(DEMO_MODE_ENV, "1"))
        self.mode: str = (
            MODE_DEMO if demo_requested or not self.production_available else MODE_PRODUCTION
        )

        if _truthy(os.environ.get(REPLAY_MODE_ENV)):
            client, available, note = _load_replay_client(self.config)
            self.client: Any | None = client
            self.replay_available = available
            self.replay_note = note
        else:
            # None＝讓 agent 依 config.model 自己建 client。
            self.client = None
            self.replay_available = False
            self.replay_note = f"{REPLAY_MODE_ENV} 沒有開，會直接呼叫模型"

        self._mock: MockSalonDataProvider | None = None
        self.sessions: dict[str, agent.ChatSession] = {}

    # --- 兩個模式共用的取用點 -------------------------------------------------

    def provider(self) -> Any:
        if self.mode == MODE_PRODUCTION and self._production_provider is not None:
            return self._production_provider
        if self._mock is None:
            self._mock = MockSalonDataProvider(config=self.config)
        return self._mock

    def pages_source(self) -> str:
        """四個資料頁**現在**讀的是誰。

        跟 `mode` 是兩件事：`mode` 說的是助理（agent）讀誰，這裡說的是那四頁讀誰。
        demo 一律 fixture（設了後台位址也不轉發——徽章寫 DEMO 就不准讀正式資料）；
        production 有後台位址才轉發。
        """
        if self.mode == MODE_PRODUCTION and self.backoffice_base:
            return PAGES_BACKOFFICE
        return PAGES_FIXTURES

    def pages_note(self) -> str | None:
        """徽章寫 PRODUCTION、四頁卻還是假資料時要講的那句話。"""
        if self.mode == MODE_PRODUCTION and self.pages_source() == PAGES_FIXTURES:
            return PAGES_FALLBACK_NOTE
        return None

    def data_source_payload(self) -> dict[str, Any]:
        """`/health` 與 `/api/mode` 共用同一份來源說明：兩邊不准各講各的。"""
        source = self.pages_source()
        return {
            "data_source": source,
            "data_source_label": PAGES_LABEL[source],
            "data_source_note": self.pages_note(),
        }

    def as_of(self) -> datetime:
        """示範模式釘在資料錨點；正式模式問真的今天。"""
        if self.as_of_override is not None:
            return self.as_of_override
        if self.mode == MODE_DEMO:
            return ANCHOR
        return now()

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": self.mode,
            "provider": type(self.provider()).__name__,
            "replay_available": self.replay_available,
            "replay_note": self.replay_note,
            "production_available": self.production_available,
            "production_note": self.production_note,
            "as_of": self.as_of().isoformat(),
            "as_of_note": self.as_of_note,
            # 助理讀誰（徽章）與四個資料頁讀誰（下面那三格）是兩件事。
            "provider_data_source": (
                "fixed-seed demo data" if self.mode == MODE_DEMO else "read-only"
            ),
            **self.data_source_payload(),
        }

    def remember(self, session: agent.ChatSession) -> None:
        self.sessions[session.session_id] = session
        while len(self.sessions) > SESSION_LIMIT:
            self.sessions.pop(next(iter(self.sessions)))


def _fixture(page: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / f"{page}.json").read_text(encoding="utf-8"))


def _forward(base: str, path: str) -> JSONResponse:
    """A 版：四個資料頁轉發到正式後台的同一條路徑。"""
    url = base.rstrip("/") + path
    try:
        with httpx.Client(timeout=_FORWARD_TIMEOUT_SECONDS) as http:
            upstream = http.get(url)
    except Exception as exc:  # noqa: BLE001 - 連不上就講清楚是哪一段連不上
        raise HTTPException(
            status_code=502,
            detail=f"轉發到 {url} 失敗：{type(exc).__name__}: {exc}",
        ) from exc
    if upstream.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"轉發到 {url} 收到 {upstream.status_code}",
        )
    try:
        return JSONResponse(upstream.json())
    except ValueError as exc:
        raise HTTPException(
            status_code=502, detail=f"轉發到 {url} 收到的不是 JSON"
        ) from exc


def create_app() -> FastAPI:
    """每次呼叫都是一台獨立的伺服器：模式與 session 都掛在它身上。"""
    application = FastAPI(title="got you 設計師助理（示範）", docs_url=None, redoc_url=None)
    runtime = _Runtime()
    application.state.runtime = runtime

    @application.get("/health")
    def health() -> dict[str, Any]:
        return runtime.health()

    def _mode_payload() -> dict[str, Any]:
        return {
            "mode": runtime.mode,
            "production_available": runtime.production_available,
            "production_note": runtime.production_note,
            **runtime.data_source_payload(),
        }

    @application.get("/api/mode")
    def read_mode() -> dict[str, Any]:
        return _mode_payload()

    @application.post("/api/mode")
    def switch_mode(payload: ModeRequest) -> dict[str, Any]:
        wanted = payload.mode.strip().lower()
        if wanted == MODE_DEMO:
            runtime.mode = MODE_DEMO
        elif wanted == MODE_PRODUCTION:
            if not runtime.production_available:
                # 切不過去就當場說，不要讓徽章寫著 PRODUCTION 卻在讀假資料。
                raise HTTPException(status_code=400, detail=str(runtime.production_note))
            runtime.mode = MODE_PRODUCTION
        else:
            raise HTTPException(
                status_code=400, detail=f"mode 只能是 {MODE_DEMO} 或 {MODE_PRODUCTION}"
            )
        return _mode_payload()

    @application.post("/api/chat")
    def chat(payload: ChatRequest) -> dict[str, Any]:
        session = runtime.sessions.get(payload.session_id) if payload.session_id else None
        try:
            result = agent.run_chat(
                payload.message,
                provider=runtime.provider(),
                scope=runtime.scope,
                config=runtime.config,
                as_of=runtime.as_of(),
                session=session,
                client=runtime.client,
            )
        except NotImplementedError as exc:
            # 第二階段的 agent 迴圈還沒合進來時，給一句看得懂的話而不是 500 堆疊。
            raise HTTPException(
                status_code=503, detail=f"agent 迴圈還沒接上：{exc}"
            ) from exc

        session_id = session.session_id if session else uuid.uuid4().hex
        history = result.transcript or (session.history if session else [])
        runtime.remember(agent.ChatSession(session_id=session_id, history=list(history)))
        return {
            "reply": result.reply,
            "tool_calls": [record.model_dump() for record in result.tool_calls],
            "session_id": session_id,
            "model": result.model,
        }

    @application.get("/api/demo/{page}")
    def demo_page(page: str, request: Request) -> Any:
        if page not in DEMO_PAGES:
            raise HTTPException(status_code=404, detail=f"沒有這一頁：{page}")
        # 看的是 mode，不是「有沒有設 BACKOFFICE_API_BASE」——
        # 只看環境變數的話，徽章寫 DEMO 也會把正式後台的全名與電話端上畫面。
        if runtime.pages_source() == PAGES_BACKOFFICE:
            return _forward(runtime.backoffice_base, request.url.path)
        payload = _fixture(page)
        note = runtime.pages_note()
        if note is not None:
            # 正式模式卻讀 fixture：回應自己要講出來，不然畫面上看不出差別。
            payload = {**payload, "data_source": PAGES_FIXTURES, "data_source_note": note}
        return payload

    # 靜態檔掛在最後：上面每一條路由都先比對，剩下的才交給 assistant/frontend/。
    application.mount(
        "/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend"
    )
    return application


app = create_app()
