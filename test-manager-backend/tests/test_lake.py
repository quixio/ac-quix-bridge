import pytest

from shared.post_race_ai import lake


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_lake_query_missing_creds_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("Quix__Lakehouse__Query__Url", raising=False)
    monkeypatch.delenv("Quix__Lakehouse__Query__AuthToken", raising=False)
    with pytest.raises(RuntimeError):
        lake.lake_query("SELECT 1")


def test_lake_query_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("Quix__Lakehouse__Query__Url", "http://lake")
    monkeypatch.setenv("Quix__Lakehouse__Query__AuthToken", "tok")
    captured: dict[str, object] = {}

    def fake_post(url, content, headers, timeout):  # noqa: ANN001, ANN202
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResp("lap,pos\n1,0.5\n1,0.6\n")

    monkeypatch.setattr(lake.httpx, "post", fake_post)
    df = lake.lake_query("SELECT lap,pos FROM t WHERE x=1")
    assert captured["url"] == "http://lake/query"
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert list(df.columns) == ["lap", "pos"]
    assert len(df) == 2
