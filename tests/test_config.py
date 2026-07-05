"""Config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from triarb.config import load_settings
from triarb.config.models import Settings, Venue

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestExampleConfig:
    def test_example_config_is_valid(self) -> None:
        settings = load_settings(REPO_ROOT / "config" / "config.example.yaml")
        assert settings.venue is Venue.BITGET
        assert settings.quote_currency == "USDT"
        assert settings.triangles.auto_discovery is True
        assert settings.observer.depth_channel == "books15"


class TestValidation:
    def test_defaults(self) -> None:
        settings = Settings()
        assert settings.venue is Venue.BITGET
        assert settings.observer.notional == 100.0

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Settings.model_validate({"observer": {"notionall": 5}})

    def test_unknown_venue_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Settings.model_validate({"venue": "kraken"})

    def test_bad_whitelist_entry_rejected(self) -> None:
        with pytest.raises(ValidationError, match="USDT-BTC-ETH"):
            Settings.model_validate({"triangles": {"whitelist": ["USDT/BTC/ETH"]}})

    def test_quote_currency_uppercased(self) -> None:
        assert Settings.model_validate({"quote_currency": "usdt"}).quote_currency == "USDT"

    def test_non_positive_notional_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Settings.model_validate({"observer": {"notional": 0}})

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_settings(tmp_path / "nope.yaml")

    def test_non_mapping_root_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("- just\n- a\n- list\n")
        with pytest.raises(ValueError, match="must be a mapping"):
            load_settings(path)
