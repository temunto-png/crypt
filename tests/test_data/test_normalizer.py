"""normalizer モジュールのテスト。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cryptbot.data.normalizer import REQUIRED_COLUMNS, calculate_adx, normalize


# ------------------------------------------------------------------ #
# ヘルパー
# ------------------------------------------------------------------ #

def make_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """テスト用の合成 OHLCV DataFrame を生成する。

    正弦波ベースの close + ランダムノイズ。
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 3_000_000 + 100_000 * np.sin(2 * np.pi * t / 50) + rng.normal(0, 5_000, n)
    high = close + rng.uniform(1_000, 10_000, n)
    low = close - rng.uniform(1_000, 10_000, n)
    open_ = close + rng.normal(0, 3_000, n)
    volume = rng.uniform(0.1, 10.0, n)

    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


# ------------------------------------------------------------------ #
# normalize()
# ------------------------------------------------------------------ #

EXPECTED_COLUMNS = [
    "sma_short",
    "sma_long",
    "atr",
    "rsi",
    "bb_upper",
    "bb_lower",
    "bb_bandwidth",
    "momentum",
    "volume_ma_ratio",
    "adx",
]


class TestNormalize:
    def test_all_columns_present(self):
        """出力に全テクニカル指標カラムが含まれること。"""
        df = make_ohlcv()
        result = normalize(df)
        for col in EXPECTED_COLUMNS:
            assert col in result.columns, f"カラム '{col}' が存在しない"

    def test_original_columns_preserved(self):
        """OHLCV 元カラムが保持されること。"""
        df = make_ohlcv()
        result = normalize(df)
        for col in ["open", "high", "low", "close", "volume"]:
            assert col in result.columns

    def test_input_not_modified(self):
        """入力 DataFrame が変更されないこと（コピー確認）。"""
        df = make_ohlcv()
        original_columns = list(df.columns)
        original_values = df["close"].copy()

        normalize(df)

        assert list(df.columns) == original_columns
        pd.testing.assert_series_equal(df["close"], original_values)
        for col in EXPECTED_COLUMNS:
            assert col not in df.columns, f"入力 df に '{col}' が追加されてしまった"

    def test_returns_copy_not_same_object(self):
        """normalize が入力とは別の DataFrame を返すこと。"""
        df = make_ohlcv()
        result = normalize(df)
        assert result is not df

    def test_warmup_rows_are_nan(self):
        """先頭の数行（ウォームアップ期間）が NaN であること。"""
        df = make_ohlcv()
        result = normalize(df)

        # sma_long は period=50 なので先頭49行は NaN
        assert result["sma_long"].iloc[:49].isna().all(), "sma_long の先頭49行が NaN でない"
        assert not pd.isna(result["sma_long"].iloc[49]), "sma_long の50行目が NaN"

        # sma_short は period=20 なので先頭19行は NaN
        assert result["sma_short"].iloc[:19].isna().all(), "sma_short の先頭19行が NaN でない"
        assert not pd.isna(result["sma_short"].iloc[19]), "sma_short の20行目が NaN"

    def test_tail_not_nan(self):
        """十分長い DataFrame では末尾が NaN でないこと。"""
        df = make_ohlcv(n=200)
        result = normalize(df)
        for col in EXPECTED_COLUMNS:
            val = result[col].iloc[-1]
            assert not pd.isna(val), f"末尾の '{col}' が NaN"

    def test_sma_short_values(self):
        """sma_short (SMA-20) の値が pandas rolling mean と一致すること。"""
        df = make_ohlcv()
        result = normalize(df)
        expected = df["close"].rolling(20).mean()
        pd.testing.assert_series_equal(result["sma_short"], expected, check_names=False)

    def test_sma_long_values(self):
        """sma_long (SMA-50) の値が pandas rolling mean と一致すること。"""
        df = make_ohlcv()
        result = normalize(df)
        expected = df["close"].rolling(50).mean()
        pd.testing.assert_series_equal(result["sma_long"], expected, check_names=False)

    def test_atr_values(self):
        """ATR の値が手計算と一致すること。"""
        df = make_ohlcv()
        result = normalize(df)

        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        expected = tr.rolling(14).mean()

        pd.testing.assert_series_equal(result["atr"], expected, check_names=False)

    def test_atr_positive(self):
        """ATR の非 NaN 値はすべて正であること。"""
        df = make_ohlcv()
        result = normalize(df)
        non_nan = result["atr"].dropna()
        assert (non_nan > 0).all(), "ATR に 0 以下の値がある"

    def test_rsi_range(self):
        """RSI の非 NaN 値は 0〜100 の範囲内であること。"""
        df = make_ohlcv()
        result = normalize(df)
        non_nan = result["rsi"].dropna()
        assert (non_nan >= 0).all() and (non_nan <= 100).all(), "RSI が 0〜100 の範囲外"

    def test_bb_upper_greater_than_lower(self):
        """bb_upper > bb_lower であること（非 NaN 行）。"""
        df = make_ohlcv()
        result = normalize(df)
        valid = result.dropna(subset=["bb_upper", "bb_lower"])
        assert (valid["bb_upper"] > valid["bb_lower"]).all()

    def test_bb_upper_formula(self):
        """bb_upper = sma_short + 2 * std(20) であること。"""
        df = make_ohlcv()
        result = normalize(df)
        std20 = df["close"].rolling(20).std(ddof=1)
        expected_upper = df["close"].rolling(20).mean() + 2 * std20
        pd.testing.assert_series_equal(result["bb_upper"], expected_upper, check_names=False)

    def test_bb_lower_formula(self):
        """bb_lower = sma_short - 2 * std(20) であること。"""
        df = make_ohlcv()
        result = normalize(df)
        std20 = df["close"].rolling(20).std(ddof=1)
        expected_lower = df["close"].rolling(20).mean() - 2 * std20
        pd.testing.assert_series_equal(result["bb_lower"], expected_lower, check_names=False)

    def test_bb_bandwidth_formula(self):
        """bb_bandwidth = (bb_upper - bb_lower) / sma_short * 100 であること。"""
        df = make_ohlcv()
        result = normalize(df)
        expected = (result["bb_upper"] - result["bb_lower"]) / result["sma_short"] * 100
        pd.testing.assert_series_equal(result["bb_bandwidth"], expected, check_names=False)

    def test_bb_bandwidth_positive(self):
        """bb_bandwidth の非 NaN 値はすべて正であること。"""
        df = make_ohlcv()
        result = normalize(df)
        non_nan = result["bb_bandwidth"].dropna()
        assert (non_nan > 0).all()

    def test_momentum_formula(self):
        """momentum = close.pct_change(14) * 100 であること。"""
        df = make_ohlcv()
        result = normalize(df)
        expected = df["close"].pct_change(14) * 100
        pd.testing.assert_series_equal(result["momentum"], expected, check_names=False)

    def test_volume_ma_ratio_formula(self):
        """volume_ma_ratio = volume / volume.rolling(20).mean() であること。"""
        df = make_ohlcv()
        result = normalize(df)
        expected = df["volume"] / df["volume"].rolling(20).mean()
        pd.testing.assert_series_equal(result["volume_ma_ratio"], expected, check_names=False)

    def test_volume_ma_ratio_positive(self):
        """volume_ma_ratio の非 NaN 値はすべて正であること。"""
        df = make_ohlcv()
        result = normalize(df)
        non_nan = result["volume_ma_ratio"].dropna()
        assert (non_nan > 0).all()

    def test_missing_columns_raises_value_error(self):
        """必要カラムが不足している場合に ValueError が発生すること。"""
        df = make_ohlcv().drop(columns=["high", "volume"])
        with pytest.raises(ValueError, match="normalize: 必要なカラムが不足"):
            normalize(df)

    def test_missing_columns_error_message_contains_column_names(self):
        """ValueError のメッセージに不足カラム名が含まれること。"""
        df = make_ohlcv().drop(columns=["high"])
        with pytest.raises(ValueError, match="high"):
            normalize(df)

    def test_empty_required_columns_constant(self):
        """REQUIRED_COLUMNS が期待通りであること。"""
        assert REQUIRED_COLUMNS == frozenset(["open", "high", "low", "close", "volume"])

    def test_row_count_preserved(self):
        """行数が変化しないこと。"""
        df = make_ohlcv(n=200)
        result = normalize(df)
        assert len(result) == len(df)

    def test_index_preserved(self):
        """インデックスが保持されること。"""
        df = make_ohlcv()
        result = normalize(df)
        pd.testing.assert_index_equal(result.index, df.index)


