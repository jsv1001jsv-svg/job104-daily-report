# 金鑰申請指南

> 本檔記錄每一把金鑰「去哪拿、拿到後放哪、怎麼確認拿對了」。
> 寫給日後回來重建環境的人（含未來的自己）—— 這些主控台的介面會改，
> 但**每個值的用途與去向不會變**，看不懂畫面時以「用途」為準。
>
> 最後驗證：2026-08-06

---

## 總覽：一共幾把、現在需要幾把

| 環境變數 | 用途 | 本機開發 | 部署 Modal |
| :--- | :--- | :---: | :---: |
| `LINE_CHANNEL_ACCESS_TOKEN` | 推播日報給使用者 | ✅ 必要 | ✅ |
| `LINE_CHANNEL_SECRET` | 驗證 webhook 請求真的來自 LINE | ✅ 必要 | ✅ |
| `LLM_API_KEY` | 呼叫 LLM 濃縮職缺描述 | ✅ 必要 | ✅ |
| `FIREBASE_SERVICE_ACCOUNT` | 存取雲端 Firestore | ❌ 不需要 | ✅ |
| `GOOGLE_APPLICATION_CREDENTIALS` | 同上，但吃檔案路徑 | ❌ 不需要 | ❌ |

**本機開發只要三把。** Firestore 走 Docker 模擬器，
`docker-compose.yml` 會帶入 `FIRESTORE_EMULATOR_HOST`，SDK 看到它就完全不做認證。

> ⚠️ **兩個 Google，別搞混**
> `LLM_API_KEY`（AI Studio，一串 `AIza...`）與 `GOOGLE_APPLICATION_CREDENTIALS`
> （Firebase 憑證**檔案的路徑**）是完全不同的東西，只是都掛 Google 的名字。
> 把 API key 貼進後者，程式會去找一個叫 `AIza...` 的檔案然後失敗。

---

## 一、Google AI Studio（職缺摘要）

**要拿到的東西**：一把 API key，格式 `AIza` 開頭約 39 字元。

### 步驟

1. 開 https://aistudio.google.com
2. 用 Google 帳號登入（一般個人帳號即可）
3. 左側選單或右上角點 **Get API key**
4. 點 **Create API key**
   - 若問要建在哪個 Google Cloud 專案，選現有的或讓它自動建一個新的，都可以
5. 複製那串 key

### 注意

- **免費、不需要信用卡**，也不必開啟 Google Cloud 計費。
- 免費額度依模型約 **5-15 RPM / 100-1000 RPD**。本專案一個使用者一天最多
  20 次呼叫（`MAX_JOBS_PER_DAY`），個位數使用者綽綽有餘。
- ⚠️ 免費層的請求內容**可能被 Google 用於改善模型**。本專案送出的只有
  104 的公開職缺描述，沒有個資，可以接受。
- key 只會完整顯示一次，離開頁面後多半只看得到前幾碼 —— 當下就存好。

### 放進 `.env`

```
LLM_API_KEY=AIza...
```

`LLM_API_URL` 與 `LLM_MODEL` 不用填，程式內建預設值（見 `src/config.py`）。
要換供應商（OpenRouter / Groq / 自架 LiteLLM）才需要設定它們，見
[CLAUDE.md 第 5.2 節](../../CLAUDE.md)。

---

## 二、LINE Messaging API（推播與 webhook）

**要拿到的東西**：三個值。前兩個進 `.env`，第三個先記著備用。

| 值 | 在哪 | 用途 |
| :--- | :--- | :--- |
| Channel access token | Messaging API 分頁 | 推播訊息的身分 |
| Channel secret | Basic settings 分頁 | 驗證 webhook 簽章 |
| **Your user ID** | Basic settings 分頁 | 你自己的 LINE ID，測試用 |

### 步驟

#### 1. 建立 Provider

1. 開 https://developers.line.biz/console/
2. 用 LINE 帳號登入（就是你手機那個 LINE）
3. 點 **Create a new provider**
4. 名稱隨意（例如你的名字或 `job104`）—— Provider 只是個容器，代表「誰做的」

#### 2. 建立 Messaging API channel

1. 在剛建的 Provider 頁面點 **Create a new channel**
2. 選 **Messaging API** ← ⚠️ **別選成 LINE Login**，那是網站登入用的，沒有推播功能
3. 填必要欄位：
   - **Channel name**：官方帳號顯示的名字（例如「104 職缺日報」）
     ⚠️ 建立後 7 天內不能改名
   - **Channel description**：隨意
   - **Category / Subcategory**：隨意，選最接近的
   - **Email address**：你的信箱
