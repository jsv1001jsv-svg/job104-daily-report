"""FastAPI webhook：接 LINE 的 follow / message 事件，登記使用者與搜尋條件。

一個官方帳號只能設一個 webhook URL，所有使用者的事件都進這個端點。

安全：LINE 的請求一律驗證 X-Line-Signature（HMAC-SHA256）。
沒驗簽章的話，任何人都能偽造請求灌爆你的 Firestore。
"""

import base64
import hashlib
import hmac
import logging

from fastapi import FastAPI, Header, HTTPException, Request, status

from src.config import get_settings
from src.webhook.handlers import handle_event

logger = logging.getLogger(__name__)

app = FastAPI(title="104 每日職缺日報 Webhook", docs_url=None, redoc_url=None)


@app.get("/health")
async def health() -> dict[str, str]:
    """存活檢查。Docker healthcheck 與監控用。"""
    return {"status": "ok"}


@app.post("/callback")
async def callback(
    request: Request,
    x_line_signature: str = Header(default=""),
) -> dict[str, str]:
    """LINE webhook 進入點。

    Raises:
        HTTPException: 401 簽章驗證失敗。
    """
    body = await request.body()

    if not _verify_signature(body, x_line_signature):
        logger.warning("簽章驗證失敗，拒絕請求")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature",
        )

    payload = await request.json()

    # LINE 要求 webhook 快速回 200，處理邏輯不可拖太久
    for event in payload.get("events", []):
        try:
            await handle_event(event)
        except Exception:
            # 單一事件失敗不影響同批其他事件，也不能讓 LINE 收到 500 而重送
            logger.exception("處理事件失敗：%s", event.get("type"))

    return {"status": "ok"}


def _verify_signature(body: bytes, signature: str) -> bool:
    """驗證請求確實來自 LINE。

    Args:
        body: 原始 request body（必須是未經解析的 bytes）。
        signature: X-Line-Signature header 的值。

    Returns:
        簽章正確為 True。
    """
    secret = get_settings().line_channel_secret
    if not secret or not signature:
        return False

    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")

    # 用 compare_digest 而非 == ，避免時序攻擊
    return hmac.compare_digest(expected, signature)
