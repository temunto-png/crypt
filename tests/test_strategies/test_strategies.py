"""5戦略クラスの単体テスト。"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from cryptbot.strategies.base import Direction
from cryptbot.strategies.buy_and_hold import BuyAndHoldStrategy
from cryptbot.strategies.ma_cross import MACrossStrategy
from cryptbot.strategies.mean_reversion import MeanReversionStrategy
from cryptbot.strategies.momentum import MomentumStrategy
from cryptbot.strategies.volatility_filter import VolatilityFilterStrategy
from cryptbot.utils.time_utils import JST, now_jst


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _ts() -> datetime:
    return now_jst()


def _make_base_df(n: int = 5) -> pd.DataFrame:
    """timestamp カラム付きの最小 DataFrame を返す。"""
    return pd.DataFrame({
        "timestamp": [now_jst()] * n,
        "close": [5_000_000.0] * n,
    })


def _make_ma_df(
    shorts: list[float],
    longs: list[float],
) -> pd.DataFrame:
    """sma_short / sma_long カラムを持つ DataFrame。"""
    n = len(shorts)
    return pd.DataFrame({
        "timestamp": [now_jst()] * n,
        "sma_short": shorts,
        "sma_long": longs,
    })


def _make_momentum_df(
    momentums: list[float],
) -> pd.DataFrame:
    n = len(momentums)
    return pd.DataFrame({
        "timestamp": [now_jst()] * n,
        "momentum": momentums,
    })


def _make_mean_rev_df(
    close: float,
    bb_lower: float,
    bb_upper: float,
    bb_bandwidth: float,
) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": [now_jst()],
        "close": [close],
        "bb_lower": [bb_lower],
        "bb_upper": [bb_upper],
        "bb_bandwidth": [bb_bandwidth],
    })


def _make_vol_filter_df(
    shorts: list[float],
    longs: list[float],
    atrs: list[float],
) -> pd.DataFrame:
    n = len(shorts)
    return pd.DataFrame({
        "timestamp": [now_jst()] * n,
        "sma_short": shorts,
        "sma_long": longs,
        "atr": atrs,
    })


# ---------------------------------------------------------------------------
# BuyAndHoldStrategy
# ---------------------------------------------------------------------------

class TestBuyAndHoldStrategy:
    def test_first_call_returns_buy(self) -> None:
        strat = BuyAndHoldStrategy()
        df = _make_base_df(1)
        sig = strat.generate_signal(df)
        assert sig.direction == Direction.BUY
        assert sig.confidence == 1.0

    def test_second_call_returns_hold(self) -> None:
        strat = BuyAndHoldStrategy()
        df = _make_base_df(1)
        strat.generate_signal(df)
        sig = strat.generate_signal(df)
        assert sig.direction == Direction.HOLD

    def test_multiple_calls_all_hold_after_first(self) -> None:
        strat = BuyAndHoldStrategy()
        df = _make_base_df(1)
        strat.generate_signal(df)
        for _ in range(5):
            sig = strat.generate_signal(df)
            assert sig.direction == Direction.HOLD

    def test_name(self) -> None:
        assert BuyAndHoldStrategy().name == "buy_and_hold"

    def test_timestamp_fallback_when_no_timestamp_column(self) -> None:
        strat = BuyAndHoldStrategy()
        df = pd.DataFrame({"close": [1.0]})
        sig = strat.generate_signal(df)
        assert sig.timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# MACrossStrategy
# ---------------------------------------------------------------------------

class TestMACrossStrategy:
    def test_golden_cross_returns_buy(self) -> None:
        # 前バー: short < long → 今バー: short > long
        df = _make_ma_df(
            shorts=[1000.0, 1020.0],
            longs=[1010.0, 1010.0],
        )
        sig = MACrossStrategy().generate_signal(df)
        assert sig.direction == Direction.BUY

    def test_dead_cross_returns_sell(self) -> None:
        # 前バー: short > long → 今バー: short < long
        df = _make_ma_df(
            shorts=[1020.0, 1000.0],
            longs=[1010.0, 1010.0],
        )
        sig = MACrossStrategy().generate_signal(df)
        assert sig.direction == Direction.SELL

    def test_small_diff_returns_hold(self) -> None:
        # |short - long| / long < 0.001 → HOLD
        df = _make_ma_df(
            shorts=[1000.0, 1000.1],
            longs=[1000.0, 1000.0],
        )
        sig = MACrossStrategy(min_ma_diff_pct=0.001).generate_signal(df)
        assert sig.direction == Direction.HOLD

    def test_nan_returns_hold(self) -> None:
        df = _make_ma_df(
            shorts=[float("nan"), float("nan")],
            longs=[1000.0, 1000.0],
        )
        sig = MACrossStrategy().generate_signal(df)
        assert sig.direction == Direction.HOLD

    def test_insufficient_data_returns_hold(self) -> None:
        df = _make_ma_df(shorts=[1000.0], longs=[900.0])
        sig = MACrossStrategy().generate_signal(df)
        assert sig.direction == Direction.HOLD

    def test_no_cross_returns_hold(self) -> None:
        # どちらも short > long のまま変化なし
        df = _make_ma_df(
            shorts=[1020.0, 1025.0],
            longs=[1000.0, 1000.0],
        )
        sig = MACrossStrategy().generate_signal(df)
        assert sig.direction == Direction.HOLD

    def test_name(self) -> None:
        assert MACrossStrategy().name == "ma_cross"


# ---------------------------------------------------------------------------
# MomentumStrategy
# ---------------------------------------------------------------------------

class TestMomentumStrategy:
    def test_buy_when_above_threshold(self) -> None:
        df = _make_momentum_df([3.0])
        sig = MomentumStrategy(threshold=2.0).generate_signal(df)
        assert sig.direction == Direction.BUY

    def test_sell_when_below_neg_threshold(self) -> None:
        df = _make_momentum_df([-3.0])
        sig = MomentumStrategy(threshold=2.0).generate_signal(df)
        assert sig.direction == Direction.SELL

    def test_hold_within_threshold(self) -> None:
        df = _make_momentum_df([1.0])
        sig = MomentumStrategy(threshold=2.0).generate_signal(df)
        assert sig.direction == Direction.HOLD

    def test_confidence_capped_at_1(self) -> None:
        # momentum = 100.0, threshold = 2.0 → confidence = min(100/2/3, 1.0) = 1.0
        df = _make_momentum_df([100.0])
        sig = MomentumStrategy(threshold=2.0).generate_signal(df)
        assert sig.direction == Direction.BUY
        assert sig.confidence == 1.0

    def test_confidence_proportional(self) -> None:
        # momentum = 3.0, threshold = 2.0 → confidence = 3/(2*3) = 0.5
        df = _make_momentum_df([3.0])
        sig = MomentumStrategy(threshold=2.0).generate_signal(df)
        assert abs(sig.confidence - 0.5) < 1e-9

    def test_nan_returns_hold(self) -> None:
        df = _make_momentum_df([float("nan")])
        sig = MomentumStrategy().generate_signal(df)
        assert sig.direction == Direction.HOLD

    def test_name(self) -> None:
        assert MomentumStrategy().name == "momentum"


# ---------------------------------------------------------------------------
# MeanReversionStrategy
# ---------------------------------------------------------------------------

class TestMeanReversionStrategy:
    def test_buy_when_close_below_bb_lower(self) -> None:
        # close < bb_lower, bb_bandwidth >= 1.0
        sig = MeanReversionStrategy(min_bandwidth_pct=1.0).generate_signal(
            _make_mean_rev_df(close=990.0, bb_lower=1000.0, bb_upper=1100.0, bb_bandwidth=5.0)
        )
        assert sig.direction == Direction.BUY

    def test_hold_when_bandwidth_too_small(self) -> None:
        # close < bb_lower だが bandwidth < min_bandwidth_pct
        sig = MeanReversionStrategy(min_bandwidth_pct=1.0).generate_signal(
            _make_mean_rev_df(close=990.0, bb_lower=1000.0, bb_upper=1100.0, bb_bandwidth=0.5)
        )
        assert sig.direction == Direction.HOLD

    def test_sell_when_close_above_bb_upper(self) -> None:
        sig = MeanReversionStrategy().generate_signal(
            _make_mean_rev_df(close=1200.0, bb_lower=900.0, bb_upper=1100.0, bb_bandwidth=5.0)
        )
        assert sig.direction == Direction.SELL

    def test_hold_when_in_range(self) -> None:
        sig = MeanReversionStrategy().generate_signal(
            _make_mean_rev_df(close=1050.0, bb_lower=1000.0, bb_upper=1100.0, bb_bandwidth=5.0)
        )
        assert sig.direction == Direction.HOLD

    def test_nan_returns_hold(self) -> None:
        sig = MeanReversionStrategy().generate_signal(
            _make_mean_rev_df(close=float("nan"), bb_lower=1000.0, bb_upper=1100.0, bb_bandwidth=5.0)
        )
        assert sig.direction == Direction.HOLD

    def test_name(self) -> None:
        assert MeanReversionStrategy().name == "mean_reversion"


# ---------------------------------------------------------------------------
# VolatilityFilterStrategy
# ---------------------------------------------------------------------------

class TestVolatilityFilterStrategy:
    def _golden_cross_df_with_atr(
        self,
        n: int = 25,
        atr_last: float = 2000.0,
    ) -> pd.DataFrame:
        """ゴールデンクロスが起きる DataFrame を生成し、ATR を指定値に設定。"""
        # 前n-1バーは short < long、最後1バーは short > long でクロス
        shorts = [900.0] * (n - 1) + [1100.0]
        longs = [1000.0] * n
        # atr: 先頭n-1は低め (100)、最後1バーは atr_last
        atrs = [100.0] * (n - 1) + [atr_last]
        return _make_vol_filter_df(shorts=shorts, longs=longs, atrs=atrs)

    def test_sufficient_atr_passes_ma_cross(self) -> None:
        # ATR 中央値が 100 → atr_last > 100 × 1.0 → MACross に委譲 → BUY
        df = self._golden_cross_df_with_atr(n=25, atr_last=200.0)
        sig = VolatilityFilterStrategy(atr_multiplier=1.0).generate_signal(df)
        assert sig.direction == Direction.BUY

    def test_insufficient_atr_returns_hold(self) -> None:
        # ATR 中央値が 100 → atr_last = 50 < 100 × 1.0 → HOLD
        df = self._golden_cross_df_with_atr(n=25, atr_last=50.0)
        sig = VolatilityFilterStrategy(atr_multiplier=1.0).generate_signal(df)
        assert sig.direction == Direction.HOLD

    def test_missing_atr_column_returns_hold(self) -> None:
        df = _make_ma_df(shorts=[1100.0, 1200.0], longs=[1000.0, 1000.0])
        sig = VolatilityFilterStrategy().generate_signal(df)
        assert sig.direction == Direction.HOLD

    def test_nan_atr_returns_hold(self) -> None:
        df = _make_vol_filter_df(
            shorts=[900.0, 1100.0],
            longs=[1000.0, 1000.0],
            atrs=[100.0, float("nan")],
        )
        sig = VolatilityFilterStrategy().generate_signal(df)
        assert sig.direction == Direction.HOLD

    def test_name(self) -> None:
        assert VolatilityFilterStrategy().name == "volatility_filter"
