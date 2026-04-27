from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from typing import Any

import pytest

from src.backends import postgres
from src.backends.postgres import (
    PostgresSchedulingBackend,
    _get_clinic_now,
    _get_working_days,
    _normalize_date,
    _normalize_time,
    _parse_business_time,
)
from src.errors import (
    AppointmentNotFoundError,
    AppointmentUnavailableError,
    InvalidAppointmentRequestError,
)


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...] | None]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, tuple[Any, ...] | None]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.executed.append((query, params))

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows.pop(0)

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows = self._rows.pop(0)
        assert isinstance(rows, list)
        return rows


class FakeConnection:
    def __init__(self, rows: list[tuple[Any, ...] | None]) -> None:
        self.cursor_obj = FakeCursor(rows)
        self.committed = False

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.committed = True


def patch_connect(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[tuple[Any, ...] | None],
) -> FakeConnection:
    connection = FakeConnection(rows)
    monkeypatch.setattr(postgres.psycopg, "connect", lambda dsn: connection)
    return connection


@pytest.fixture(autouse=True)
def fixed_clinic_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        postgres,
        "_get_clinic_now",
        lambda: datetime(2026, 4, 27, 10, 0),
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-05-01", "2026-05-01"),
        ("05/01/2026", "2026-05-01"),
        ("May 1, 2026", "2026-05-01"),
    ],
)
def test_normalize_date_accepts_common_formats(raw: str, expected: str) -> None:
    assert _normalize_date(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("14:30", "14:30"),
        ("2:30 PM", "14:30"),
        ("2pm", "14:00"),
    ],
)
def test_normalize_time_accepts_common_formats(raw: str, expected: str) -> None:
    assert _normalize_time(raw) == expected


def test_normalize_date_rejects_unparseable_values() -> None:
    with pytest.raises(InvalidAppointmentRequestError, match="Could not parse date"):
        _normalize_date("next-ish Tuesday")


def test_normalize_time_rejects_unparseable_values() -> None:
    with pytest.raises(InvalidAppointmentRequestError, match="Could not parse time"):
        _normalize_time("after lunch")


def test_get_clinic_now_uses_clinic_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLINIC_TIMEZONE", "Europe/Madrid")

    assert _get_clinic_now().tzinfo is not None


def test_backend_uses_database_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/db")

    assert PostgresSchedulingBackend()._dsn == "postgresql://example/db"


def test_backend_uses_default_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert PostgresSchedulingBackend()._dsn == "postgresql://postgres:postgres@localhost:5432/prosper"


def test_find_patient_rejects_incomplete_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        postgres.psycopg,
        "connect",
        lambda dsn: pytest.fail("database should not be queried for incomplete names"),
    )

    assert PostgresSchedulingBackend()._find_patient_sync("Pau", "1996-09-02") is None


