"""pytest グローバル設定。

テスト実行時に環境変数が未設定でも Settings がデフォルト値でインスタンス化できるよう、
CRYPT_ プレフィックスの必須環境変数をクリアしておく。
"""
import os

import pytest


@pytest.fixture(autouse=True)
def clear_crypt_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CRYPT_ 環境変数をテスト間でクリアし、.env ファイルの影響を排除する。"""
    for key in list(os.environ):
        if key.startswith("CRYPT_") or key in ("TRADING_MODE", "KILL_SWITCH_OVERRIDE"):
            monkeypatch.delenv(key, raising=False)
