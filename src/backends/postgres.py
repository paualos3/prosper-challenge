from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from loguru import logger

from src.dates import get_clinic_timezone_name
from src.errors import (
    AppointmentNotFoundError,
    AppointmentUnavailableError,
    InvalidAppointmentRequestError,
)

DEFAULT_CLINIC_OPEN_TIME = "09:00"
DEFAULT_CLINIC_CLOSE_TIME = "17:00"
DEFAULT_CLINIC_WORKING_DAYS = "0,1,2,3,4"


def _normalize_date(value: str) -> str:
    value = value.strip()
    formats = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%B %d, %Y",
        "%b %d, %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise InvalidAppointmentRequestError(f"Could not parse date: {value!r}")


def _normalize_time(value: str) -> str:
    value = value.strip().upper().replace(".", "")
    formats = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p")
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime("%H:%M")
        except ValueError:
            continue
    raise InvalidAppointmentRequestError(f"Could not parse time: {value!r}")


def _get_clinic_now() -> datetime:
    return datetime.now(ZoneInfo(get_clinic_timezone_name()))


def _ensure_slot_is_not_in_past(normalized_date: str, normalized_time: str) -> None:
    slot = datetime.strptime(f"{normalized_date} {normalized_time}", "%Y-%m-%d %H:%M")
    now = _get_clinic_now().replace(tzinfo=None, second=0, microsecond=0)
    if slot < now:
        raise InvalidAppointmentRequestError(
            f"Cannot book an appointment in the past: {normalized_date} {normalized_time}"
        )


def _parse_business_time(env_name: str, default: str) -> str:
    return _normalize_time(os.getenv(env_name, default))


def _get_working_days() -> set[int]:
    raw_days = os.getenv("CLINIC_WORKING_DAYS", DEFAULT_CLINIC_WORKING_DAYS)
    try:
        days = {int(day.strip()) for day in raw_days.split(",") if day.strip()}
    except ValueError as e:
        raise InvalidAppointmentRequestError(
            "CLINIC_WORKING_DAYS must contain comma-separated integers from 0 to 6"
        ) from e

    if not days or any(day < 0 or day > 6 for day in days):
        raise InvalidAppointmentRequestError(
            "CLINIC_WORKING_DAYS must contain comma-separated integers from 0 to 6"
        )
    return days


def _ensure_slot_is_during_business_hours(normalized_date: str, normalized_time: str) -> None:
    slot_date = datetime.strptime(normalized_date, "%Y-%m-%d").date()
    if slot_date.weekday() not in _get_working_days():
        raise AppointmentUnavailableError(
            f"The clinic is closed on {slot_date.strftime('%A')}. Ask for a weekday slot."
        )

    open_time = _parse_business_time("CLINIC_OPEN_TIME", DEFAULT_CLINIC_OPEN_TIME)
    close_time = _parse_business_time("CLINIC_CLOSE_TIME", DEFAULT_CLINIC_CLOSE_TIME)
    if open_time >= close_time:
        raise InvalidAppointmentRequestError("Clinic opening time must be before closing time")

    if not (open_time <= normalized_time < close_time):
        raise AppointmentUnavailableError(
            f"Slot {normalized_date} {normalized_time} is outside clinic hours "
            f"({open_time}-{close_time})"
        )


def _validate_bookable_slot(normalized_date: str, normalized_time: str) -> None:
    _ensure_slot_is_not_in_past(normalized_date, normalized_time)
    _ensure_slot_is_during_business_hours(normalized_date, normalized_time)


