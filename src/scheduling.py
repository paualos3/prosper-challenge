from __future__ import annotations

import os
from typing import Any, Protocol

from src.backends.postgres import PostgresSchedulingBackend
from src.errors import AppointmentUnavailableError
from src.integrations import healthie


class SchedulingBackend(Protocol):
    async def find_patient(self, name: str, date_of_birth: str) -> dict[str, Any] | None: ...

    async def create_appointment(
        self, patient_id: str, date: str, time: str
    ) -> dict[str, Any] | None: ...

    async def get_patient_appointments(self, patient_id: str) -> list[dict[str, Any]]: ...

    async def modify_appointment(
        self, patient_id: str, appointment_id: str, date: str, time: str
    ) -> dict[str, Any] | None: ...

    async def cancel_appointment(
        self, patient_id: str, appointment_id: str
    ) -> dict[str, Any] | None: ...

    async def shutdown(self) -> None: ...


class HealthieSchedulingBackend:
    async def find_patient(self, name: str, date_of_birth: str) -> dict[str, Any] | None:
        return await healthie.find_patient(name=name, date_of_birth=date_of_birth)

    async def create_appointment(
        self, patient_id: str, date: str, time: str
    ) -> dict[str, Any] | None:
        try:
            return await healthie.create_appointment(patient_id=patient_id, date=date, time=time)
        except healthie.AppointmentUnavailableError as e:
            raise AppointmentUnavailableError(str(e)) from e

    async def get_patient_appointments(self, patient_id: str) -> list[dict[str, Any]]:
        return await healthie.get_patient_appointments(patient_id=patient_id)

    async def modify_appointment(
        self, patient_id: str, appointment_id: str, date: str, time: str
    ) -> dict[str, Any] | None:
        try:
            return await healthie.modify_appointment(
                patient_id=patient_id,
                appointment_id=appointment_id,
                date=date,
                time=time,
            )
        except healthie.AppointmentUnavailableError as e:
            raise AppointmentUnavailableError(str(e)) from e

    async def cancel_appointment(
        self, patient_id: str, appointment_id: str
    ) -> dict[str, Any] | None:
        return await healthie.cancel_appointment(
            patient_id=patient_id,
            appointment_id=appointment_id,
        )

    async def shutdown(self) -> None:
        await healthie.shutdown()


def get_scheduling_backend() -> SchedulingBackend:
    backend = os.getenv("SCHEDULER_BACKEND", "postgres").strip().lower()
    if backend == "healthie":
        return HealthieSchedulingBackend()
    if backend == "postgres":
        return PostgresSchedulingBackend()
    raise ValueError(
        f"Unsupported SCHEDULER_BACKEND={backend!r}. Use 'healthie' or 'postgres'."
    )
