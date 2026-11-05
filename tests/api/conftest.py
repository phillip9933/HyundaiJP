"""Shared pytest fixtures for custom_components.hyundai_jp._api tests."""

import pathlib

import pytest

from custom_components.hyundai_jp._api.telemetry import JapanTelemetry

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    """Return the path to the fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def telemetry_parser() -> JapanTelemetry:
    """A real, I/O-free parser with deterministic UTC time and test scale."""
    return JapanTelemetry(temperature_range=tuple(x * 0.5 for x in range(28, 60)))
