"""Tests for the batched yfinance fetchers in price_fetcher.

The batch functions wrap yf.download; the tests monkeypatch it with a small
hand-built DataFrame so no network access happens.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + os.sep + "..")

import price_fetcher


def _make_batch_df(data_by_symbol):
    """Build a group_by='ticker'-style multi-index OHLCV DataFrame."""
    import pandas as pd

    frames = {}
    for symbol, rows in data_by_symbol.items():
        idx = pd.to_datetime([r["date"] for r in rows])
        frames[symbol] = pd.DataFrame(
            {
                "Open": [r["open"] for r in rows],
                "High": [r["high"] for r in rows],
                "Low": [r["low"] for r in rows],
                "Close": [r["close"] for r in rows],
                "Volume": [r["volume"] for r in rows],
            },
            index=idx,
        )
    df = pd.concat(frames, axis=1, names=["ticker", "field"])
    return df


class TestDownloadHistoriesParsing:
    def test_multi_symbol_rows_extracted(self, monkeypatch):
        import yfinance as yf

        df = _make_batch_df({
            "AAA": [
                {"date": "2026-01-05", "open": 10.0, "high": 11.0, "low": 9.5,
                 "close": 10.5, "volume": 100},
                {"date": "2026-01-06", "open": 10.5, "high": 12.0, "low": 10.0,
                 "close": 11.5, "volume": 200},
            ],
            "BBB": [
                {"date": "2026-01-05", "open": 50.0, "high": 51.0, "low": 49.0,
                 "close": 50.5, "volume": 300},
            ],
        })
        captured = {}

        def fake_download(**kwargs):
            captured.update(kwargs)
            return df

        monkeypatch.setattr(yf, "download", fake_download)
        results = price_fetcher._download_histories(["AAA", "BBB"], "2026-01-05")

        assert set(results) == {"AAA", "BBB"}
        assert captured["start"] == "2026-01-05"
        # rows are sorted ascending and carry the full OHLCV shape
        aaa = results["AAA"]
        assert [r["price_date"] for r in aaa] == ["2026-01-05", "2026-01-06"]
        assert aaa[0]["close"] == 10.5
        assert aaa[1]["high"] == 12.0
        assert aaa[0]["volume"] == 100
        assert results["BBB"][0]["low"] == 49.0

    def test_nan_close_rows_are_dropped(self, monkeypatch):
        import yfinance as yf
        import pandas as pd

        idx = pd.to_datetime(["2026-01-05", "2026-01-06"])
        sub = pd.DataFrame(
            {"Open": [10.0, float("nan")], "High": [11.0, float("nan")],
             "Low": [9.0, float("nan")], "Close": [10.5, float("nan")],
             "Volume": [100, 0]},
            index=idx,
        )
        df = pd.concat({"AAA": sub}, axis=1, names=["ticker", "field"])
        monkeypatch.setattr(yf, "download", lambda **kwargs: df)

        results = price_fetcher._download_histories(["AAA"], "2026-01-05")
        assert len(results["AAA"]) == 1
        assert results["AAA"][0]["price_date"] == "2026-01-05"


class TestFetchPriceHistories:
    def test_empty_requests(self):
        assert price_fetcher.fetch_price_histories({}) == {}

    def test_single_symbol_delegates_to_fetch_price_history(self, monkeypatch):
        captured = []

        def fake_history(symbol, start, end):
            captured.append((symbol, start, end))
            return [{"price_date": start, "low": 1.0, "high": 2.0, "close": 1.5}]

        monkeypatch.setattr(price_fetcher, "fetch_price_history", fake_history)
        results = price_fetcher.fetch_price_histories({"AAPL": "2026-01-05"})

        assert results == {"AAPL": [{"price_date": "2026-01-05", "low": 1.0,
                                     "high": 2.0, "close": 1.5}]}
        assert captured == [("AAPL", "2026-01-05",
                              __import__("datetime").date.today().strftime("%Y-%m-%d"))]

    def test_batch_uses_earliest_start(self, monkeypatch):
        import yfinance as yf

        df = _make_batch_df({
            "AAA": [{"date": "2025-01-02", "open": 1.0, "high": 2.0, "low": 0.5,
                     "close": 1.5, "volume": 10}],
        })
        starts_seen = {}

        def fake_download(**kwargs):
            starts_seen["start"] = kwargs["start"]
            return df

        monkeypatch.setattr(yf, "download", fake_download)
        monkeypatch.setattr(price_fetcher, "rate_limit", lambda *a, **k: None)
        # BBB is absent from the batch; its per-symbol fallback must stay
        # offline too.
        monkeypatch.setattr(price_fetcher, "fetch_price_history",
                            lambda s, start, end: [])
        results = price_fetcher.fetch_price_histories(
            {"AAA": "2025-01-02", "BBB": "2026-06-01"})

        # one download from the earliest of the two requested starts
        assert starts_seen["start"] == "2025-01-02"
        assert results["AAA"][0]["close"] == 1.5
        # BBB has no data in the fake download and no fallback rows -> absent
        assert "BBB" not in results or not results.get("BBB")


class TestFetchCurrentPricesBatch:
    def test_batch_extracts_latest_close(self, monkeypatch):
        import yfinance as yf

        df = _make_batch_df({
            "AAA": [
                {"date": "2026-09-03", "open": 10.0, "high": 11.0, "low": 9.5,
                 "close": 10.5, "volume": 100},
                {"date": "2026-09-04", "open": 10.5, "high": 12.0, "low": 10.0,
                 "close": 11.5, "volume": 200},
            ],
        })
        monkeypatch.setattr(yf, "download", lambda **kwargs: df)
        monkeypatch.setattr(price_fetcher, "rate_limit", lambda *a, **k: None)
        # ZZZ is absent from the batch; keep its per-symbol fallback offline.
        monkeypatch.setattr(price_fetcher, "fetch_current_price",
                            lambda s: None)
        prices = price_fetcher.fetch_current_prices_batch(["AAA", "ZZZ"])

        assert prices["AAA"]["close"] == 11.5
        assert prices["AAA"]["price_date"] == "2026-09-04"
        # ZZZ (missing from the batch) falls back to an individual fetch,
        # which returns None here -> absent from the map.
        assert "ZZZ" not in prices

    def test_empty_and_single_symbol(self, monkeypatch):
        assert price_fetcher.fetch_current_prices_batch([]) == {}
        # single symbol delegates to fetch_current_price (no batch download)
        monkeypatch.setattr(price_fetcher, "fetch_current_price",
                            lambda s: {"symbol": s, "close": 5.0})
        assert price_fetcher.fetch_current_prices_batch(["ONE"]) == \
            {"ONE": {"symbol": "ONE", "close": 5.0}}