# ------------------------------------------------------------------ #
# calculate_adx()
# ------------------------------------------------------------------ #

class TestCalculateAdx:
    def test_returns_series(self):
        """calculate_adx が pd.Series を返すこと。"""
        df = make_ohlcv()
        result = calculate_adx(df)
        assert isinstance(result, pd.Series)

    def test_length_matches_input(self):
        """出力の長さが入力と一致すること。"""
        df = make_ohlcv()
        result = calculate_adx(df)
        assert len(result) == len(df)

    def test_index_preserved(self):
        """インデックスが保持されること。"""
        df = make_ohlcv()
        result = calculate_adx(df)
        pd.testing.assert_index_equal(result.index, df.index)

    def test_tail_not_nan(self):
        """十分長い DataFrame では末尾が NaN でないこと。"""
        df = make_ohlcv(n=200)
        result = calculate_adx(df)
        assert not pd.isna(result.iloc[-1])

    def test_adx_range(self):
        """非 NaN 値は 0〜100 の範囲内であること。"""
        df = make_ohlcv(n=200)
        result = calculate_adx(df)
        non_nan = result.dropna()
        assert len(non_nan) > 0
        assert (non_nan >= 0).all() and (non_nan <= 100).all(), "ADX が 0〜100 の範囲外"

    def test_custom_period(self):
        """period パラメータが機能すること（結果が異なること）。"""
        df = make_ohlcv(n=200)
        adx_14 = calculate_adx(df, period=14)
        adx_28 = calculate_adx(df, period=28)
        # 期間が異なれば結果も異なる
        assert not adx_14.dropna().equals(adx_28.dropna())

    def test_missing_columns_raises(self):
        """必要カラムが不足している場合に ValueError が発生すること。"""
        df = make_ohlcv().drop(columns=["high"])
        with pytest.raises(ValueError, match="calculate_adx: 必要なカラムが不足"):
            calculate_adx(df)

    def test_trending_market_high_adx(self):
        """強いトレンド相場では ADX が上昇傾向にあること。"""
        n = 200
        # 線形トレンドで高い ADX を期待
        t = np.arange(n, dtype=float)
        close = 1_000_000 + 10_000 * t
        high = close + 5_000
        low = close - 5_000
        open_ = close
        volume = np.ones(n)
        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
        )
        result = calculate_adx(df)
        # 末尾100行の平均 ADX が 20 を超えること（強トレンド）
        tail_mean = result.iloc[-100:].mean()
        assert tail_mean > 20, f"強トレンド相場での ADX 平均が低すぎる: {tail_mean:.2f}"
