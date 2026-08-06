# 104 每日職缺日報自動化

多使用者 LINE 服務：使用者加官方帳號好友、設定「地區 + 職務」條件後，
每天早上 09:00（Asia/Taipei）自動抓取 104 新上架職缺，用 LLM 整理成日報推送。

需求與決策記錄見 [CLAUDE.md](CLAUDE.md)。

---

## 架構：本機 Docker Compose 開發，Modal 雲端部署

核心邏輯（`src/`）與執行平台完全脫鉤，兩個入口共用同一份程式碼：

```
src/                    純 Python，不知道 Modal 或 Docker 存在
  ├── pipeline.py       主流程：抓取 → 去重 → 摘要 → 推播 → 入庫
  ├── config.py         環境變數載入與驗證
  ├── models.py         Job / UserConfig / DailyReport
  ├── scraper/          104 抓取 + 地區代碼對照
  ├── summarizer/       OpenRouter 摘要
  ├── store/            Firestore 存取與去重
  ├── notifier/         LINE 推播
  └── webhook/          FastAPI，接 LINE 事件

modal_app.py            ← 雲端入口（Modal Cron + Web Endpoint）
scheduler/run.py        ← 本機入口（APScheduler 模擬 cron）
docker-compose.yml      ← 本機開發環境
```

為什麼這樣分：日報必須每天準時送達，綁在本機電腦的開機狀態上不可靠，
所以正式環境走 Modal；但本機用 Compose 開發除錯比每次部署到雲端快得多。

---

## 快速開始

需要 Docker Desktop。

```bash
# 1. 準備環境變數
cp .env.example .env
#    本機只需三把金鑰：LINE_CHANNEL_ACCESS_TOKEN / LINE_CHANNEL_SECRET / LLM_API_KEY
#    申請步驟見 docs/setup/credentials.md（Firestore 走模擬器，不需憑證）

# 2. 啟動
docker compose up -d

# 3. 驗證
curl http://localhost:8100/health     # 應回 {"status":"ok"}
docker compose ps                     # 三個服務都應為 Up
```

三個服務：

| 服務 | 用途 | 本機位址 |
| :--- | :--- | :--- |
| `api` | FastAPI，接 LINE webhook | http://localhost:8100 |
| `scheduler` | APScheduler，每日 09:00 觸發日報 | 無對外 port |
| `firestore` | Firebase 模擬器，本機資料庫 | localhost:8181 |

> Port 用 `.env` 的 `API_PORT` / `FIRESTORE_PORT` 控制。
> 預設避開 8000 / 8080，因為這兩個常被其他專案佔用。

---

## 常用指令

```bash
# 開發
docker compose up -d                  # 背景啟動
docker compose logs -f api            # 追 log
docker compose exec api bash          # 進容器
docker compose down                   # 停止
docker compose down -v                # 停止並清空 Firestore 資料（破壞性）

# 測試與品質
docker compose exec api pytest        # 全部測試 + 覆蓋率
docker compose exec api pytest tests/unit/test_area_map.py -v
docker compose exec api ruff check .  # lint
docker compose exec api ruff check . --fix

# 不等到 09:00，立刻跑一次日報
docker compose exec scheduler python -c \
  "import asyncio; from src.pipeline import run_daily_report; print(asyncio.run(run_daily_report()))"

# 或讓 scheduler 容器一啟動就跑一次
RUN_ON_STARTUP=1 docker compose up scheduler
```

改程式碼**不需要**重 build —— 專案目錄用 volume 掛進容器，
`uvicorn --reload` 會自動重啟。只有改 `requirements.txt` 才需要 `docker compose build`。

---

## 現況

**已完成**：Docker 開發環境、專案骨架、領域模型、地區解析、LINE 訊息排版、webhook 簽章驗證。

**未完成**：104 API 欄位驗證、Firestore 整合測試、Modal 部署、各項服務帳號申請。

詳細進度與待辦見 [CLAUDE.md](CLAUDE.md) 的「開發紀錄」與「申請進度」。

---

## 注意事項

- **金鑰**：`.env` 已列入 `.gitignore`，絕不進版控。正式環境用 Modal Secrets。
- **104 抓取**：程式內建請求間隔（`SCRAPE_DELAY_SECONDS`）與每日筆數上限，一天只跑一次。
- **LINE 免費額度**：官方帳號免費方案每月推播則數有限，約可服務個位數使用者。
