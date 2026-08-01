"""地區解析測試。

代碼值已於 2026-08-01 對 104 官方 Area.json 驗證，因此除了查找邏輯外，
也針對幾個代表性代碼與 104 的合併縣市規則做斷言 —— 這些是 104 改版時
最該被擋下來的迴歸。
"""

import pytest

from src.scraper.area_map import (
    DEFAULT_AREA_CODE,
    parse_query,
    resolve_area_code,
)


class TestResolveAreaCode:
    @pytest.mark.parametrize("alias", ["台北", "臺北", "台北市", "臺北市", "北市"])
    def test_台北的各種寫法都能解析(self, alias: str) -> None:
        assert resolve_area_code(alias) == resolve_area_code("臺北市")

    def test_前後空白會被忽略(self) -> None:
        assert resolve_area_code("  台中  ") == resolve_area_code("臺中市")

    def test_無法辨識的地區回傳_None(self) -> None:
        assert resolve_area_code("火星") is None

    def test_空字串回傳_None(self) -> None:
        assert resolve_area_code("") is None
        assert resolve_area_code("   ") is None

    @pytest.mark.parametrize(
        ("city", "expected"),
        [
            ("台北市", "6001001000"),
            ("新北市", "6001002000"),
            ("桃園市", "6001005000"),
            ("台中市", "6001008000"),
            ("高雄市", "6001016000"),
            ("連江縣", "6001023000"),
        ],
    )
    def test_代碼值與_104_官方對照表一致(self, city: str, expected: str) -> None:
        assert resolve_area_code(city) == expected

    @pytest.mark.parametrize(
        "aliases",
        [
            ("新竹", "新竹市", "新竹縣", "竹科"),
            ("嘉義", "嘉義市", "嘉義縣"),
        ],
    )
    def test_104_不分縣市者共用同一碼(self, aliases: tuple[str, ...]) -> None:
        codes = {resolve_area_code(alias) for alias in aliases}
        assert len(codes) == 1
        assert None not in codes

    @pytest.mark.parametrize("island", ["澎湖", "金門", "馬祖", "連江"])
    def test_離島也要能解析(self, island: str) -> None:
        assert resolve_area_code(island) is not None


class TestParseQuery:
    def test_地區加職務會正確拆開(self) -> None:
        area_code, keyword = parse_query("台北 後端工程師")
        assert area_code == resolve_area_code("臺北市")
        assert keyword == "後端工程師"

    def test_職務含空白時完整保留(self) -> None:
        _, keyword = parse_query("新北 AI 工程師")
        assert keyword == "AI 工程師"

    def test_無法辨識地區時整句當關鍵字並搜全台(self) -> None:
        area_code, keyword = parse_query("資深 Python 工程師")
        assert area_code == DEFAULT_AREA_CODE
        assert keyword == "資深 Python 工程師"

    def test_只給地區沒給職務要報錯(self) -> None:
        with pytest.raises(ValueError, match="職務關鍵字"):
            parse_query("台北")

    def test_空輸入要報錯(self) -> None:
        with pytest.raises(ValueError, match="不可為空"):
            parse_query("   ")
