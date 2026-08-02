# CLAUDE.md — 104 每日職缺日報自動化（多使用者版）

> 本檔記錄專案上下文，供後續開發（含 Claude Code）參考。
> 狀態：**核心流程全部打通並測試（覆蓋率 99%）。
> 104 抓取已可用，剩下的是申請各服務帳號與雲端部署**。
> 最後更新：2026-08-02

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
  [抓取] 104 搜尋 JSON API（curl_cffi，模仿瀏覽器 TLS 指紋通過 Cloudflare）
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
| 抓取 | **curl_cffi**（模仿瀏覽器 TLS 指紋） | Cloudflare 擋的是 TLS 指紋不是 header，見 5.1 |
| 資料庫 | **Firebase（Firestore）** | 使用者表 + 各使用者已看過職缺 ID |
| 摘要 | **OpenRouter**（省錢模型，型號待定） | 濃縮職缺描述 |
| 推送 | **LINE Messaging API** push message | LINE Notify 已停用（見註記） |

---

## 5. 關鍵決策與注意事項

- ⚠️ **LINE Notify 已於 2025/3/31 停用**，改用 **Messaging API + 官方帳號**。
- **免費額度成本**：LINE 官方帳號免費方案每月推播則數有限（約 200~500 則/月，依方案）。1 使用者 × 1 則/天 × 30 天 ≈ 30 則/人月 → 免費方案約可服務個位數~十幾位使用者，之後需升級方案或分流。
- **104 抓取**：✅ 已可用。JSON API 的 endpoint / 參數 / 欄位見 `src/scraper/client.py`。
  `www.104.com.tw` 有 Cloudflare bot 防護，但它是在 **TLS handshake** 層辨識機器人
  （JA3/JA4 指紋），不是看 header —— 所以 httpx 補再多 header 都是 403，而
  `curl_cffi` 模仿 Chrome 的 handshake 就直接通過，**不需要瀏覽器**。見 5.1。
  例外：`static.104.com.tw` 的分類表（Area / JobCat）本來就沒有防護。
- **104 官方無公開職缺 API**：`developers.104.com.tw` 只提供 B2B（履歷傳輸、職缺刊登），
  需企業客戶身分，且沒有任何一支能讓第三方讀取公開職缺。爬蟲無法用官方管道取代。
- 低頻（一天一次）、有筆數上限、帶可辨識 UA，當好公民。
- **一個官方帳號只能設一個 Webhook URL**：多使用者共用同一個 Webhook 端點處理所有事件。
- **金鑰不寫進程式碼**：全部放 **Modal Secrets**。

### 5.1 抓取策略：Cloudflare 擋的是 TLS 指紋

> 這一節解釋「我們在做什麼、為什麼這樣做」，寫給日後回來看的人（含未來的自己）。
> 決策日期：2026-08-02（推翻 2026-08-01 的 Playwright 方案）。

#### 遇到的問題

104 的職缺資料是靠 JSON API 傳給網頁的，那兩支 API 的網址、參數、回應欄位
都已驗證清楚（見 `src/scraper/client.py`）。照理說用 Python 直接發個 HTTP
請求就能拿到資料。

但實測失敗：`www.104.com.tw` 前面擋著一層 **Cloudflare 的機器人防護**。
它判定請求來自程式就回 403，內容是一頁「Just a moment...」的驗證頁。

#### 關鍵發現：問題不在 header，在 TLS

一開始的假設是「請求看起來不像瀏覽器」，所以往 header 上補：User-Agent、
Referer、Accept-Language⋯⋯ **全部無效**。後來改用 Playwright 開真的
Chromium，文件頁能過挑戰，但**頁面自己發出的 API 請求照樣 403**。

那條線索其實已經指出答案了：如果連真瀏覽器發的請求都被擋，問題就不在
「請求長什麼樣」。**真正的判別依據是 TLS handshake 的指紋（JA3/JA4）。**

每個 HTTP 客戶端在建立加密連線時，會送出一組自己特有的參數組合 ——
支援哪些加密套件、以什麼順序排列、帶哪些擴充欄位。這組合像指紋一樣，
Python 的 TLS 函式庫和 Chrome 差很多，Cloudflare 比對一下就知道對面不是瀏覽器。
**這一切發生在任何 header 被送出之前**，所以 header 補得再完整都沒有意義。

#### 解法：curl_cffi

`curl_cffi` 是 curl-impersonate 的 Python 綁定，它會**照著 Chrome 的方式做
TLS handshake**——用相同的加密套件順序、相同的擴充欄位、相同的 HTTP/2 設定。
對 Cloudflare 來說指紋就是 Chrome 的，於是直接放行。

用法上和 requests / httpx 幾乎一樣，關鍵只有一個參數：

```python
AsyncSession(impersonate="chrome")   # 就這樣，其餘照常
```

⚠️ `impersonate` 是**整個傳輸層唯一不能拿掉的設定**。拿掉就退回 Python 原生
指紋、全數 403。`src/scraper/transport.py` 裡標了註解，並有一個迴歸測試
（`test_一定要帶上瀏覽器指紋`）盯著它。

