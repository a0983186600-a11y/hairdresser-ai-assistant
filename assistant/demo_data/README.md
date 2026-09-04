# 示範資料集（全部是假的）

這裡的五個 JSON 是 `generate.py` 用 **seed 42** 產生的，而且**跟著進版控**：
clone 下來不必先跑產生器，直接就有東西可以問。

```bash
uv run python -c "from assistant.demo_data.generate import generate; generate()"
```

同一個 seed 一定產出**逐 byte 相同**的檔案（`tests/test_assistant_demo_data.py` 跑兩次比對），
所以 repo 裡的檔案永遠等於 `generate(seed=42)` 的產物；手改會被測試抓到。

## 「現在」是固定的：2026-09-01T00:00:00+08:00

資料的錨點時間釘死在 `2026-09-01T00:00:00+08:00`（`generate.ANCHOR`，時區 `Asia/Taipei`）。
**這是刻意的，不是偷懶。** 三個理由：

1. README 的截圖、考卷（`docs/agent-bakeoff/exam.md`）算出來的答案、REPLAY 逐字稿，
   三邊要對得起來。資料每天飄，就沒有一個對得上。
2. 評審 clone 下來跑，看到的數字必須跟影片裡一模一樣。
3. 隨機抽樣的示範資料等於「每次跑答案都不一樣」，那就沒辦法用測試把行為釘住。

代價是**每一次查詢都要明講 `as_of`**（通常就是 `ANCHOR`）。
整個 `assistant/` 底下沒有一行 `datetime.now()`——這是同一個決定的另一半。

想看別組情境：`generate(seed=7, out_dir=...)`。預設仍然是 42。

## 姓名與電話

姓名由 `generate.py` 裡自己寫的**姓氏表與名字音節表**組合而成，
沒有抄任何真實客人名單；電話是 `09` 開頭的十碼假號碼，也不對應任何真實門號。
任何與真人的雷同純屬組合上的巧合。

## 檔案與形狀

| 檔案 | 筆數 | 一筆長什麼樣 |
|---|---|---|
| `designers.json` | 3 | `designer_ref` / `display_name` / `store_name` / `joined_at` |
| `customers.json` | 300 | `customer_ref` / `designer_ref` / `full_name` / `phone` / `created_at` / `line_user_ref` |
| `visits.json` | 1438 | `visit_ref` / `customer_ref` / `designer_ref` / `visited_at` / `service_family` / `amount_twd` |
| `appointments.json` | 28 | `appointment_ref` / `customer_ref` / `designer_ref` / `starts_at` / `service_family` / `status` |
| `conversations.json` | 84 | `conversation_ref` / `customer_ref` / `designer_ref` / `state` / `identity_ambiguity` / `safe_draft_fields` / `updated_at` / `messages[]` |

- `service_family` 是封閉 enum：`cut` / `perm` / `color` / `treatment` / `bleach` / `scalp`。
- `amount_twd` 有 **6.7%（97 筆）是 `null`**——真的會發生（POS 沒帶回金額、當場沒登記）。
  工具因此必須把「已知金額」跟「缺金額筆數」分開報，不准把已知金額講成完整營收。
- 每位客人只屬於一位設計師；對話也只屬於該客人的設計師。scope 隔離就靠這個形狀。
- 對話的 `state` 有 `active` / `closed` / `human_takeover`（有 15 段是真人接手）。
- `messages[].role` 是 `user` / `assistant` / `designer`；內容全是編的，
  沒有一句來自真實逐字稿。

## 資料裡刻意埋的形狀

產生器不是均勻亂灑，而是照沙龍真實的客群分佈來鋪，讓 8 個工具都有東西可以答：

| 客群 | 佔比 | 到店次數 | 回訪間隔 | 距 ANCHOR 最後一次到店 |
|---|---|---|---|---|
| 高消費常客 | 8% | 9–16 | 28–45 天 | 3–35 天 |
| 一般客 | 40% | 3–9 | 30–70 天 | 5–45 天 |
| 開始拖的 | 24% | 2–7 | 45–90 天 | 46–119 天 |
| 幾乎不回的 | 18% | 2–5 | 60–110 天 | 120–400 天 |
| 新客 | 10% | 1 | — | 3–60 天 |

- 回訪間隔以 **30–90 天**為主，長尾一路拉到 **399 天**——流失名單才有東西可抓。
- 60 天以上沒回來的有 **111 位**。
- 金額照服務家族的價格帶抽（`cut` 800–1500、`perm` 2500–6000、`color` 2000–5500、
  `treatment` 1200–3000、`bleach` 3000–7000、`scalp` 1000–2000，以 50 元為單位）。
- `appointments.json` 全部落在 `ANCHOR` **之後**（未來的預約）。

## 為什麼 JSON 長這樣

`ensure_ascii=False`（中文要看得懂，這是給人讀的示範資料）、`indent=2`、
`sort_keys=True`（欄位順序不靠 dict 插入順序，改程式不會整份 diff）。
