"""Shared pytest fixtures for custom_components.hyundai_jp._api tests."""

import pathlib

import pytest

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    """Return the path to the fixtures directory."""
    return FIXTURES_DIR