實測結果（2026-08-02）：

| 客戶端 | 搜尋 API | 詳情 API |
| :--- | :--- | :--- |
| httpx（Python 原生指紋） | ❌ 403 挑戰頁 | ❌ 403 |
| Playwright 真瀏覽器 | ❌ 403 | ❌ 403 |
| **curl_cffi（Chrome 指紋）** | ✅ 31 筆／頁，共 59 頁 1767 筆 | ✅ 條件與福利齊全 |

連續 5 次搜尋 5/5 成功，翻頁正常且無重複。

#### 為什麼這比 Playwright 好得多

Playwright 方案曾經被接受過（見下方「歷史」），換成 curl_cffi 後三項代價全消失：

| 項目 | Playwright | curl_cffi |
| :--- | :--- | :--- |
| Docker image | +1GB（Chromium 及其相依函式庫） | +幾 MB |
| 每次執行 | 多花 10~30 秒啟動瀏覽器與等頁面 | 與一般 HTTP 請求相同 |
| Modal 費用 | 記憶體與執行時間高一個量級 | 一般水準 |
| 可靠度 | 挑戰頁行為會變，且**實際上根本沒成功** | 已端到端驗證 |

#### 架構分層

傳輸與解析刻意分開，這次換掉整個傳輸層時 `client.py` 一行都不用改：

```
transport.py  傳輸  —— 通過 Cloudflare、把 JSON 拿到手（curl_cffi）
client.py     解析  —— 把 104 的 JSON 轉成 Job（純函式，不發請求）
fetcher.py    流程  —— 搜尋 → 逐筆補詳情，處理部分失敗
```

#### 被否決的方案

- **純 httpx**：實測 403，且原因是架構性的（TLS 層），不是調參數能解決。
- **Playwright**：已實作又移除。除了代價高，**實測根本沒通過**——
  文件頁能過挑戰，API 請求仍 403。
- **手動複製 `cf_clearance` cookie**：那個 cookie 會過期、綁 IP 與瀏覽器指紋，
  等於每隔一段時間要人工介入。自動化服務不該有需要人手動續命的環節。
- **`mobile.104.com.tw`**：2026-08-02 探測，這個子網域確實存在且沒有 Cloudflare，
  但不論什麼路徑都只回同一份「版本不存在」的公告 JSON —— 它是 App 的版本檢查
  端點，不是職缺 API。已排除。
- **改用台灣就業通開放 API**：免費合法且實測可用，但職缺池與 104 差太多
  （政府就業服務站為主），撐不起日報的數量。列為日後的補充來源，不是替代方案。

#### 這件事的教訓

2026-08-01 花了一整天在 Playwright 上，方向是錯的。錯在**沒有先問「被擋的
原因是什麼」就開始找「更強的工具」**——從 httpx 換到瀏覽器，是把賭注加大，
而不是把問題查清楚。

真正有用的那條線索（真瀏覽器發的請求也被擋）當天就已經觀察到了，
但當時被當成「瀏覽器還不夠像真人」，於是繼續往反偵測的方向加碼。
如果那時停下來問一句「連真瀏覽器都被擋，那被檢查的到底是什麼？」，
答案（TLS 層）會來得快很多。

#### 歷史：曾經採用的 Playwright 方案

保留這段是為了讓日後看到 git 歷史裡的 `browser.py` 時知道它為什麼存在、
又為什麼消失。當時的推論鏈是：httpx 被擋 → 需要真瀏覽器通過挑戰 →
用 Playwright 攔截頁面自己發的 XHR。實作完成、單元測試通過，
但**實際連線從未成功**，API 一律 403。詳見 `docs/devlog/2026-08-01.md`。

#### 這件事不影響的部分

`static.104.com.tw` 上的分類表（`Area.json` / `JobCat.json`）本來就沒有
Cloudflare，純 HTTP 就抓得到，地區代碼與職務代碼那部分完全不受影響。

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

1. ~~**104 API 實際格式**~~ ✅ 2026-08-01 完成驗證，已寫入 `src/scraper/client.py`。
2. ~~**地區對應表代碼值**~~ ✅ 2026-08-01 對 104 官方 `Area.json` 驗證，原本 20 筆錯 16 筆。
3. ~~**Cloudflare 繞過方式**~~ ✅ 2026-08-02 解決。Cloudflare 擋的是 TLS 指紋，
   改用 `curl_cffi` 模仿 Chrome 的 handshake 即可，不需要瀏覽器。
   Playwright 那層已整個移除。完整脈絡見 **第 5.1 節**。
4. **keyword vs jobcat 搜尋**：`jobcat`（如後端工程師 = 2007001016）比關鍵字精準，
   關鍵字會誤中標題含該字串的無關職缺。代價是使用者的自然語言輸入要多一層對應到職務代碼。
   `search_jobs()` 兩種都支援，預設用 keyword，尚未決定正式採用哪個。
5. **台灣就業通 API 是否納入為補充來源**：勞動部開放資料，免費免申請、實測可用，
   但職缺池與 104 差異大（政府就業服務站為主），且資料集標示更新頻率「每 1 年」待查證。
