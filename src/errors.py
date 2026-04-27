"""Shared scheduling errors surfaced by backend implementations."""


class SchedulingBackendError(Exception):
    """Base error for scheduling backend failures."""


class AppointmentUnavailableError(SchedulingBackendError):
    """Raised when an appointment slot cannot be booked."""


class AppointmentNotFoundError(SchedulingBackendError):
    """Raised when an appointment does not exist or does not belong to a patient."""


class InvalidAppointmentRequestError(SchedulingBackendError):
    """Raised when a booking request is invalid before reaching the scheduler."""
