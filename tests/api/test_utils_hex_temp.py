import pytest

from custom_components.hyundai_jp._api.utils import get_hex_temp_into_index


@pytest.mark.parametrize(
    ("hex_temp", "index"), [("00H", 0), ("0AH", 10), ("1FH", 31), ("1A0H", 416)]
)
def test_hex_temp_round_trip(hex_temp: str, index: int) -> None:
    assert get_hex_temp_into_index(hex_temp) == index


def test_hex_temp_none() -> None:
    assert get_hex_temp_into_index(None) is None
