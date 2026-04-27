from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src import tool_handlers
from src.errors import (
    AppointmentNotFoundError,
    AppointmentUnavailableError,
    InvalidAppointmentRequestError,
)
from src.tool_handlers import (
    SchedulingToolHandlers,
    ToolSessionState,
    build_tools_schema,
    register_tool_handlers,
)


class FakeBackend:
    def __init__(self) -> None:
        self.patient = {
            "patient_id": "1",
            "name": "Pau Test",
            "date_of_birth": "1996-09-02",
        }
        self.appointments = [
            {
                "appointment_id": "7",
                "patient_id": "1",
                "date": "2026-05-01",
                "time": "10:00",
            }
        ]
        self.next_error: Exception | None = None
        self.next_patient: dict[str, Any] | None = self.patient
        self.next_appointment: dict[str, Any] | None = {
            "appointment_id": "8",
            "patient_id": "1",
            "date": "2026-05-02",
            "time": "11:00",
        }

    def _raise_if_needed(self) -> None:
        if self.next_error:
            error = self.next_error
            self.next_error = None
            raise error

    async def find_patient(self, name: str, date_of_birth: str) -> dict[str, Any] | None:
        self._raise_if_needed()
        return self.next_patient

    async def get_patient_appointments(self, patient_id: str) -> list[dict[str, Any]]:
        self._raise_if_needed()
        return self.appointments

    async def create_appointment(
        self, patient_id: str, date: str, time: str
    ) -> dict[str, Any] | None:
        self._raise_if_needed()
        return self.next_appointment

    async def modify_appointment(
        self, patient_id: str, appointment_id: str, date: str, time: str
    ) -> dict[str, Any] | None:
        self._raise_if_needed()
        return self.next_appointment

    async def cancel_appointment(
        self, patient_id: str, appointment_id: str
    ) -> dict[str, Any] | None:
        self._raise_if_needed()
        return self.next_appointment

    async def shutdown(self) -> None:
        return None


def run(coro):
    return asyncio.run(coro)


def test_build_tools_schema_includes_expected_tools() -> None:
    names = [tool.name for tool in build_tools_schema().standard_tools]

    assert names == [
        "get_current_date",
        "find_patient",
        "get_patient_appointments",
        "create_appointment",
        "modify_appointment",
        "cancel_appointment",
    ]


def test_register_tool_handlers_registers_and_wraps_functions() -> None:
    backend = FakeBackend()
    handlers = SchedulingToolHandlers(backend)
    registered = {}

    class FakeLLM:
        def register_function(self, name, handler) -> None:
            registered[name] = handler

    class FakeParams:
        arguments = {"name": "Pau Test", "date_of_birth": "1996-09-02"}

        def __init__(self) -> None:
            self.result = None

        async def result_callback(self, result) -> None:
            self.result = result

    register_tool_handlers(FakeLLM(), handlers)
    params = FakeParams()

    run(registered["find_patient"](params))

    assert set(registered) == {
        "get_current_date",
        "find_patient",
        "get_patient_appointments",
        "create_appointment",
        "modify_appointment",
        "cancel_appointment",
    }
    assert params.result["status"] == "ok"


def test_state_tracks_confirmed_patient_and_known_appointments() -> None:
    state = ToolSessionState()
    state.confirm_patient({"patient_id": 1})
    state.remember_appointments([{"appointment_id": 7}, {"appointment_id": None}, {}])

    state.validate_patient("1")
    state.validate_known_appointment("7")
    with pytest.raises(InvalidAppointmentRequestError, match="patient must be confirmed"):
        state.validate_patient("2")
    with pytest.raises(InvalidAppointmentRequestError, match="current appointment list"):
        state.validate_known_appointment("8")


def test_find_patient_success_confirms_patient() -> None:
    backend = FakeBackend()
    handlers = SchedulingToolHandlers(backend)

    result = run(handlers.find_patient({"name": "Pau Test", "date_of_birth": "1996-09-02"}))

    assert result["status"] == "ok"
    assert handlers.state.confirmed_patient_id == "1"


def test_get_current_date_success_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackend()
    handlers = SchedulingToolHandlers(backend)

    monkeypatch.setattr(
        tool_handlers,
        "get_current_date_context",
        lambda: {"current_date": "2026-04-27"},
    )
    assert run(handlers.get_current_date({})) == {
        "status": "ok",
        "date_context": {"current_date": "2026-04-27"},
    }

    def raise_error():
        raise RuntimeError("clock broke")

    monkeypatch.setattr(tool_handlers, "get_current_date_context", raise_error)
    assert run(handlers.get_current_date({}))["status"] == "error"


def test_find_patient_handles_missing_args_and_not_found() -> None:
    backend = FakeBackend()
    handlers = SchedulingToolHandlers(backend)

    assert run(handlers.find_patient({}))["status"] == "invalid_request"
    backend.next_patient = None
    assert (
        run(handlers.find_patient({"name": "Pau Test", "date_of_birth": "1996-09-02"}))[
            "status"
        ]
        == "not_found"
    )


