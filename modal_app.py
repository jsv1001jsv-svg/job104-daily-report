"""Modal 部署入口（正式環境）。

刻意保持極薄 —— 所有邏輯都在 src/ 裡，這個檔案只負責「什麼時候跑」與「怎麼對外」。
這樣本機（Docker Compose）與雲端（Modal）跑的是同一份核心程式碼。

部署：
    modal deploy modal_app.py

部署後 Modal 會給一個公開網址，把 `<url>/callback` 填進
LINE Developers Console 的 Webhook URL。

⚠️ 尚未部署驗證，待 Modal 帳號與 Secrets 設定完成後測試。
"""

import modal

# 用同一份 Dockerfile 定義雲端環境，避免本機與雲端環境漂移
image = modal.Image.from_dockerfile("Dockerfile", stage="production")

app = modal.App("job104-daily-report", image=image)

# 金鑰全部走 Modal Secrets，不進程式碼也不進 image
secrets = [modal.Secret.from_name("job104-secrets")]


@app.function(
    schedule=modal.Cron("0 9 * * *", timezone="Asia/Taipei"),
    secrets=secrets,
    timeout=900,  # 使用者多時整批要跑一陣子，給 15 分鐘
)
async def daily_report() -> dict[str, int]:
    """每天 09:00 (Asia/Taipei) 為所有使用者產生並推送日報。"""
    from src.config import get_settings
    from src.pipeline import run_daily_report

    # 先檢查金鑰再開工。Modal Secrets 設錯時，這裡會立刻以清楚的訊息失敗，
    # 而不是等抓完 104、燒完 LLM 額度之後才在推播那一步爆掉。
    get_settings().require_production_secrets()

    return await run_daily_report()


@app.function(secrets=secrets)
@modal.asgi_app()
def webhook():
    """LINE webhook 端點。Modal 會給這個函式一個公開 https 網址。"""
    from src.webhook.app import app as fastapi_app

    return fastapi_app
