"""Tests for centralized data paths."""


import pytest

from optionsagents.paths import data_root

pytestmark = pytest.mark.unit


def test_data_root_default():
    assert data_root().endswith(".tradingagents")


def test_data_root_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OPTIONS_DATA_DIR", str(tmp_path / "data"))
    assert data_root() == str(tmp_path / "data")