def test_find_patient_maps_backend_errors() -> None:
    backend = FakeBackend()
    handlers = SchedulingToolHandlers(backend)

    backend.next_error = InvalidAppointmentRequestError("bad dob")
    assert (
        run(handlers.find_patient({"name": "Pau Test", "date_of_birth": "bad"}))["status"]
        == "invalid_request"
    )
    backend.next_error = RuntimeError("db down")
    assert (
        run(handlers.find_patient({"name": "Pau Test", "date_of_birth": "1996-09-02"}))[
            "status"
        ]
        == "error"
    )


def test_get_patient_appointments_requires_confirmed_patient() -> None:
    backend = FakeBackend()
    handlers = SchedulingToolHandlers(backend)

    assert run(handlers.get_patient_appointments({}))["status"] == "invalid_request"
    assert (
        run(handlers.get_patient_appointments({"patient_id": "1"}))["status"]
        == "invalid_request"
    )


def test_get_patient_appointments_success_remembers_ids() -> None:
    backend = FakeBackend()
    handlers = SchedulingToolHandlers(backend)
    run(handlers.find_patient({"name": "Pau Test", "date_of_birth": "1996-09-02"}))

    result = run(handlers.get_patient_appointments({"patient_id": "1"}))

    assert result["status"] == "ok"
    assert result["has_existing_appointments"] is True
    assert handlers.state.known_appointment_ids == {"7"}


def test_get_patient_appointments_maps_backend_error() -> None:
    backend = FakeBackend()
    handlers = SchedulingToolHandlers(backend)
    run(handlers.find_patient({"name": "Pau Test", "date_of_birth": "1996-09-02"}))
    backend.next_error = RuntimeError("db down")

    assert run(handlers.get_patient_appointments({"patient_id": "1"}))["status"] == "error"


def test_create_appointment_requires_confirmed_patient_and_args() -> None:
    backend = FakeBackend()
    handlers = SchedulingToolHandlers(backend)

    assert run(handlers.create_appointment({}))["status"] == "invalid_request"
    assert (
        run(handlers.create_appointment({"patient_id": "1", "date": "2026-05-01", "time": "10:00"}))[
            "status"
        ]
        == "invalid_request"
    )


def test_create_appointment_success_and_error_mappings() -> None:
    backend = FakeBackend()
    handlers = SchedulingToolHandlers(backend)
    run(handlers.find_patient({"name": "Pau Test", "date_of_birth": "1996-09-02"}))
    args = {"patient_id": "1", "date": "2026-05-01", "time": "10:00"}

    assert run(handlers.create_appointment(args))["status"] == "ok"
    backend.next_error = AppointmentUnavailableError("slot taken")
    assert run(handlers.create_appointment(args))["status"] == "unavailable"
    backend.next_error = RuntimeError("db down")
    assert run(handlers.create_appointment(args))["status"] == "error"
    backend.next_appointment = None
    assert run(handlers.create_appointment(args))["status"] == "error"


def test_modify_appointment_requires_known_appointment_and_maps_errors() -> None:
    backend = FakeBackend()
    handlers = SchedulingToolHandlers(backend)
    run(handlers.find_patient({"name": "Pau Test", "date_of_birth": "1996-09-02"}))
    args = {"patient_id": "1", "appointment_id": "7", "date": "2026-05-01", "time": "10:00"}

    assert run(handlers.modify_appointment({}))["status"] == "invalid_request"
    assert run(handlers.modify_appointment(args))["status"] == "invalid_request"
    run(handlers.get_patient_appointments({"patient_id": "1"}))
    assert run(handlers.modify_appointment(args))["status"] == "ok"
    backend.next_error = AppointmentNotFoundError("missing")
    assert run(handlers.modify_appointment(args))["status"] == "not_found"
    backend.next_error = AppointmentUnavailableError("slot taken")
    assert run(handlers.modify_appointment(args))["status"] == "unavailable"
    backend.next_error = RuntimeError("db down")
    assert run(handlers.modify_appointment(args))["status"] == "error"
    backend.next_appointment = None
    assert run(handlers.modify_appointment(args))["status"] == "error"


def test_cancel_appointment_requires_known_appointment_and_maps_errors() -> None:
    backend = FakeBackend()
    handlers = SchedulingToolHandlers(backend)
    run(handlers.find_patient({"name": "Pau Test", "date_of_birth": "1996-09-02"}))
    args = {"patient_id": "1", "appointment_id": "7"}

    assert run(handlers.cancel_appointment({}))["status"] == "invalid_request"
    assert run(handlers.cancel_appointment(args))["status"] == "invalid_request"
    run(handlers.get_patient_appointments({"patient_id": "1"}))
    assert run(handlers.cancel_appointment(args))["status"] == "ok"
    backend.next_error = AppointmentNotFoundError("missing")
    assert run(handlers.cancel_appointment(args))["status"] == "not_found"
    backend.next_error = RuntimeError("db down")
    assert run(handlers.cancel_appointment(args))["status"] == "error"