6. **OpenRouter 省錢模型**：實作前挑一個當下便宜且中文摘要品質可接受的型號。
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
│   │   ├── transport.py    #   傳輸：curl_cffi 通過 Cloudflare
│   │   ├── client.py       #   解析：104 JSON → Job（純函式）
│   │   ├── fetcher.py      #   流程：搜尋 → 逐筆補詳情
│   │   └── area_map.py     #   城市 → area code
│   ├── summarizer/openrouter.py
│   ├── store/firestore.py  #   Repository 模式，Firestore 細節封在這
│   ├── notifier/line.py    #   LINE push + 訊息排版
│   └── webhook/
│       ├── app.py          #   FastAPI + 簽章驗證
│       └── handlers.py     #   follow / message 事件處理
│
├── tests/unit/             # 171 個測試，覆蓋率 99%
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

### 2026-08-02（下午）— Cloudflare 解決，Playwright 整層移除

詳見 [docs/devlog/2026-08-02.md](docs/devlog/2026-08-02.md)。

**突破**：Cloudflare 擋的是 **TLS handshake 指紋（JA3/JA4）**，不是 header。
改用 `curl_cffi`（模仿 Chrome 的 handshake）純 HTTP 直接通過，**不需要瀏覽器**。
完整脈絡與「為什麼昨天方向錯了」見第 5.1 節。

**完成**
- 新增 `src/scraper/transport.py`（傳輸層），端到端對真實 104 驗證通過
- 移除 `browser.py`、Playwright 依賴、Dockerfile 的 Chromium 安裝層
- `client.py` 收斂成純解析函式，不再發任何請求
- 測試 169 → 171，覆蓋率 89% → **99%**

**代價全消失**（Docker 重 build 實測）
| 項目 | Playwright | curl_cffi |
| :--- | ---: | ---: |
| api image | 2.13 GB | **471 MB** |
| 每次執行 | +10~30 秒 | 一般 HTTP |
| 實際可用 | ❌ 從未成功 | ✅ 已驗證 |

**已驗證 Linux 容器內同樣可用**（`production` 階段以非 root 實跑抓到職缺），
Modal 部署的前提成立 —— 先前的驗證都在 Windows host，而 TLS 指紋依賴
底層函式庫，跨 OS 必須實測而非推論。

### 2026-08-02（上午）— 繞開抓取，補完其他環節的測試

詳見 [docs/devlog/2026-08-02.md](docs/devlog/2026-08-02.md)。

**決策**：Cloudflare 先擱置。抓取成不成功，pipeline / summarizer / store /
webhook 的邏輯都不會變，這些模組的驗證不該被一個外部封鎖擋住。

**完成**
- 測試 79 → 169 通過，覆蓋率 54% → **89%**，首次通過 80% 門檻
- `pipeline` / `summarizer` 從 0% 補到 100%；除 `browser.py` 外所有模組 ≥98%
- 釘住幾條「錯了會造成資料遺失」的規則：推播成功才標記已看過、
  單一使用者失敗不影響其他人、webhook 處理失敗仍回 200

**刻意不做**
- `browser.py` 維持 35%。它需要真實 Chromium 與 Cloudflare 挑戰才有意義，
  用假物件測等於測自己寫的假物件；且該模組行為都還沒定案。

**踩到的坑**
| 問題 | 原因 | 解法 |
| :--- | :--- | :--- |
| `job.__dict__` 拋 AttributeError | `dataclass(slots=True)` 沒有實例字典 | 用 `dataclasses.replace()` |
| 測試讀到本機 `.env`，換台機器就壞 | `Settings` 預設 `env_file=".env"` | 測試用 `Settings(_env_file=None, ...)` |

### 2026-08-01（下午）— 104 API 驗證完成

**完成**
- 新增 `scripts/probe_104_api.py`：探測工具，打真實 endpoint 並印出原始回應
- 搜尋與詳情兩支 API 全部驗證，`client.py` 依真實欄位重寫
- `area_map.py` 20 筆地區代碼改用 104 官方 `Area.json` 的值
- 測試 31 → 79 通過，覆蓋率 44% → 54%

**推翻的假設**
| 原本以為 | 實際 |
| :--- | :--- |
| httpx 帶 UA/Referer 就能抓 | ❌ Cloudflare 擋，403「Just a moment...」 |
| 回應是 `data.list` | `data` 直接就是陣列 |
| 詳情是 `/job/ajax/content/{id}` | 是 `/api/jobs/{slug}`（舊教學已過時） |
| `jobNo` 是職缺 ID | 詳情 API 只吃**網址短碼**，兩套 ID 不通用 |
| area code 大致正確 | 20 筆錯 16 筆，且 104 不分新竹縣市／嘉義縣市 |

**發現**
- `static.104.com.tw/category-tool/json/{Area,JobCat,Indust}.json` 是公開靜態分類表，
  **沒有 Cloudflare**，可直接抓。原始檔存於 `docs/api-samples/`。
- 104 無公開職缺 API；台灣就業通（勞動部）有免費開放 API 可作補充來源。

### 2026-08-01（上午）— 專案骨架與 Docker 開發環境

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
