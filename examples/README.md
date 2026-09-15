# 讓 B 版不只是展示：可以修改的資料與完整流程

這裡的內容都是新編假資料，沒有取自真實名單、POS 或 LINE。
不要使用真實客資；此公開 demo 沒有正式登入與存取控制。

## 1. 先跑完一個真正有用的流程

完成 README 的 `uv sync --locked --extra dev` 後：

```sh
uv run python -m assistant.demo_data.validate examples/synthetic
uv run python -m assistant.demo_data.walkthrough examples/synthetic \
  --designer-ref demo-a --as-of 2026-09-01T12:00:00+08:00
```

這條流程會真的使用共用工具，不呼叫模型、不讀錄音、不連網、不預約或送信：

1. 找出 60 天沒回來的客人：只找到 `demo-c1`，92 天沒回來。
2. 讀取他的消費：兩次到店，已知金額 3000 元，另一次金額缺漏。
3. 依最後一次的「染髮」服務擬回訪草稿：人自己看過、決定是否使用。

輸出只含共用工具遮罩後的結果，`sent: false` 表示沒有送出。
`demo-c3` 是另一位設計師的客人，其 99000 元不應出現在 `demo-a` 的答案。
可改成 `--designer-ref demo-b` 比對各自的資料。這個參數是本機示範選擇器，
不是登入授權；不能把它原樣開放給公開網站的使用者。

## 2. 改資料，驗證答案有沒有跟著變

複製 `synthetic/` 到自己的本機資料夾，不覆蓋 `assistant/demo_data/`。
把 `demo-v1` 的金額由 3000 改成 3500，再跑驗證與上面的工具流程。
已知金額應變成 3500，缺金額仍是一筆。把 `demo-v2` 移到 8 月底，
則 `demo-c1` 不再符合 60 天條件，流程應停在空清單，不能補一個客人。

這是確定性工具結果，不是 AI 自由對話品質評分。自訂資料**不會**自動更新
網頁的固定工作台、錄音或預約面板；這一批刻意先提供獨立本機工具流程。
絕對不要換掉出貨資料後沿用 `REPLAY_MODE=1`，因為回答文字仍是舊錄音。

## 3. 從空白開始

`empty/` 有五個空陣列，格式有效但沒有設計師或客人，故不能產生回訪草稿。
參照 `synthetic/` 加入資料；五個檔都必須存在，沒有資料就保留 `[]`。

| 檔案 | 必填欄位 |
| --- | --- |
| designers.json | designer_ref、display_name、store_name、joined_at |
| customers.json | customer_ref、designer_ref、full_name、created_at |
| visits.json | visit_ref、customer_ref、designer_ref、visited_at、service_family、amount_twd |
| appointments.json | appointment_ref、customer_ref、designer_ref、starts_at、service_family、status |
| conversations.json | conversation_ref、customer_ref、designer_ref、state、identity_ambiguity、safe_draft_fields、updated_at、messages |

客人的 phone、line_user_ref、pos_customer_id 可省略或填 null；本例不填電話。
messages 每筆要有 role、created_at、content。
safe_draft_fields 可填空物件，或 service_family、preferred_date（YYYY-MM-DD）、
preferred_time（HH:MM）；不要把姓名電話塞進草稿。

- 識別碼用無個資的字串；每張表內不可重複。客人必须對應存在的設計師。
- 消費、預約、對話必须對應存在的客人，且 designer_ref 必須與該客人一致。
- 時間含時區，例如 `2026-09-01T12:00:00+08:00`，不接受無時區時間。
- amount_twd 是非負整數或 null；null 是未知，**不是 0**。退款不屬於本範本規格。
- service_family：cut / perm / color / treatment / bleach / scalp。
- 預約 status：pending / confirmed / cancelled。
- 對話 state：active / closed / human_takeover；role：user / assistant / designer。
- 不收未列出的欄位；訊息依時間排列，不能晚於對話 updated_at。
- 檢查器每檔最多 10 MiB、每表最多 50000 筆；只印表名、列號與錯誤類別，不印輸入值。

`validate` 成功 exit 0，失敗 exit 1。它驗的是格式與關聯，**不是去識別、同意書或
資料安全稽核**，也不會上傳資料。請勿把實際客資提交到 repo 或公開 issue。

## 4. 想接其他系統？

資料層介面在 `assistant/adapters/provider.py`；可由其他開發者實作 adapter，
但此批不提供 SQL／POS／LINE 匯入器，也不複製商業系統的私人 adapter。
正式使用需另做登入、伺服器決定使用者範圍、資料權限、安全隔離及人工作業確認。
