from __future__ import annotations

from datetime import datetime, timezone

from src.dates import DEFAULT_CLINIC_TIMEZONE, get_clinic_timezone_name, get_current_date_context


def test_get_clinic_timezone_name_defaults_to_madrid(monkeypatch) -> None:
    monkeypatch.delenv("CLINIC_TIMEZONE", raising=False)

    assert get_clinic_timezone_name() == DEFAULT_CLINIC_TIMEZONE


def test_get_clinic_timezone_name_uses_environment(monkeypatch) -> None:
    monkeypatch.setenv("CLINIC_TIMEZONE", "America/New_York")

    assert get_clinic_timezone_name() == "America/New_York"


def test_get_clinic_timezone_name_ignores_blank_environment(monkeypatch) -> None:
    monkeypatch.setenv("CLINIC_TIMEZONE", "   ")

    assert get_clinic_timezone_name() == DEFAULT_CLINIC_TIMEZONE


def test_get_current_date_context_uses_requested_timezone() -> None:
    now = datetime(2026, 4, 27, 22, 30, tzinfo=timezone.utc)

    assert get_current_date_context(now=now, timezone_name="Europe/Madrid") == {
        "timezone": "Europe/Madrid",
        "current_date": "2026-04-28",
        "current_time": "00:30",
        "current_datetime": "2026-04-28T00:30+02:00",
        "weekday": "Tuesday",
    }