def test_find_patient_returns_none_when_no_row(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_connect(monkeypatch, [None])

    assert PostgresSchedulingBackend()._find_patient_sync("Pau Test", "1996-09-02") is None


def test_find_patient_returns_patient(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_connect(monkeypatch, [(123, "Pau", "Test", date(1996, 9, 2))])

    assert PostgresSchedulingBackend()._find_patient_sync(" Pau   Test ", "09/02/1996") == {
        "patient_id": "123",
        "name": "Pau Test",
        "date_of_birth": "1996-09-02",
    }


def test_create_appointment_rejects_unknown_patient(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_connect(monkeypatch, [None])

    with pytest.raises(InvalidAppointmentRequestError, match="does not exist"):
        PostgresSchedulingBackend()._create_appointment_sync("missing", "2026-05-01", "10:00")


def test_create_appointment_rejects_past_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        postgres.psycopg,
        "connect",
        lambda dsn: pytest.fail("database should not be queried for past appointments"),
    )

    with pytest.raises(InvalidAppointmentRequestError, match="Cannot book"):
        PostgresSchedulingBackend()._create_appointment_sync("1", "2026-04-27", "09:59")


def test_create_appointment_rejects_slot_before_clinic_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(AppointmentUnavailableError, match="outside clinic hours"):
        PostgresSchedulingBackend()._create_appointment_sync("1", "2026-05-01", "08:59")


def test_create_appointment_rejects_slot_at_clinic_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(AppointmentUnavailableError, match="outside clinic hours"):
        PostgresSchedulingBackend()._create_appointment_sync("1", "2026-05-01", "17:00")


def test_create_appointment_rejects_weekend_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AppointmentUnavailableError, match="closed on Saturday"):
        PostgresSchedulingBackend()._create_appointment_sync("1", "2026-05-02", "10:00")


def test_create_appointment_uses_configured_business_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLINIC_OPEN_TIME", "10:00")
    monkeypatch.setenv("CLINIC_CLOSE_TIME", "12:00")
    monkeypatch.setenv("CLINIC_WORKING_DAYS", "4,5")
    connection = patch_connect(
        monkeypatch,
        [(1,), None, (7, 1, date(2026, 5, 2), time(10, 30))],
    )

    assert PostgresSchedulingBackend()._create_appointment_sync("1", "2026-05-02", "10:30") == {
        "appointment_id": "7",
        "patient_id": "1",
        "date": "2026-05-02",
        "time": "10:30",
        "status": "scheduled",
    }
    assert connection.committed is True


def test_parse_business_time_rejects_invalid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLINIC_OPEN_TIME", "morning")

    with pytest.raises(InvalidAppointmentRequestError, match="Could not parse time"):
        _parse_business_time("CLINIC_OPEN_TIME", "09:00")


def test_get_working_days_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLINIC_WORKING_DAYS", "1,banana")

    with pytest.raises(InvalidAppointmentRequestError, match="CLINIC_WORKING_DAYS"):
        _get_working_days()


def test_get_working_days_rejects_empty_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLINIC_WORKING_DAYS", " ")

    with pytest.raises(InvalidAppointmentRequestError, match="CLINIC_WORKING_DAYS"):
        _get_working_days()


def test_create_appointment_rejects_invalid_business_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLINIC_OPEN_TIME", "17:00")
    monkeypatch.setenv("CLINIC_CLOSE_TIME", "09:00")

    with pytest.raises(InvalidAppointmentRequestError, match="opening time"):
        PostgresSchedulingBackend()._create_appointment_sync("1", "2026-05-01", "10:00")


def test_create_appointment_rejects_existing_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_connect(monkeypatch, [(1,), (99,)])

    with pytest.raises(AppointmentUnavailableError, match="already booked"):
        PostgresSchedulingBackend()._create_appointment_sync("1", "2026-05-01", "10:00")


def test_create_appointment_returns_none_when_insert_returns_no_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = patch_connect(monkeypatch, [(1,), None, None])

    assert PostgresSchedulingBackend()._create_appointment_sync("1", "2026-05-01", "10:00") is None
    assert connection.committed is True


def test_create_appointment_returns_created_appointment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = patch_connect(
        monkeypatch,
        [(1,), None, (7, 1, date(2026, 5, 1), time(10, 30))],
    )

    assert PostgresSchedulingBackend()._create_appointment_sync("1", "May 1, 2026", "10:30 AM") == {
        "appointment_id": "7",
        "patient_id": "1",
        "date": "2026-05-01",
        "time": "10:30",
        "status": "scheduled",
    }
    assert connection.committed is True


def test_async_methods_delegate_to_sync_methods() -> None:
    backend = PostgresSchedulingBackend()
    backend._find_patient_sync = lambda name, date_of_birth: {"patient_id": "1"}
    backend._create_appointment_sync = lambda patient_id, date_value, time_value: {
        "appointment_id": "7"
    }
    backend._get_patient_appointments_sync = lambda patient_id: [{"appointment_id": "7"}]
    backend._modify_appointment_sync = lambda patient_id, appointment_id, date_value, time_value: {
        "appointment_id": appointment_id,
        "status": "modified",
    }
    backend._cancel_appointment_sync = lambda patient_id, appointment_id: {
        "appointment_id": appointment_id,
        "status": "cancelled",
    }

    assert asyncio.run(backend.find_patient("Pau Test", "1996-09-02")) == {"patient_id": "1"}
    assert asyncio.run(backend.create_appointment("1", "2026-05-01", "10:00")) == {
        "appointment_id": "7"
    }
    assert asyncio.run(backend.get_patient_appointments("1")) == [{"appointment_id": "7"}]
    assert asyncio.run(backend.modify_appointment("1", "7", "2026-05-01", "10:00")) == {
        "appointment_id": "7",
        "status": "modified",
    }
    assert asyncio.run(backend.cancel_appointment("1", "7")) == {
        "appointment_id": "7",
        "status": "cancelled",
    }
    assert asyncio.run(backend.shutdown()) is None


def test_get_patient_appointments_rejects_unknown_patient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_connect(monkeypatch, [None])

    with pytest.raises(InvalidAppointmentRequestError, match="does not exist"):
        PostgresSchedulingBackend()._get_patient_appointments_sync("missing")


def test_get_patient_appointments_returns_ordered_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = patch_connect(
        monkeypatch,
        [
            (1,),
            [
                (7, 1, date(2026, 5, 1), time(10, 30)),
                (8, 1, date(2026, 5, 2), time(9, 0)),
            ],
        ],
    )

    assert PostgresSchedulingBackend()._get_patient_appointments_sync("1") == [
        {
            "appointment_id": "7",
            "patient_id": "1",
            "date": "2026-05-01",
            "time": "10:30",
        },
        {
            "appointment_id": "8",
            "patient_id": "1",
            "date": "2026-05-02",
            "time": "09:00",
        },
    ]
    assert connection.cursor_obj.executed[-1][1] == ("1", "2026-04-27", "2026-04-27", "10:00")


def test_modify_appointment_rejects_missing_appointment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_connect(monkeypatch, [None])

    with pytest.raises(AppointmentNotFoundError, match="was not found"):
        PostgresSchedulingBackend()._modify_appointment_sync("1", "404", "2026-05-01", "10:00")


def test_modify_appointment_rejects_past_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        postgres.psycopg,
        "connect",
        lambda dsn: pytest.fail("database should not be queried for past appointments"),
    )

    with pytest.raises(InvalidAppointmentRequestError, match="Cannot book"):
        PostgresSchedulingBackend()._modify_appointment_sync("1", "7", "2026-04-27", "09:59")


