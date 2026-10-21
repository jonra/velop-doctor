from pathlib import Path

import pytest

from ethtester.config import Config, load_config


def test_missing_file_gives_defaults(tmp_path: Path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_values_override_defaults(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text('display = "V3"\nexpected_speed = 100\ninterval = 2\n')
    cfg = load_config(path)
    assert cfg.display == "V3"
    assert cfg.expected_speed == 100
    assert cfg.interval == 2
    assert cfg.interface == "eth0"


def test_unknown_keys_are_ignored(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text('colour = "red"\n')
    assert load_config(path) == Config()


def test_invalid_display_rejected(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text('display = "V9"\n')
    with pytest.raises(ValueError, match="display"):
        load_config(path)
