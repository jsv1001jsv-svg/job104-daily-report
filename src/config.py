"""應用設定：從環境變數載入並在啟動時驗證。

設定缺失要在「啟動時」就爆掉，而不是等到早上 9 點推播失敗才發現。
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """所有環境變數的單一來源，欄位名對應 .env.example。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LINE ---
    line_channel_access_token: str = ""
    line_channel_secret: str = ""

    # --- LLM（摘要）---
    # 刻意用中性命名：任何 OpenAI 相容的 /chat/completions 端點都能直接換上，
    # 換供應商只需改這三個環境變數，程式碼不動。
    # 預設是 Google AI Studio —— 免費額度不需信用卡，且繁體中文品質穩定。
    llm_api_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    llm_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"

    # --- Firebase ---
    firebase_project_id: str = "job104-daily-report"
    firestore_emulator_host: str = ""
    # service account JSON 的**內容**（不是檔案路徑）。
    # Modal Secrets 只能給環境變數，給不了檔案，所以雲端走這條；
    # 本機留空即可 —— 模擬器不需要憑證。見 src/store/firestore.py。
    firebase_service_account: str = ""

    # --- 應用 ---
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_jobs_per_day: int = Field(default=20, ge=1, le=100)
    scrape_delay_seconds: float = Field(default=1.5, ge=0.5)

    @property
    def use_emulator(self) -> bool:
        """是否連 Firestore 模擬器（本機開發）而非雲端。"""
        return bool(self.firestore_emulator_host)

    def require_production_secrets(self) -> None:
        """正式環境啟動前檢查：缺任何一把金鑰就拒絕啟動。

        Raises:
            RuntimeError: 有必要金鑰未設定，訊息會列出所有缺少的項目。
        """
        required = {
            "LINE_CHANNEL_ACCESS_TOKEN": self.line_channel_access_token,
            "LINE_CHANNEL_SECRET": self.line_channel_secret,
            "LLM_API_KEY": self.llm_api_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(
                f"缺少必要環境變數：{', '.join(missing)}。"
                "本機請填寫 .env；正式環境請設定 Modal Secrets。"
            )


@lru_cache
def get_settings() -> Settings:
    """取得設定單例。加 lru_cache 避免每次呼叫都重讀 .env。"""
    return Settings()
