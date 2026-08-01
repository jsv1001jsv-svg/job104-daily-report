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

    # --- OpenRouter ---
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.0-flash-001"

    # --- Firebase ---
    firebase_project_id: str = "job104-daily-report"
    firestore_emulator_host: str = ""

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
            "OPENROUTER_API_KEY": self.openrouter_api_key,
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