def test_modify_appointment_rejects_out_of_hours_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(AppointmentUnavailableError, match="outside clinic hours"):
        PostgresSchedulingBackend()._modify_appointment_sync("1", "7", "2026-05-01", "08:59")


def test_modify_appointment_rejects_conflicting_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_connect(monkeypatch, [(7,), (8,)])

    with pytest.raises(AppointmentUnavailableError, match="already booked"):
        PostgresSchedulingBackend()._modify_appointment_sync("1", "7", "2026-05-01", "10:00")


def test_modify_appointment_returns_none_when_update_returns_no_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = patch_connect(monkeypatch, [(7,), None, None])

    assert (
        PostgresSchedulingBackend()._modify_appointment_sync("1", "7", "2026-05-01", "10:00")
        is None
    )
    assert connection.committed is True


def test_modify_appointment_returns_modified_appointment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = patch_connect(
        monkeypatch,
        [(7,), None, (7, 1, date(2026, 5, 1), time(10, 30))],
    )

    assert PostgresSchedulingBackend()._modify_appointment_sync(
        "1", "7", "May 1, 2026", "10:30 AM"
    ) == {
        "appointment_id": "7",
        "patient_id": "1",
        "date": "2026-05-01",
        "time": "10:30",
        "status": "modified",
    }
    assert connection.committed is True


def test_cancel_appointment_rejects_missing_appointment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = patch_connect(monkeypatch, [None])

    with pytest.raises(AppointmentNotFoundError, match="was not found"):
        PostgresSchedulingBackend()._cancel_appointment_sync("1", "404")
    assert connection.committed is True


def test_cancel_appointment_returns_cancelled_appointment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = patch_connect(monkeypatch, [(7, 1, date(2026, 5, 1), time(10, 30))])

    assert PostgresSchedulingBackend()._cancel_appointment_sync("1", "7") == {
        "appointment_id": "7",
        "patient_id": "1",
        "date": "2026-05-01",
        "time": "10:30",
        "status": "cancelled",
    }
    assert connection.committed is True
