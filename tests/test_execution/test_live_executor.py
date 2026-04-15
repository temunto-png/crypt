"""LiveExecutor のテスト（スケルトン確認）。"""
from __future__ import annotations

import asyncio

import pytest

from cryptbot.execution.live_executor import LiveExecutor


def _run(coro):
    return asyncio.run(coro)


class TestLiveExecutorSkeleton:
    def test_can_be_instantiated(self) -> None:
        """__init__ は NotImplementedError を raise しない。"""
        executor = LiveExecutor(exchange=object(), storage=object())
        assert executor is not None

    def test_place_order_raises_not_implemented(self) -> None:
        executor = LiveExecutor(exchange=object(), storage=object())
        with pytest.raises(NotImplementedError):
            _run(
                executor.place_order("btc_jpy", "buy", "market", 0.1, price=10_000_000.0)
            )

    def test_cancel_order_raises_not_implemented(self) -> None:
        executor = LiveExecutor(exchange=object(), storage=object())
        with pytest.raises(NotImplementedError):
            _run(executor.cancel_order("btc_jpy", "12345"))

    def test_get_open_orders_raises_not_implemented(self) -> None:
        executor = LiveExecutor(exchange=object(), storage=object())
        with pytest.raises(NotImplementedError):
            _run(executor.get_open_orders("btc_jpy"))

    def test_is_subclass_of_base_executor(self) -> None:
        """BaseExecutor のサブクラスであることを確認。"""
        from cryptbot.execution.base import BaseExecutor
        assert issubclass(LiveExecutor, BaseExecutor)
