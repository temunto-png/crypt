"""Settings のユニットテスト。"""
from pathlib import Path

import pytest
import yaml

import pytest as _pytest

from cryptbot.config.settings import (
    BacktestSettings,
    CircuitBreakerSettings,
    CvarSettings,
    Settings,
    load_settings,
)


class TestSettingsDefaults:
    def test_mode_defaults_to_paper(self) -> None:
        s = Settings()
        assert s.mode == "paper"

    def test_api_keys_default_empty(self) -> None:
        s = Settings()
        # デフォルトは空文字
        assert s.bitbank_api_key.get_secret_value() == ""
        assert s.bitbank_api_secret.get_secret_value() == ""

    def test_api_key_is_masked_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRYPT_BITBANK_API_KEY", "supersecretkey123456")
        s = Settings()
        # SecretStr は str() でマスクされる
        assert str(s.bitbank_api_key) == "**********"
        assert s.bitbank_api_key.get_secret_value() == "supersecretkey123456"

    def test_kill_switch_inactive_by_default(self) -> None:
        s = Settings()
        assert s.kill_switch.active is False

    def test_circuit_breaker_defaults(self) -> None:
        s = Settings()
        cb = s.circuit_breaker
        assert cb.daily_loss_pct == pytest.approx(0.03)
        assert cb.max_drawdown_pct == pytest.approx(0.15)
        assert cb.max_consecutive_losses == 5

    def test_backtest_defaults(self) -> None:
        s = Settings()
        bt = s.backtest
        assert bt.test_start_index is None
        assert bt.test_set_evaluated is False
        assert bt.test_set_data_hash is None


class TestSettingsFromEnv:
    def test_mode_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRYPT_MODE", "paper")
        s = Settings()
        assert s.mode == "paper"

    def test_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRYPT_BITBANK_API_KEY", "test_key_value")
        s = Settings()
        assert s.bitbank_api_key.get_secret_value() == "test_key_value"

    def test_kill_switch_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRYPT_KILL_SWITCH__ACTIVE", "true")
        s = Settings()
        assert s.kill_switch.active is True


class TestSettingsFromYaml:
    def test_load_from_yaml(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(
            yaml.dump({"mode": "paper", "kill_switch": {"active": False}}),
            encoding="utf-8",
        )
        s = load_settings(yaml_path=yaml_file)
        assert s.mode == "paper"

    def test_yaml_mode_live_is_loaded(self, tmp_path: Path) -> None:
        """YAML の mode: live が実際に反映されること（P0-01 回帰テスト）。"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("mode: live\n", encoding="utf-8")
        s = load_settings(yaml_path=yaml_file)
        assert s.mode == "live"

    def test_yaml_circuit_breaker_is_loaded(self, tmp_path: Path) -> None:
        """YAML のネスト設定 circuit_breaker.daily_loss_pct が反映されること（P0-01 回帰テスト）。"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(
            "circuit_breaker:\n  daily_loss_pct: 0.07\n",
            encoding="utf-8",
        )
        s = load_settings(yaml_path=yaml_file)
        assert s.circuit_breaker.daily_loss_pct == pytest.approx(0.07)

    def test_yaml_cvar_half_pct_is_loaded(self, tmp_path: Path) -> None:
        """YAML の cvar.half_pct が反映されること（ネスト設定 P0-01 回帰テスト）。"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(
            "cvar:\n  warn_pct: -0.02\n  half_pct: -0.03\n  stop_pct: -0.05\n",
            encoding="utf-8",
        )
        s = load_settings(yaml_path=yaml_file)
        assert s.cvar.half_pct == pytest.approx(-0.03)

    def test_env_overrides_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """環境変数が YAML の値より優先されること。"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("mode: live\n", encoding="utf-8")
        monkeypatch.setenv("CRYPT_MODE", "paper")
        s = load_settings(yaml_path=yaml_file)
        assert s.mode == "paper"

    def test_missing_yaml_uses_defaults(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "no_such_file.yaml"
        s = load_settings(yaml_path=nonexistent)
        assert s.mode == "paper"

    def test_yaml_invalid_value_raises(self, tmp_path: Path) -> None:
        """YAML に不正値（circuit_breaker.daily_loss_pct: 0）を渡すと ValidationError になること。"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(
            "circuit_breaker:\n  daily_loss_pct: 0\n",
            encoding="utf-8",
        )
        with pytest.raises(Exception):
            load_settings(yaml_path=yaml_file)


class TestCircuitBreakerValidation:
    def test_zero_pct_raises(self) -> None:
        with pytest.raises(Exception):
            CircuitBreakerSettings(daily_loss_pct=0)

    def test_negative_pct_raises(self) -> None:
        with pytest.raises(Exception):
            CircuitBreakerSettings(max_drawdown_pct=-0.1)

    def test_zero_consecutive_losses_raises(self) -> None:
        with pytest.raises(Exception):
            CircuitBreakerSettings(max_consecutive_losses=0)

    def test_valid_settings_accepted(self) -> None:
        cb = CircuitBreakerSettings(daily_loss_pct=0.05, max_consecutive_losses=3)
        assert cb.daily_loss_pct == pytest.approx(0.05)


class TestCvarValidation:
    def test_positive_values_raise(self) -> None:
        with pytest.raises(Exception):
            CvarSettings(warn_pct=0.01)

    def test_wrong_ordering_raises(self) -> None:
        # warn より stop が大きいのはNG
        with pytest.raises(Exception):
            CvarSettings(warn_pct=-0.05, half_pct=-0.04, stop_pct=-0.03)

    def test_valid_ordering_accepted(self) -> None:
        cv = CvarSettings(warn_pct=-0.02, half_pct=-0.03, stop_pct=-0.05)
        assert cv.warn_pct > cv.half_pct > cv.stop_pct


class TestBacktestSettings:
    def test_test_set_evaluated_immutable_pattern(self) -> None:
        """test_set_evaluated が False → True の方向にしか変えてはいけないことを確認。
        （Phase 1 で実際の保護ロジックを追加する）
        """
        bt = BacktestSettings(test_set_evaluated=True)
        assert bt.test_set_evaluated is True


class TestPaperSettingsMomentum:
    def test_paper_settings_momentum_defaults(self) -> None:
        from cryptbot.config.settings import PaperSettings
        s = PaperSettings()
        assert s.momentum_threshold == 3.0
        assert s.momentum_window == 20
        assert s.timeframe == "15min"

    def test_paper_settings_momentum_configurable(self) -> None:
        from cryptbot.config.settings import PaperSettings
        s = PaperSettings(momentum_threshold=2.5, momentum_window=10)
        assert s.momentum_threshold == 2.5
        assert s.momentum_window == 10

    def test_paper_settings_momentum_threshold_must_be_positive(self) -> None:
        from cryptbot.config.settings import PaperSettings
        with pytest.raises(Exception):
            PaperSettings(momentum_threshold=0.0)

    def test_paper_settings_momentum_window_must_be_positive(self) -> None:
        from cryptbot.config.settings import PaperSettings
        with pytest.raises(Exception):
            PaperSettings(momentum_window=0)


class TestLiveSettingsMomentum:
    def test_live_settings_momentum_defaults(self) -> None:
        from cryptbot.config.settings import LiveSettings
        s = LiveSettings()
        assert s.momentum_threshold == 3.0
        assert s.momentum_window == 20
        assert s.timeframe == "15min"