4. 勾選同意條款 → **Create**

> 建立 Messaging API channel 時，LINE 會自動一併建立對應的
> **LINE 官方帳號（Official Account）**。兩者是同一個東西的兩個管理介面：
> Developers Console 管技術設定，Official Account Manager 管行銷功能。

#### 3. 取得 Channel secret

**Basic settings** 分頁 → 找到 `Channel secret` → 複製。

#### 4. 取得 Channel access token

**Messaging API** 分頁 → 捲到最下方 `Channel access token (long-lived)`
→ 點 **Issue** → 複製那串很長的字串。

- 「long-lived」表示不會過期，適合這種長期跑的服務。
- 重新 Issue 會讓舊的失效，別在服務上線後亂按。

#### 5. 抄下 Your user ID

**Basic settings** 分頁最下方 → `Your user ID`，`U` 開頭的 33 字元。

**這個很重要**：它是「你自己」的 LINE 使用者 ID。有了它，就能直接把使用者
資料塞進 Firestore 模擬器測試日報推播，**不需要架設公開的 webhook 網址**。

#### 6. 加自己的官方帳號為好友

**Messaging API** 分頁上有一個 QR code → 用手機 LINE 掃描 → 加好友。

⚠️ **這步不能跳過。** LINE 不允許推播訊息給不是好友的使用者，
沒加好友的話推播會直接失敗。

---

### ⚠️ 最容易卡住的坑：自動回應會攔截訊息

LINE 官方帳號**預設開啟「自動回應訊息」**。這個功能開著的時候，
LINE 平台會自己回覆使用者，**不把訊息轉發給你的 webhook** ——
結果就是「程式明明沒錯，但 webhook 永遠收不到東西」。

**關掉它**：

1. 開 https://manager.line.biz/ （LINE Official Account Manager，與 Developers Console 是**不同**的網站）
2. 選你的官方帳號 → 右上 **設定** → 左側 **回應設定**
3. 把「回應時間」與「非回應時間」都設成 **手動聊天**（不勾自動回應訊息）
4. 確認 **Webhook** 顯示為啟用

> 這一步要等到第二階段（接 webhook）才會用到。
> 第一階段只驗證日報推播，不受影響。

---

### 放進 `.env`

```
LINE_CHANNEL_ACCESS_TOKEN=<Issue 出來的長字串>
LINE_CHANNEL_SECRET=<Basic settings 那個>
```

`Your user ID` **不進 `.env`** —— 它不是金鑰，是測試資料，
用來手動建立第一個使用者。

---

## 三、Firebase（僅部署時需要）

> 本機開發跳過這節。

1. 開 https://console.firebase.google.com → 建立專案
2. 建立 **Firestore Database**（正式模式，地區選 `asia-east1` 台灣或 `asia-northeast1` 東京）
3. 專案設定 → **服務帳戶** → **產生新的私密金鑰** → 下載 JSON 檔

這份 JSON 有兩種用法，**依環境二選一**：

| 環境 | 變數 | 值 |
| :--- | :--- | :--- |
| Modal | `FIREBASE_SERVICE_ACCOUNT` | JSON 檔的**完整內容** |
| 一般機器 | `GOOGLE_APPLICATION_CREDENTIALS` | JSON 檔的**路徑** |

Modal Secrets 只能存環境變數、給不了檔案，所以雲端走前者。
兩者都設時以 `FIREBASE_SERVICE_ACCOUNT` 優先。實作見 `src/store/firestore.py`
的 `build_client()`。

⚠️ 這份 JSON 等同資料庫的完整存取權，**絕不能進版控**。

---

## 四、確認金鑰真的可用

`.env` 填好後：

```bash
docker compose up -d
docker compose exec api python -c "from src.config import get_settings; get_settings().require_production_secrets(); print('三把金鑰齊全')"
```

缺哪一把會一次列出所有缺項。

> 註：`require_production_secrets()` 目前還沒接進任何啟動流程
> （見 CLAUDE.md 第 9 節待辦），所以現階段只能像上面這樣手動呼叫檢查。

---

## 安全須知

- `.env` 已列入 `.gitignore`，**絕不會進版控**。本檔案裡也不該出現任何真實金鑰。
- 金鑰若不小心貼進 commit、聊天室或截圖，**視為已洩漏**，回原主控台重新產生一把。
- 正式環境（Modal）不讀 `.env`，全部走 Modal Secrets。
