"""Date helpers for resolving relative scheduling requests."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_CLINIC_TIMEZONE = "Europe/Madrid"


def get_clinic_timezone_name() -> str:
    return (
        os.getenv("CLINIC_TIMEZONE", DEFAULT_CLINIC_TIMEZONE).strip()
        or DEFAULT_CLINIC_TIMEZONE
    )


def get_current_date_context(
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    timezone = ZoneInfo(timezone_name or get_clinic_timezone_name())
    localized_now = now.astimezone(timezone) if now else datetime.now(timezone)

    return {
        "timezone": timezone.key,
        "current_date": localized_now.date().isoformat(),
        "current_time": localized_now.strftime("%H:%M"),
        "current_datetime": localized_now.isoformat(timespec="minutes"),
        "weekday": localized_now.strftime("%A"),
    }
