"""Firestore 憑證來源的選擇邏輯。

三種環境要走三條不同的路：

    本機 Compose   模擬器，完全不需要憑證
    Modal（雲端）  Secrets 只能給環境變數，憑證是 JSON **字串**
    一般機器       沿用 Google 預設憑證機制（ADC，讀憑證**檔案**）

選錯的後果是部署當下才炸，所以這裡把三條路都釘住。
"""

import json

import pytest

from src.config import Settings
from src.store import firestore as store_module
from src.store.firestore import JobStore, build_client

_FAKE_CREDENTIALS = object()


class FakeAsyncClient:
    """只記錄建構參數 —— 這裡要驗的是「傳了什麼憑證給 SDK」。"""

    def __init__(self, project: str | None = None, credentials: object | None = None) -> None:
        self.project = project
        self.credentials = credentials


@pytest.fixture(autouse=True)
def _fake_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """攔住真實 SDK：建立 AsyncClient 會去找憑證，單元測試不該碰到那些。"""
    monkeypatch.setattr(store_module.firestore, "AsyncClient", FakeAsyncClient)


def _settings(**overrides: str) -> Settings:
    """繞過本機 .env，避免測試結果隨機器而變。"""
    return Settings(_env_file=None, **overrides)


def _service_account_json(project_id: str = "job104-daily-report") -> str:
    return json.dumps({
        "type": "service_account",
        "project_id": project_id,
        "private_key_id": "abc",
        "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
        "client_email": "bot@example.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token",
    })


class TestEmulator:
    def test_模擬器模式不帶憑證(self) -> None:
        """本機開發不該要求任何金鑰 —— 這是「三把金鑰就能跑」的前提。"""
        client = build_client(_settings(firestore_emulator_host="firestore:8080"))

        assert client.credentials is None

    def test_模擬器模式忽略_service_account(self) -> None:
        """本機不小心留著雲端金鑰時，也不該誤連到正式資料庫。"""
        client = build_client(
            _settings(
                firestore_emulator_host="firestore:8080",
                firebase_service_account=_service_account_json(),
            )
        )

        assert client.credentials is None


class TestServiceAccountString:
    def test_用字串建立憑證(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Modal Secrets 只能給環境變數，憑證必須能從字串直接建。"""
        monkeypatch.setattr(
            store_module.service_account.Credentials,
            "from_service_account_info",
            classmethod(lambda cls, info, **kw: _FAKE_CREDENTIALS),
        )

        client = build_client(_settings(firebase_service_account=_service_account_json()))

        assert client.credentials is _FAKE_CREDENTIALS

    def test_專案_id_不一致時發出警告(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """憑證指向 A 專案、設定寫 B 專案，會寫進錯的資料庫且不易察覺。"""
        monkeypatch.setattr(
            store_module.service_account.Credentials,
            "from_service_account_info",
            classmethod(lambda cls, info, **kw: _FAKE_CREDENTIALS),
        )

        with caplog.at_level("WARNING"):
            build_client(
                _settings(
                    firebase_project_id="專案B",
                    firebase_service_account=_service_account_json("專案A"),
                )
            )

        assert "專案A" in caplog.text
        assert "專案B" in caplog.text


class TestBadInput:
    def test_不是合法_json_時給出可讀錯誤(self) -> None:
        """貼金鑰時少複製一段是常見失誤，錯誤訊息要直接指出是哪個變數。"""
        with pytest.raises(RuntimeError, match="FIREBASE_SERVICE_ACCOUNT"):
            build_client(_settings(firebase_service_account="{不是 json"))

    def test_json_合法但不是_service_account_時給出可讀錯誤(self) -> None:
        """貼成 Firebase 網頁設定檔（apiKey/authDomain）是另一個常見失誤。"""
        with pytest.raises(RuntimeError, match="FIREBASE_SERVICE_ACCOUNT"):
            build_client(_settings(firebase_service_account='{"apiKey": "x"}'))


class TestDefaultCredentials:
    def test_沒設字串時退回預設憑證機制(self) -> None:
        """在有 GOOGLE_APPLICATION_CREDENTIALS 的機器上仍照舊運作。"""
        client = build_client(_settings())

        assert client.credentials is None
        assert client.project == "job104-daily-report"

    def test_只有空白字元視為未設定(self) -> None:
        client = build_client(_settings(firebase_service_account="   "))

        assert client.credentials is None


class TestJobStoreWiring:
    def test_未注入_client_時走_build_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """憑證邏輯要真的被 JobStore 用到，不能只是個沒人呼叫的函式。"""
        monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "firestore:8080")
        from src.config import get_settings

        get_settings.cache_clear()
        try:
            store = JobStore()
        finally:
            get_settings.cache_clear()

        assert isinstance(store._client, FakeAsyncClient)
