# CLAUDE.md — 104 每日職缺日報自動化（多使用者版）

> 本檔記錄專案上下文，供後續開發（含 Claude Code）參考。
> 狀態：**需求已確認，進入申請 / 建置階段**。
> 最後更新：2026-07-28

---

## 1. 專案目標

一個**多使用者**的 LINE 服務：任何人加入官方帳號、設定自己的搜尋條件後，系統每個**早上 9:00（Asia/Taipei，平日與週末皆執行）**自動到 **104 人力銀行** 抓取符合條件的職缺，用 LLM 整理成「每日職缺日報」，透過 **LINE Messaging API** 分別推送給各使用者。

日報每筆職缺需含：**工作內容、條件（需求）、福利、薪資、地點**（外加標題、公司、原始連結）。

---

## 2. 核心規則（已確認）

- **搜尋條件依使用者自訂**：以「地區 + 職務關鍵字」為主（例：「台北 後端工程師」「新北 AI 工程師」）。系統把自然語言條件轉成 104 搜尋參數（keyword + area code）。
- **去重（只推新職缺）**：每天比對 Firebase 中「已看過的職缺 ID」，只呈現**今天新出現**的職缺。舊職缺保留在 Firebase，供使用者日後查閱。
  - 例：7/28 抓到 20 筆全部入庫；7/29 抓到 30 筆，其中 10 筆是新的 → 只推那 10 筆。
- **每日呈現數量**：最多 **20 筆**新職缺；不足則有幾筆推幾筆；**完全沒有**則推「今日已無新職缺」提示。
- **發送時間**：每天 **09:00（Asia/Taipei）**，平日 + 週末。
- **收件對象**：**任意使用者**（多使用者，各自條件、各自去重清單）。

---

## 3. 系統架構

```
[使用者端]
  加官方帳號好友 ──► LINE 送 follow 事件 ──► Webhook 存 userId
  傳「地區 職務」 ──► LINE 送 message 事件 ─► Webhook 存該使用者搜尋條件
                                                    │
                                              (Firebase 使用者表)
[每日排程]
  Modal Cron（09:00 Asia/Taipei）
        │  讀取所有使用者 + 各自搜尋條件
        ▼
  [抓取] 104 搜尋 JSON API（HTTP 帶 UA/Referer 為主；Playwright 備援）
        │
        ▼
  [去重] 與 Firebase 該使用者「已看過職缺 ID」比對 → 取新職缺
        │
        ▼
  [整理] OpenRouter LLM 濃縮成固定欄位（省錢模型）
        │
        ▼
  [推送] LINE push message → 各使用者 userId
        │
        ▼
  [入庫] 新職缺 ID 寫回 Firebase
```

---

## 4. 技術棧

| 環節 | 選型 | 備註 |
|------|------|------|
| 排程 | **Modal** `modal.Cron`，時區 Asia/Taipei | 每天 09:00 |
| Webhook | **Modal Web Endpoint**（FastAPI） | 接 LINE follow / message 事件，登記使用者與條件 |
| 抓取 | **HTTP 請求（httpx）為主**，Playwright 備援 | 104 有 JSON API |
| 資料庫 | **Firebase（Firestore）** | 使用者表 + 各使用者已看過職缺 ID |
| 摘要 | **OpenRouter**（省錢模型，型號待定） | 濃縮職缺描述 |
| 推送 | **LINE Messaging API** push message | LINE Notify 已停用（見註記） |

---

## 5. 關鍵決策與注意事項

- ⚠️ **LINE Notify 已於 2025/3/31 停用**，改用 **Messaging API + 官方帳號**。
- **免費額度成本**：LINE 官方帳號免費方案每月推播則數有限（約 200~500 則/月，依方案）。1 使用者 × 1 則/天 × 30 天 ≈ 30 則/人月 → 免費方案約可服務個位數~十幾位使用者，之後需升級方案或分流。
- **104 抓取**：以 JSON API 為主，帶合理 `User-Agent` 與 `Referer`；低頻（一天一次）、當好公民。
- **一個官方帳號只能設一個 Webhook URL**：多使用者共用同一個 Webhook 端點處理所有事件。
- **金鑰不寫進程式碼**：全部放 **Modal Secrets**。

---

## 6. 環境變數 / Secrets（存於 Modal Secrets）

```
LINE_CHANNEL_ACCESS_TOKEN   # LINE Messaging API（long-lived）
LINE_CHANNEL_SECRET         # 驗證 webhook 簽章
OPENROUTER_API_KEY          # OpenRouter
FIREBASE_SERVICE_ACCOUNT    # Firebase service account JSON（Firestore 存取）
```

---

## 7. Firestore 資料結構（初版規劃）

```
users/{userId}
  ├─ area_keyword: "台北 後端工程師"   # 使用者輸入的原始條件
  ├─ area_code:    "6001000000"        # 對應 104 area 參數
  ├─ keyword:      "後端工程師"
  ├─ created_at
  └─ seen_jobs/{jobId}                 # 子集合：已看過的職缺
        ├─ title, company, url
        └─ first_seen_at
```

---

## 8. 申請進度（Setup Checklist）

- [ ] **LINE**：建立官方帳號 → 啟用 Messaging API → 取得 Channel access token + Channel secret ← **進行中**
- [ ] **OpenRouter**：註冊 → 取得 API key → 選省錢模型
- [ ] **Firebase**：建立專案 → 開 Firestore → 產生 service account 金鑰
- [ ] **Modal**：註冊 → 安裝 CLI → 建立 Secrets → 部署 webhook + cron

---

## 9. 待確認 / 待決定

1. **OpenRouter 省錢模型**：實作前挑一個當下便宜且中文摘要品質可接受的型號。
2. **使用者設定條件的 UX**：預設用「傳訊息給官方帳號（例：台北 後端工程師）」來設定，之後可加指令（如「/set」「/stop」）。
3. **地區對應表**：需建立「城市名稱 → 104 area code」對照（先支援六都 + 常見縣市）。

---

## 10. 建議目錄結構（尚未建立）

```
job-daily-report/
├── CLAUDE.md
├── modal_app.py        # Modal 進入點：cron + web endpoint(webhook)
├── webhook.py          # 處理 LINE follow/message 事件，登記使用者
├── scraper.py          # 104 抓取（JSON API / 備援）
├── area_map.py         # 城市 → 104 area code 對照
├── summarizer.py       # OpenRouter 摘要
├── notifier.py         # LINE push message
├── store.py            # Firebase(Firestore) 存取 + 去重
├── config.py           # 時間、數量上限等設定
└── requirements.txt
```
