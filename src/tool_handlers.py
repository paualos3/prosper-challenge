"""LLM tool schemas and handlers for the scheduling workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams

from src.dates import get_current_date_context
from src.errors import (
    AppointmentNotFoundError,
    AppointmentUnavailableError,
    InvalidAppointmentRequestError,
)
from src.scheduling import SchedulingBackend


@dataclass
class ToolSessionState:
    confirmed_patient_id: str | None = None
    known_appointment_ids: set[str] = field(default_factory=set)

    def confirm_patient(self, patient: dict[str, Any]) -> None:
        self.confirmed_patient_id = str(patient["patient_id"])
        self.known_appointment_ids.clear()

    def remember_appointments(self, appointments: list[dict[str, Any]]) -> None:
        self.known_appointment_ids = {
            str(appointment["appointment_id"])
            for appointment in appointments
            if appointment.get("appointment_id")
        }

    def validate_patient(self, patient_id: str) -> None:
        if patient_id != self.confirmed_patient_id:
            raise InvalidAppointmentRequestError(
                "The patient must be confirmed before managing appointments."
            )

    def validate_known_appointment(self, appointment_id: str) -> None:
        if appointment_id not in self.known_appointment_ids:
            raise InvalidAppointmentRequestError(
                "The appointment must be selected from the current appointment list."
            )


class SchedulingToolHandlers:
    def __init__(
        self,
        scheduling_backend: SchedulingBackend,
        state: ToolSessionState | None = None,
    ) -> None:
        self.scheduling_backend = scheduling_backend
        self.state = state or ToolSessionState()

    async def get_current_date(self, arguments: dict[str, Any]) -> dict[str, Any]:
        logger.info("Tool get_current_date called")
        try:
            return {"status": "ok", "date_context": get_current_date_context()}
        except Exception as e:
            logger.exception("get_current_date failed")
            return {
                "status": "error",
                "message": "Could not determine the current clinic date.",
                "details": str(e),
            }

    async def find_patient(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = arguments.get("name")
        date_of_birth = arguments.get("date_of_birth")
        logger.info(f"Tool find_patient called: name={name!r}, dob={date_of_birth!r}")

        if not name or not date_of_birth:
            return {
                "status": "invalid_request",
                "message": "Both name and date_of_birth are required.",
            }

        try:
            patient = await self.scheduling_backend.find_patient(
                name=name, date_of_birth=date_of_birth
            )
        except InvalidAppointmentRequestError as e:
            return {"status": "invalid_request", "message": str(e)}
        except Exception as e:
            logger.exception("find_patient failed")
            return {
                "status": "error",
                "message": (
                    "Could not reach the scheduling system. Please ask the caller "
                    "to try again shortly."
                ),
                "details": str(e),
            }

        if not patient:
            return {
                "status": "not_found",
                "message": (
                    "No patient matched that name and date of birth. Ask the caller "
                    "to confirm spelling and date."
                ),
            }

        self.state.confirm_patient(patient)
        return {"status": "ok", "patient": patient}

    async def get_patient_appointments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        patient_id = arguments.get("patient_id")
        logger.info(f"Tool get_patient_appointments called: patient_id={patient_id!r}")

        if not patient_id:
            return {"status": "invalid_request", "message": "patient_id is required."}

        try:
            self.state.validate_patient(patient_id)
            appointments = await self.scheduling_backend.get_patient_appointments(
                patient_id=patient_id
            )
        except InvalidAppointmentRequestError as e:
            return {"status": "invalid_request", "message": str(e)}
        except Exception as e:
            logger.exception("get_patient_appointments failed")
            return {
                "status": "error",
                "message": (
                    "Could not check existing appointments. Ask the caller whether "
                    "they want to continue booking or contact the clinic."
                ),
                "details": str(e),
            }

        self.state.remember_appointments(appointments)
        return {
            "status": "ok",
            "appointments": appointments,
            "has_existing_appointments": bool(appointments),
        }

    async def create_appointment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        patient_id = arguments.get("patient_id")
        date = arguments.get("date")
        time = arguments.get("time")
        logger.info(
            f"Tool create_appointment called: patient_id={patient_id!r}, "
            f"date={date!r}, time={time!r}"
        )

        if not patient_id or not date or not time:
            return {
                "status": "invalid_request",
                "message": "patient_id, date, and time are all required.",
            }

        try:
            self.state.validate_patient(patient_id)
            appointment = await self.scheduling_backend.create_appointment(
                patient_id=patient_id, date=date, time=time
            )
        except InvalidAppointmentRequestError as e:
            return {"status": "invalid_request", "message": str(e)}
        except AppointmentUnavailableError as e:
            return {
                "status": "unavailable",
                "message": str(e)
                or "That time slot is unavailable. Ask the caller for another option.",
            }
        except Exception as e:
            logger.exception("create_appointment failed")
            return {
                "status": "error",
                "message": (
                    "Could not create the appointment. Ask the caller to try a "
                    "different time or to call back later."
                ),
                "details": str(e),
            }

        if not appointment:
            return {
                "status": "error",
                "message": (
                    "The appointment could not be created. Ask the caller for an "
                    "alternative slot."
                ),
            }

        return {"status": "ok", "appointment": appointment}

    async def modify_appointment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        patient_id = arguments.get("patient_id")
        appointment_id = arguments.get("appointment_id")
        date = arguments.get("date")
        time = arguments.get("time")
        logger.info(
            f"Tool modify_appointment called: patient_id={patient_id!r}, "
            f"appointment_id={appointment_id!r}, date={date!r}, time={time!r}"
        )

        if not patient_id or not appointment_id or not date or not time:
            return {
                "status": "invalid_request",
                "message": "patient_id, appointment_id, date, and time are all required.",
            }

        try:
            self.state.validate_patient(patient_id)
            self.state.validate_known_appointment(appointment_id)
            appointment = await self.scheduling_backend.modify_appointment(
                patient_id=patient_id,
                appointment_id=appointment_id,
                date=date,
                time=time,
            )
        except InvalidAppointmentRequestError as e:
            return {"status": "invalid_request", "message": str(e)}
        except AppointmentNotFoundError as e:
            return {"status": "not_found", "message": str(e)}
        except AppointmentUnavailableError as e:
            return {
                "status": "unavailable",
                "message": str(e)
                or "That time slot is unavailable. Ask the caller for another option.",
            }
        except Exception as e:
            logger.exception("modify_appointment failed")
            return {
                "status": "error",
                "message": (
                    "Could not modify the appointment. Ask the caller to try a "
                    "different time or to contact the clinic."
                ),
                "details": str(e),
            }

        if not appointment:
            return {"status": "error", "message": "The appointment could not be modified."}

        return {"status": "ok", "appointment": appointment}

    async def cancel_appointment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        patient_id = arguments.get("patient_id")
        appointment_id = arguments.get("appointment_id")
        logger.info(
            f"Tool cancel_appointment called: patient_id={patient_id!r}, "
            f"appointment_id={appointment_id!r}"
        )

        if not patient_id or not appointment_id:
            return {
                "status": "invalid_request",
                "message": "patient_id and appointment_id are required.",
            }

        try:
            self.state.validate_patient(patient_id)
            self.state.validate_known_appointment(appointment_id)
            appointment = await self.scheduling_backend.cancel_appointment(
                patient_id=patient_id,
                appointment_id=appointment_id,
            )
        except InvalidAppointmentRequestError as e:
            return {"status": "invalid_request", "message": str(e)}
        except AppointmentNotFoundError as e:
            return {"status": "not_found", "message": str(e)}
        except Exception as e:
            logger.exception("cancel_appointment failed")
            return {
                "status": "error",
                "message": (
                    "Could not cancel the appointment. Ask the caller to contact "
                    "the clinic."
                ),
                "details": str(e),
            }

        return {"status": "ok", "appointment": appointment}


def build_tools_schema() -> ToolsSchema:
    return ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name="get_current_date",
                description=(
                    "Get today's date, current time, weekday, and timezone for the clinic. "
                    "Use this before converting relative appointment requests like today, "
                    "tomorrow, next Friday, or in two weeks into ISO dates."
                ),
                properties={},
                required=[],
            ),
            FunctionSchema(
                name="find_patient",
                description=(
                    "Look up a patient in the scheduling system by full name and date of birth. "
                    "Call this only after you have collected and confirmed both fields with "
                    "the caller, and after telling the caller you are going to look up the "
                    "record."
                ),
                properties={
                    "name": {
                        "type": "string",
                        "description": (
                            "The patient's full name as confirmed by the caller "
                            "(e.g. 'Jane Doe')."
                        ),
                    },
                    "date_of_birth": {
                        "type": "string",
                        "description": "The patient's date of birth in ISO format YYYY-MM-DD.",
                    },
                },
                required=["name", "date_of_birth"],
            ),
            FunctionSchema(
                name="get_patient_appointments",
                description=(
                    "List existing appointments for an identified patient. Call this after "
                    "find_patient succeeds and before asking for a new appointment time."
                ),
                properties={
                    "patient_id": {
                        "type": "string",
                        "description": "patient_id returned from a successful find_patient call.",
                    },
                },
                required=["patient_id"],
            ),
            FunctionSchema(
                name="create_appointment",
                description=(
                    "Create a new appointment in the scheduling system for an already-identified "
                    "patient. Only call after find_patient has succeeded and the caller "
                    "has confirmed the desired date and time, and after telling the caller you "
                    "are checking and booking the slot."
                ),
                properties={
                    "patient_id": {
                        "type": "string",
                        "description": "patient_id returned from a successful find_patient call.",
                    },
                    "date": {
                        "type": "string",
                        "description": "Appointment date in ISO format YYYY-MM-DD.",
                    },
                    "time": {
                        "type": "string",
                        "description": (
                            "Appointment start time in 24-hour HH:MM format "
                            "(local clinic time)."
                        ),
                    },
                },
                required=["patient_id", "date", "time"],
            ),
            FunctionSchema(
                name="modify_appointment",
                description=(
                    "Modify an existing appointment for an identified patient. Only call after "
                    "the caller has heard the existing appointment details, chosen to modify it, "
                    "and confirmed the new date and time."
                ),
                properties={
                    "patient_id": {
                        "type": "string",
                        "description": "patient_id returned from a successful find_patient call.",
                    },
                    "appointment_id": {
                        "type": "string",
                        "description": "appointment_id returned by get_patient_appointments.",
                    },
                    "date": {
                        "type": "string",
                        "description": "New appointment date in ISO format YYYY-MM-DD.",
                    },
                    "time": {
                        "type": "string",
                        "description": "New appointment start time in 24-hour HH:MM format.",
                    },
                },
                required=["patient_id", "appointment_id", "date", "time"],
            ),
            FunctionSchema(
                name="cancel_appointment",
                description=(
                    "Cancel an existing appointment for an identified patient. Only call after "
                    "the caller has explicitly confirmed they want to cancel that appointment."
                ),
                properties={
                    "patient_id": {
                        "type": "string",
                        "description": "patient_id returned from a successful find_patient call.",
                    },
                    "appointment_id": {
                        "type": "string",
                        "description": "appointment_id returned by get_patient_appointments.",
                    },
                },
                required=["patient_id", "appointment_id"],
            ),
        ]
    )


def register_tool_handlers(
    llm: Any,
    handlers: SchedulingToolHandlers,
) -> None:
    def wrap(
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> Callable[[FunctionCallParams], Awaitable[None]]:
        async def wrapped(params: FunctionCallParams) -> None:
            result = await handler(params.arguments)
            await params.result_callback(result)

        return wrapped

    llm.register_function("get_current_date", wrap(handlers.get_current_date))
    llm.register_function("find_patient", wrap(handlers.find_patient))
    llm.register_function(
        "get_patient_appointments",
        wrap(handlers.get_patient_appointments),
    )
    llm.register_function("create_appointment", wrap(handlers.create_appointment))
    llm.register_function("modify_appointment", wrap(handlers.modify_appointment))
    llm.register_function("cancel_appointment", wrap(handlers.cancel_appointment))