class PostgresSchedulingBackend:
    def __init__(self) -> None:
        self._dsn = os.getenv(
            "DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/prosper",
        )

    async def find_patient(self, name: str, date_of_birth: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._find_patient_sync, name, date_of_birth)

    async def create_appointment(
        self, patient_id: str, date: str, time: str
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._create_appointment_sync, patient_id, date, time)

    async def get_patient_appointments(self, patient_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._get_patient_appointments_sync, patient_id)

    async def modify_appointment(
        self, patient_id: str, appointment_id: str, date: str, time: str
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._modify_appointment_sync, patient_id, appointment_id, date, time
        )

    async def cancel_appointment(
        self, patient_id: str, appointment_id: str
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._cancel_appointment_sync, patient_id, appointment_id
        )

    async def shutdown(self) -> None:
        return

    def _find_patient_sync(self, name: str, date_of_birth: str) -> dict[str, Any] | None:
        normalized_dob = _normalize_date(date_of_birth)
        parts = [part for part in name.strip().split() if part]
        if len(parts) < 2:
            logger.info(f"Postgres patient lookup rejected incomplete name={name!r}")
            return None
        first_name = parts[0]
        last_name = " ".join(parts[1:])

        query = """
            SELECT id, first_name, last_name, date_of_birth
            FROM patients
            WHERE lower(first_name) = lower(%s)
              AND lower(last_name) = lower(%s)
              AND date_of_birth = %s::date
            LIMIT 1
        """
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (first_name, last_name, normalized_dob))
                row = cur.fetchone()
                if not row:
                    logger.info(
                        f"Postgres patient not found for first_name={first_name!r} "
                        f"last_name={last_name!r} dob={normalized_dob!r}"
                    )
                    return None
                patient_id, p_first_name, p_last_name, dob = row
                return {
                    "patient_id": str(patient_id),
                    "name": f"{p_first_name} {p_last_name}",
                    "date_of_birth": dob.isoformat(),
                }

    def _create_appointment_sync(
        self, patient_id: str, date: str, time: str
    ) -> dict[str, Any] | None:
        normalized_date = _normalize_date(date)
        normalized_time = _normalize_time(time)
        _validate_bookable_slot(normalized_date, normalized_time)

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM patients WHERE id = %s", (patient_id,))
                if not cur.fetchone():
                    raise InvalidAppointmentRequestError(
                        f"Patient id {patient_id!r} does not exist"
                    )

                cur.execute(
                    """
                    SELECT id FROM appointments
                    WHERE appointment_date = %s::date
                      AND appointment_time = %s::time
                    LIMIT 1
                    """,
                    (normalized_date, normalized_time),
                )
                existing = cur.fetchone()
                if existing:
                    raise AppointmentUnavailableError(
                        f"Slot {normalized_date} {normalized_time} is already booked"
                    )

                cur.execute(
                    """
                    INSERT INTO appointments (patient_id, appointment_date, appointment_time)
                    VALUES (%s, %s::date, %s::time)
                    RETURNING id, patient_id, appointment_date, appointment_time
                    """,
                    (patient_id, normalized_date, normalized_time),
                )
                row = cur.fetchone()
                conn.commit()
                if not row:
                    return None
                appointment_id, out_patient_id, out_date, out_time = row
                return {
                    "appointment_id": str(appointment_id),
                    "patient_id": str(out_patient_id),
                    "date": out_date.isoformat(),
                    "time": out_time.strftime("%H:%M"),
                    "status": "scheduled",
                }

    def _get_patient_appointments_sync(self, patient_id: str) -> list[dict[str, Any]]:
        now = _get_clinic_now()
        current_date = now.date().isoformat()
        current_time = now.strftime("%H:%M")

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM patients WHERE id = %s", (patient_id,))
                if not cur.fetchone():
                    raise InvalidAppointmentRequestError(
                        f"Patient id {patient_id!r} does not exist"
                    )

                cur.execute(
                    """
                    SELECT id, patient_id, appointment_date, appointment_time
                    FROM appointments
                    WHERE patient_id = %s
                      AND (
                        appointment_date > %s::date
                        OR (
                          appointment_date = %s::date
                          AND appointment_time >= %s::time
                        )
                      )
                    ORDER BY appointment_date, appointment_time
                    """,
                    (
                        patient_id,
                        current_date,
                        current_date,
                        current_time,
                    ),
                )
                rows = cur.fetchall()
                return [_appointment_row_to_dict(row) for row in rows]

    def _modify_appointment_sync(
        self, patient_id: str, appointment_id: str, date: str, time: str
    ) -> dict[str, Any] | None:
        normalized_date = _normalize_date(date)
        normalized_time = _normalize_time(time)
        _validate_bookable_slot(normalized_date, normalized_time)

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM appointments
                    WHERE id = %s AND patient_id = %s
                    LIMIT 1
                    """,
                    (appointment_id, patient_id),
                )
                if not cur.fetchone():
                    raise AppointmentNotFoundError(
                        f"Appointment id {appointment_id!r} was not found for this patient"
                    )

                cur.execute(
                    """
                    SELECT id FROM appointments
                    WHERE appointment_date = %s::date
                      AND appointment_time = %s::time
                      AND id <> %s
                    LIMIT 1
                    """,
                    (normalized_date, normalized_time, appointment_id),
                )
                if cur.fetchone():
                    raise AppointmentUnavailableError(
                        f"Slot {normalized_date} {normalized_time} is already booked"
                    )

                cur.execute(
                    """
                    UPDATE appointments
                    SET appointment_date = %s::date,
                        appointment_time = %s::time
                    WHERE id = %s AND patient_id = %s
                    RETURNING id, patient_id, appointment_date, appointment_time
                    """,
                    (normalized_date, normalized_time, appointment_id, patient_id),
                )
                row = cur.fetchone()
                conn.commit()
                if not row:
                    return None
                appointment = _appointment_row_to_dict(row)
                appointment["status"] = "modified"
                return appointment

    def _cancel_appointment_sync(
        self, patient_id: str, appointment_id: str
    ) -> dict[str, Any] | None:
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM appointments
                    WHERE id = %s AND patient_id = %s
                    RETURNING id, patient_id, appointment_date, appointment_time
                    """,
                    (appointment_id, patient_id),
                )
                row = cur.fetchone()
                conn.commit()
                if not row:
                    raise AppointmentNotFoundError(
                        f"Appointment id {appointment_id!r} was not found for this patient"
                    )
                appointment = _appointment_row_to_dict(row)
                appointment["status"] = "cancelled"
                return appointment


def _appointment_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    appointment_id, patient_id, appointment_date, appointment_time = row
    return {
        "appointment_id": str(appointment_id),
        "patient_id": str(patient_id),
        "date": appointment_date.isoformat(),
        "time": appointment_time.strftime("%H:%M"),
    }
