# CLAUDE.md — 104 每日職缺日報自動化（多使用者版）

> 本檔記錄專案上下文，供後續開發（含 Claude Code）參考。
> 狀態：**專案骨架與 Docker 開發環境已建立，待驗證 104 API**。
> 最後更新：2026-08-01

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
| 本機開發 | **Docker Compose**（api / scheduler / firestore 三服務） | 見第 11 節 |
| 排程 | **Modal** `modal.Cron`，時區 Asia/Taipei | 每天 09:00；本機由 APScheduler 模擬 |
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

1. **104 API 實際格式**（🔴 最高優先，卡住 scraper / summarizer / pipeline 的測試）：
   endpoint、參數名、回應欄位路徑目前皆為推測，須開 DevTools Network 實測後修正。
2. **地區對應表代碼值**：`src/scraper/area_map.py` 的 area code 尚未驗證，
   須逐一勾選 104 各縣市、比對網址 `area=` 參數。查找**邏輯**已完成並有測試。
3. **OpenRouter 省錢模型**：實作前挑一個當下便宜且中文摘要品質可接受的型號。
   目前預設 `google/gemini-2.0-flash-001`。
4. **使用者設定條件的 UX**：目前用「傳訊息給官方帳號（例：台北 後端工程師）」，
   之後可加指令（如「/set」「/stop」）。

---

## 10. 目錄結構（已建立）

核心邏輯與執行平台脫鉤，`modal_app.py`（雲端）與 `scheduler/run.py`（本機）
都只是薄入口，共用同一份 `src/`。日後換平台只需換入口檔。

```
C:\Project\
├── CLAUDE.md               # 本檔：需求、決策、開發紀錄
├── README.md               # 怎麼跑起來
├── docker-compose.yml      # 本機三服務
├── Dockerfile              # 多階段：builder / dev / production
├── .env.example            # 環境變數範本（.env 不進版控）
├── requirements.txt        # 執行依賴（版本鎖定）
├── requirements-dev.txt    # 測試與品質工具
├── pyproject.toml          # pytest / coverage / ruff 設定
│
├── modal_app.py            # ← 雲端入口：Modal Cron + Web Endpoint
├── scheduler/run.py        # ← 本機入口：APScheduler 模擬 cron
│
├── src/                    # 平台無關的核心
│   ├── config.py           #   環境變數載入與驗證
│   ├── models.py           #   Job / UserConfig / DailyReport（全 frozen）
│   ├── pipeline.py         #   主流程：抓取→去重→摘要→推播→入庫
│   ├── scraper/
│   │   ├── client.py       #   104 JSON API
│   │   └── area_map.py     #   城市 → area code
│   ├── summarizer/openrouter.py
│   ├── store/firestore.py  #   Repository 模式，Firestore 細節封在這
│   ├── notifier/line.py    #   LINE push + 訊息排版
│   └── webhook/
│       ├── app.py          #   FastAPI + 簽章驗證
│       └── handlers.py     #   follow / message 事件處理
│
├── tests/unit/             # 31 個測試
└── docs/devlog/            # 每日詳細開發紀錄
```

> 與原規劃（9 個 .py 平鋪）的差異：改為依領域分套件，符合
> `~/.claude/rules/coding-style.md` 的「依功能組織、高內聚低耦合」。

---

## 11. 本機開發環境（Docker Compose）

**部署架構決策**：本機用 Compose 開發，正式環境部署到 Modal。
理由與被否決的方案見 `docs/devlog/2026-08-01.md`。

| 服務 | 用途 | 本機位址 |
| :--- | :--- | :--- |
| `api` | FastAPI，接 LINE webhook | http://localhost:8100 |
| `scheduler` | APScheduler，每日 09:00 觸發 | 無對外 port |
| `firestore` | Firebase 模擬器 | localhost:8181 |

```bash
cp .env.example .env          # 填金鑰
docker compose up -d
curl http://localhost:8100/health
docker compose exec api pytest
```

⚠️ Host port 預設 8100 / 8181，避開被其他專案佔用的 8000 / 8080。
改 port 只需改 `.env` 的 `API_PORT` / `FIRESTORE_PORT`。

完整指令見 [README.md](README.md)。

---

## 12. 開發紀錄

> 精簡條目。詳細除錯過程見 `docs/devlog/YYYY-MM-DD.md`。

### 2026-08-01 — 專案骨架與 Docker 開發環境

詳見 [docs/devlog/2026-08-01.md](docs/devlog/2026-08-01.md)。

**完成**
- 決定部署架構：Compose 開發 + Modal 部署（三方案比較後選定）
- Docker 三服務環境跑通：`api` / `scheduler` / `firestore` 皆 Up，`/health` 回 200
- 專案骨架：`src/` 依領域分套件，核心邏輯與平台脫鉤
- 31 個單元測試通過，ruff 全過

**踩到的坑**
| 問題 | 原因 | 解法 |
| :--- | :--- | :--- |
| 測試檔 SyntaxError | 中文函式名混入空白 `test_缺少簽章 header 被拒絕` | 改底線分隔 |
| ruff 報 27 個 N802 | 中文沒有大小寫，規則不適用 | `tests/**` 加 per-file-ignores |
| Compose 起不來，port 8080 被佔 | 另一專案 `stock-information-platform` 佔 8000；wslrelay 佔 8080 | host port 改用 `${API_PORT:-8100}` 變數 |

**未達標項目（誠實記錄）**
- 測試覆蓋率 **44%**，未達 80% 門檻。`fail_under = 80` 保留未調降。
  未覆蓋的是 scraper / summarizer / store / pipeline —— 這些依賴外部 API 的
  真實回應格式，驗證前寫測試等於測自己的猜測。
- `src/scraper/client.py` 與 `area_map.py` 的代碼值標為 ⚠️ 待驗證，尚不可用。

**下一步**：驗證 104 API 實際格式 → 補 scraper 測試 → 申請各服務帳號
