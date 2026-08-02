"""設定載入與啟動檢查測試。

設定的價值在於「壞得夠早」——缺金鑰要在啟動時就爆，
而不是等到早上 9:00 推播時才發現。
"""

import pytest

from src.config import Settings, get_settings

_ALL_SECRETS = {
    "LINE_CHANNEL_ACCESS_TOKEN": "token",
    "LINE_CHANNEL_SECRET": "secret",
    "OPENROUTER_API_KEY": "key",
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch):
    """清掉所有相關環境變數與設定快取，避免測試互相污染或讀到本機 .env。"""
    for name in (*_ALL_SECRETS, "FIRESTORE_EMULATOR_HOST", "MAX_JOBS_PER_DAY"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _settings(**overrides: str) -> Settings:
    """繞過 .env，只用明確給定的值 —— 本機 .env 有值會讓測試結果不穩定。"""
    defaults = {
        "line_channel_access_token": "",
        "line_channel_secret": "",
        "openrouter_api_key": "",
    }
    return Settings(_env_file=None, **{**defaults, **overrides})


class TestRequireProductionSecrets:
    def test_金鑰齊全時通過(self) -> None:
        settings = _settings(
            line_channel_access_token="t",
            line_channel_secret="s",
            openrouter_api_key="k",
        )

        settings.require_production_secrets()  # 不拋例外即通過

    def test_缺少金鑰時列出全部缺項(self) -> None:
        """一次列完，免得填一個重啟一次才發現還缺下一個。"""
        with pytest.raises(RuntimeError) as exc:
            _settings().require_production_secrets()

        message = str(exc.value)
        for name in _ALL_SECRETS:
            assert name in message

    def test_只缺一個時只報那一個(self) -> None:
        settings = _settings(line_channel_access_token="t", line_channel_secret="s")

        with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY") as exc:
            settings.require_production_secrets()

        assert "LINE_CHANNEL_SECRET" not in str(exc.value)


class TestEmulatorDetection:
    def test_有設模擬器位址就用模擬器(self) -> None:
        assert _settings(firestore_emulator_host="localhost:8080").use_emulator is True

    def test_沒設就連雲端(self) -> None:
        assert _settings().use_emulator is False


class TestValidation:
    def test_每日筆數超出範圍會被拒絕(self) -> None:
        """設成 0 會讓日報永遠是空的，這種錯要在啟動時擋下。"""
        with pytest.raises(ValueError):
            _settings(max_jobs_per_day=0)

    def test_未知環境變數不會導致啟動失敗(self) -> None:
        """extra="ignore"：Modal / Docker 會塞一堆自己的環境變數進來。"""
        settings = Settings(_env_file=None, SOME_UNRELATED_VAR="x")

        assert settings.app_env == "development"


class TestCaching:
    def test_get_settings_回傳同一個實例(self) -> None:
        """每次呼叫都重讀 .env 會拖慢每一筆職缺的處理。"""
        assert get_settings() is get_settings()
