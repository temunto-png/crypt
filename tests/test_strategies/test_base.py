"""BaseStrategy, Signal, MarketState, Direction, Regime, RegimeDetector のテスト。"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from cryptbot.data.normalizer import normalize
from cryptbot.strategies.base import BaseStrategy, Direction, MarketState, Signal
from cryptbot.strategies.regime import Regime, RegimeDetector
from cryptbot.utils.time_utils import JST


# ---------------------------------------------------------------------------
# テスト用コンクリート実装
# ---------------------------------------------------------------------------

class _DummyStrategy(BaseStrategy):
    """テスト用の最小実装。"""

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        return self._hold("dummy", datetime.now(JST))


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 120, trend: str = "up") -> pd.DataFrame:
    """テスト用 OHLCV DataFrame を生成する。

    Args:
        n: バー数（normalize() の dropna() 後に最低 20 バー残るよう 120 以上を推奨）
        trend: "up" / "down" / "flat"
    """
    np.random.seed(42)
    base = 5_000_000.0  # BTC/JPY 想定

    if trend == "up":
        close = base + np.arange(n) * 10_000 + np.random.randn(n) * 5_000
    elif trend == "down":
        close = base - np.arange(n) * 10_000 + np.random.randn(n) * 5_000
    else:  # flat
        close = base + np.random.randn(n) * 2_000

    high = close + np.abs(np.random.randn(n)) * 3_000
    low = close - np.abs(np.random.randn(n)) * 3_000
    open_ = close + np.random.randn(n) * 1_000
    volume = np.random.uniform(1, 10, n)

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )
    return normalize(df).dropna()


def _make_high_vol_ohlcv(n: int = 120) -> pd.DataFrame:
    """ATR ratio > 2.0 になる高ボラ DataFrame を生成する。

    先頭 n-5 バーを low-vol、末尾 5 バーを high-vol（ATR が中央値の 3 倍超）にする。
    n は normalize() の dropna() 後に最低 20 バー残るよう 120 以上を推奨。
    """
    np.random.seed(0)
    base = 5_000_000.0
    close = base + np.random.randn(n) * 1_000
    # 末尾 5 バーに大きな振れ幅を付ける
    high = close + np.abs(np.random.randn(n)) * 1_000
    low = close - np.abs(np.random.randn(n)) * 1_000
    high[-5:] = close[-5:] + 200_000   # 極端な ATR
    low[-5:] = close[-5:] - 200_000
    open_ = close + np.random.randn(n) * 500
    volume = np.ones(n)

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )
    return normalize(df).dropna()


def _now_jst() -> datetime:
    return datetime.now(JST)


# ---------------------------------------------------------------------------
# Signal / MarketState / Direction テスト
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_signal_creation(self) -> None:
        ts = _now_jst()
        sig = Signal(direction=Direction.BUY, confidence=0.8, reason="test", timestamp=ts)
        assert sig.direction == Direction.BUY
        assert sig.confidence == 0.8
        assert sig.reason == "test"
        assert sig.timestamp == ts

    def test_market_state_creation(self) -> None:
        ts = _now_jst()
        ms = MarketState(
            spread_pct=0.1,
            bid_depth_btc=0.5,
            volatility_1h_pct=1.0,
            timestamp=ts,
        )
        assert ms.spread_pct == 0.1
        assert ms.bid_depth_btc == 0.5
        assert ms.volatility_1h_pct == 1.0

    def test_direction_enum_values(self) -> None:
        assert Direction.BUY == "BUY"
        assert Direction.SELL == "SELL"
        assert Direction.HOLD == "HOLD"


# ---------------------------------------------------------------------------
# BaseStrategy.should_skip テスト
# ---------------------------------------------------------------------------

class TestShouldSkip:
    """BaseStrategy.should_skip() のデフォルト実装テスト。"""

    def _state(
        self,
        spread_pct: float = 0.1,
        bid_depth_btc: float = 1.0,
        volatility_1h_pct: float = 1.0,
    ) -> MarketState:
        return MarketState(
            spread_pct=spread_pct,
            bid_depth_btc=bid_depth_btc,
            volatility_1h_pct=volatility_1h_pct,
            timestamp=_now_jst(),
        )

    def setup_method(self) -> None:
        self.strategy = _DummyStrategy("dummy")

    def test_spread_too_large(self) -> None:
        skip, reason = self.strategy.should_skip(self._state(spread_pct=0.51))
        assert skip is True
        assert "スプレッド" in reason

    def test_spread_at_boundary(self) -> None:
        # 境界値: 0.5 はギリギリ許容（> 0.5 のみ skip）
        skip, _ = self.strategy.should_skip(self._state(spread_pct=0.5))
        assert skip is False

    def test_bid_depth_too_small(self) -> None:
        skip, reason = self.strategy.should_skip(self._state(bid_depth_btc=0.009))
        assert skip is True
        assert "板厚" in reason

    def test_bid_depth_at_boundary(self) -> None:
        # 境界値: 0.01 はギリギリ許容（< 0.01 のみ skip）
        skip, _ = self.strategy.should_skip(self._state(bid_depth_btc=0.01))
        assert skip is False

    def test_volatility_too_large(self) -> None:
        skip, reason = self.strategy.should_skip(self._state(volatility_1h_pct=5.01))
        assert skip is True
        assert "ボラティリティ" in reason

    def test_volatility_at_boundary(self) -> None:
        # 境界値: 5.0 はギリギリ許容（> 5.0 のみ skip）
        skip, _ = self.strategy.should_skip(self._state(volatility_1h_pct=5.0))
        assert skip is False

    def test_normal_conditions(self) -> None:
        skip, reason = self.strategy.should_skip(
            self._state(spread_pct=0.1, bid_depth_btc=1.0, volatility_1h_pct=1.0)
        )
        assert skip is False
        assert reason == ""


# ---------------------------------------------------------------------------
# Regime enum テスト
# ---------------------------------------------------------------------------

class TestRegimeEnum:
    def test_regime_values(self) -> None:
        assert Regime.TREND_UP == "TREND_UP"
        assert Regime.TREND_DOWN == "TREND_DOWN"
        assert Regime.RANGE == "RANGE"
        assert Regime.HIGH_VOL == "HIGH_VOL"


# ---------------------------------------------------------------------------
# RegimeDetector.detect テスト
# ---------------------------------------------------------------------------

class TestRegimeDetector:
    def setup_method(self) -> None:
        self.detector = RegimeDetector()

    def test_high_vol(self) -> None:
        df = _make_high_vol_ohlcv(n=120)
        regime = self.detector.detect(df)
        assert regime == Regime.HIGH_VOL

    def test_trend_up(self) -> None:
        df = _make_ohlcv(n=120, trend="up")
        regime = self.detector.detect(df)
        assert regime == Regime.TREND_UP

    def test_trend_down(self) -> None:
        df = _make_ohlcv(n=120, trend="down")
        regime = self.detector.detect(df)
        assert regime == Regime.TREND_DOWN

    def test_range(self) -> None:
        df = _make_ohlcv(n=120, trend="flat")
        regime = self.detector.detect(df)
        assert regime == Regime.RANGE

    def test_atr_zero_no_high_vol(self) -> None:
        """ATR median が 0 のとき HIGH_VOL にならないことを検証。"""
        # ATR を全て 0 に設定したデータを生成
        df = _make_ohlcv(n=120, trend="flat")
        df["atr"] = 0.0
        regime = self.detector.detect(df)
        # ATR ratio = 0.0 / 0 = 0.0（ゼロ除算回避）であり 2.0 を超えないため
        assert regime != Regime.HIGH_VOL

    def test_missing_columns(self) -> None:
        df = pd.DataFrame({"close": [1.0, 2.0], "high": [1.1, 2.1], "low": [0.9, 1.9]})
        with pytest.raises(ValueError, match="必要なカラムが不足"):
            self.detector.detect(df)

    def test_insufficient_data(self) -> None:
        # 必要カラムはあるが行数が 19 バー
        n = 19
        df = pd.DataFrame(
            {
                "close": np.ones(n),
                "high": np.ones(n),
                "low": np.ones(n),
                "atr": np.ones(n),
                "sma_short": np.ones(n),
                "sma_long": np.ones(n),
            }
        )
        with pytest.raises(ValueError, match="データが不足"):
            self.detector.detect(df)
