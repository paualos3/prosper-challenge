from __future__ import annotations

import asyncio

import pytest

from src import scheduling
from src.backends.postgres import PostgresSchedulingBackend
from src.errors import AppointmentUnavailableError
from src.scheduling import HealthieSchedulingBackend, get_scheduling_backend


def test_get_scheduling_backend_defaults_to_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCHEDULER_BACKEND", raising=False)

    assert isinstance(get_scheduling_backend(), PostgresSchedulingBackend)


def test_get_scheduling_backend_selects_healthie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEDULER_BACKEND", " healthie ")

    assert isinstance(get_scheduling_backend(), HealthieSchedulingBackend)


def test_get_scheduling_backend_selects_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEDULER_BACKEND", " postgres ")

    assert isinstance(get_scheduling_backend(), PostgresSchedulingBackend)


def test_get_scheduling_backend_rejects_unknown_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCHEDULER_BACKEND", "spreadsheet")

    with pytest.raises(ValueError, match="Unsupported SCHEDULER_BACKEND"):
        get_scheduling_backend()


def test_healthie_backend_delegates_patient_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_find_patient(name: str, date_of_birth: str) -> dict[str, str]:
        return {"name": name, "date_of_birth": date_of_birth}

    monkeypatch.setattr(scheduling.healthie, "find_patient", fake_find_patient)

    assert asyncio.run(HealthieSchedulingBackend().find_patient("Pau Test", "1996-09-02")) == {
        "name": "Pau Test",
        "date_of_birth": "1996-09-02",
    }


def test_healthie_backend_delegates_appointment_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_appointment(patient_id: str, date: str, time: str) -> dict[str, str]:
        return {"patient_id": patient_id, "date": date, "time": time}

    monkeypatch.setattr(scheduling.healthie, "create_appointment", fake_create_appointment)

    assert asyncio.run(HealthieSchedulingBackend().create_appointment("1", "2026-05-01", "10:00")) == {
        "patient_id": "1",
        "date": "2026-05-01",
        "time": "10:00",
    }


def test_healthie_backend_delegates_appointment_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_patient_appointments(patient_id: str) -> list[dict[str, str]]:
        return [{"patient_id": patient_id, "appointment_id": "7"}]

    monkeypatch.setattr(
        scheduling.healthie,
        "get_patient_appointments",
        fake_get_patient_appointments,
    )

    assert asyncio.run(HealthieSchedulingBackend().get_patient_appointments("1")) == [
        {"patient_id": "1", "appointment_id": "7"}
    ]


def test_healthie_backend_delegates_appointment_modification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_modify_appointment(
        patient_id: str, appointment_id: str, date: str, time: str
    ) -> dict[str, str]:
        return {
            "patient_id": patient_id,
            "appointment_id": appointment_id,
            "date": date,
            "time": time,
        }

    monkeypatch.setattr(scheduling.healthie, "modify_appointment", fake_modify_appointment)

    assert asyncio.run(
        HealthieSchedulingBackend().modify_appointment("1", "7", "2026-05-01", "10:00")
    ) == {
        "patient_id": "1",
        "appointment_id": "7",
        "date": "2026-05-01",
        "time": "10:00",
    }


def test_healthie_backend_maps_unavailable_appointment_modification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_modify_appointment(
        patient_id: str, appointment_id: str, date: str, time: str
    ) -> None:
        raise scheduling.healthie.AppointmentUnavailableError("slot taken")

    monkeypatch.setattr(scheduling.healthie, "modify_appointment", fake_modify_appointment)

    with pytest.raises(AppointmentUnavailableError, match="slot taken"):
        asyncio.run(
            HealthieSchedulingBackend().modify_appointment("1", "7", "2026-05-01", "10:00")
        )


def test_healthie_backend_delegates_appointment_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_cancel_appointment(patient_id: str, appointment_id: str) -> dict[str, str]:
        return {"patient_id": patient_id, "appointment_id": appointment_id}

    monkeypatch.setattr(scheduling.healthie, "cancel_appointment", fake_cancel_appointment)

    assert asyncio.run(HealthieSchedulingBackend().cancel_appointment("1", "7")) == {
        "patient_id": "1",
        "appointment_id": "7",
    }


def test_healthie_backend_maps_unavailable_appointment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_appointment(patient_id: str, date: str, time: str) -> None:
        raise scheduling.healthie.AppointmentUnavailableError("slot taken")

    monkeypatch.setattr(scheduling.healthie, "create_appointment", fake_create_appointment)

    with pytest.raises(AppointmentUnavailableError, match="slot taken"):
        asyncio.run(HealthieSchedulingBackend().create_appointment("1", "2026-05-01", "10:00"))


def test_healthie_backend_delegates_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def fake_shutdown() -> None:
        calls.append("shutdown")

    monkeypatch.setattr(scheduling.healthie, "shutdown", fake_shutdown)

    assert asyncio.run(HealthieSchedulingBackend().shutdown()) is None
    assert calls == ["shutdown"]
