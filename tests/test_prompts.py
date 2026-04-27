from __future__ import annotations

from src.prompts import build_system_prompt


def test_prompt_requires_notice_before_slow_tool_calls() -> None:
    prompt = build_system_prompt()

    assert "never sit in silence" in prompt
    assert "Before calling find_patient" in prompt
    assert "look up the patient record" in prompt
    assert "Before calling create_appointment" in prompt
    assert "checking and booking that slot" in prompt
    assert "checking whether they already have an appointment" in prompt


def test_prompt_mentions_invalid_request_recovery() -> None:
    prompt = build_system_prompt()

    assert "find_patient returns status 'invalid_request'" in prompt
    assert "create_appointment returns status 'invalid_request'" in prompt


def test_prompt_requires_existing_appointment_choice_before_changes() -> None:
    prompt = build_system_prompt()

    assert "modify it, cancel it, or book a separate new appointment" in prompt
    assert "Never modify or cancel an appointment" in prompt
    assert "explicitly chooses" in prompt


def test_prompt_includes_current_date_context_when_provided() -> None:
    prompt = build_system_prompt(
        {
            "weekday": "Monday",
            "current_date": "2026-04-27",
            "current_time": "10:15",
            "timezone": "Europe/Madrid",
        }
    )

    assert "Today is Monday, 2026-04-27" in prompt
    assert "Current clinic time is 10:15" in prompt
    assert "Clinic timezone is Europe/Madrid" in prompt


def test_prompt_requires_current_date_tool_for_relative_dates() -> None:
    prompt = build_system_prompt()

    assert "Never guess today's date from model memory" in prompt
    assert "tomorrow" in prompt
    assert "call get_current_date" in prompt


def test_prompt_rejects_past_appointments() -> None:
    prompt = build_system_prompt()

    assert "Ignore past appointments" in prompt
    assert "Never book or modify an appointment into the past" in prompt
    assert "Do not accept a date/time in the past" in prompt
