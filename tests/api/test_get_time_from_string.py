"""Unit tests for JapanTelemetry._get_time_from_string.

Covers the unset-timer case (issue #1206): API returns "0000" when no
departure/off-peak timer is configured; the parser must return None
instead of raising ValueError. Also covers the preexisting timesection=None
TypeError and defense-in-depth against unexpected formats.

The parser is independent of authentication, HTTP sessions and HA setup.
"""

import datetime as dt

import pytest


class TestDepartureTimeParsing:
    """Japan legacy timer values map to a time or remain unset."""

    @pytest.mark.parametrize(
        "value,timesection,expected",
        [
            # unset-timer values -> None (issue #1206)
            ("0000", 0, None),
            ("0", 0, None),
            ("", 0, None),
            (0, 0, None),
            (None, 0, None),
            ("0000", None, None),
            # real timers
            ("0800", 0, dt.time(8, 0)),
            ("0800", 1, dt.time(20, 0)),
            ("2200", 0, dt.time(22, 0)),  # >1260 -> %H%M path
            ("1300", 1, dt.time(13, 0)),  # >1260 -> %H%M path
            # preexisting-bug fix: timesection=None no longer crashes
            ("0800", None, dt.time(8, 0)),
            # defense-in-depth: unexpected format -> None (no crash)
            ("ABCD", 0, None),
        ],
    )
    def test_parse(self, telemetry_parser, value, timesection, expected):
        assert telemetry_parser._get_time_from_string(value, timesection) == expected
