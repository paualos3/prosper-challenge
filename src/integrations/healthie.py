"""Healthie EHR integration module.

This module provides functions to interact with Healthie for patient management
and appointment scheduling. The integration is driven through the Healthie web
application (secure.gethealthie.com) using a Playwright headless browser, which
keeps things working with the same email/password credentials a clinician would
use to sign in.

The browser session is initialized once per process and reused across calls. A
single asyncio.Lock serializes page operations so that concurrent tool calls
from the voice agent never step on each other.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

HEALTHIE_BASE_URL = "https://secure.gethealthie.com"

_browser: Browser | None = None
_context: BrowserContext | None = None
_page: Page | None = None
_session_lock = asyncio.Lock()
_artifacts_dir = Path(".artifacts/healthie")


class HealthieError(Exception):
    """Base error for Healthie integration failures."""


class AppointmentUnavailableError(HealthieError):
    """Raised when an appointment slot cannot be booked (conflict, blocked, etc.)."""


async def _capture_debug_screenshot(page: Page | None, label: str) -> str | None:
    """Capture a screenshot for debugging failed scraping steps.

    Returns:
        str | None: Screenshot path if captured successfully, otherwise None.
    """
    if page is None or page.is_closed():
        logger.debug(f"Skipping screenshot for {label!r}: page unavailable")
        return None

    _artifacts_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-").lower() or "unknown"
    screenshot_path = _artifacts_dir / f"{timestamp}-{safe_label}.png"

    try:
        await page.screenshot(path=str(screenshot_path), full_page=True)
        logger.warning(f"Captured debug screenshot: {screenshot_path}")
        return str(screenshot_path)
    except Exception as e:
        logger.warning(f"Failed to capture debug screenshot for {label!r}: {e}")
        return None


async def login_to_healthie() -> Page:
    """Log into Healthie and return an authenticated page instance.

    The browser, context, and page are stored at module scope so subsequent calls
    reuse the same session. Calls made before the first login will trigger the
    login flow; afterwards the cached page is returned.

    Returns:
        Page: An authenticated Playwright Page instance.

    Raises:
        ValueError: If required environment variables are missing.
        HealthieError: If login fails for any reason.
    """
    global _browser, _context, _page

    email = os.environ.get("HEALTHIE_EMAIL")
    password = os.environ.get("HEALTHIE_PASSWORD")

    if not email or not password:
        raise ValueError(
            "HEALTHIE_EMAIL and HEALTHIE_PASSWORD must be set in environment variables"
        )

    if _page is not None and not _page.is_closed():
        return _page

    logger.info("Logging into Healthie...")
    playwright = await async_playwright().start()
    _browser = await playwright.chromium.launch(headless=True)
    _context = await _browser.new_context()
    _page = await _context.new_page()

    await _page.goto(
        f"{HEALTHIE_BASE_URL}/users/login", wait_until="domcontentloaded"
    )

    email_input = _page.locator('input[name="identifier"]')
    await email_input.wait_for(state="visible", timeout=30000)
    await email_input.fill(email)

    submit_button = _page.locator('button:has-text("Log In")')
    await submit_button.wait_for(state="visible", timeout=30000)
    await submit_button.click()

    password_input = _page.locator('input[name="password"]')
    await password_input.wait_for(state="visible", timeout=30000)
    await password_input.fill(password)

    submit_button = _page.locator('button:has-text("Log In")')
    await submit_button.wait_for(state="visible", timeout=30000)
    await submit_button.click()

    # Some accounts show an intermediate gate after login credentials.
    continue_to_app = _page.locator(
        '[data-test-id="passkeys-continue-to-app"], '
        'button[aria-label="Continue to app"], '
        'button:has-text("Continue to app")'
    )
    try:
        await continue_to_app.wait_for(state="visible", timeout=10000)
        logger.info("Detected post-login gate, clicking 'Continue to app'")
        await continue_to_app.click()
    except PlaywrightTimeoutError:
        # Normal path for accounts that do not show this gate.
        pass

    try:
        await _page.wait_for_url(
            lambda url: "login" not in url, timeout=15000
        )
    except PlaywrightTimeoutError:
        # Fall back to a short wait so we can re-check the URL ourselves.
        await _page.wait_for_timeout(2000)

    if "login" in _page.url:
        logger.error(
            f"Login failed - still on login page, current url={_page.url!r}"
        )
        await _capture_debug_screenshot(_page, "login-failed-still-signin")
        raise HealthieError("Login failed - still on sign-in page after submit")

    logger.info(f"Successfully logged into Healthie, current url={_page.url!r}")
    return _page


async def _ensure_page() -> Page:
    """Return an authenticated page, logging in if necessary."""
    return await login_to_healthie()


def _normalize_date(value: str) -> str:
    """Coerce a date string to ISO format YYYY-MM-DD.

    Accepts a few common variants the LLM may produce (ISO, US, dotted, etc.)
    and raises ValueError if it cannot be parsed.
    """
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
    raise ValueError(f"Could not parse date: {value!r}")


def _normalize_time(value: str) -> str:
    """Coerce a time string to 24-hour HH:MM."""
    value = value.strip().upper().replace(".", "")
    formats = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p")
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime("%H:%M")
        except ValueError:
            continue
    raise ValueError(f"Could not parse time: {value!r}")


def _extract_patient_id(href: str) -> str | None:
    """Extract a Healthie client/user id from a profile URL like /users/12345."""
    match = re.search(r"/users/(\d+)", href)
    return match.group(1) if match else None


async def find_patient(name: str, date_of_birth: str) -> dict[str, Any] | None:
    """Find a patient in Healthie by name and date of birth.

    The function searches the Healthie clients list for the provided name, then
    visits each candidate's profile until it finds one whose date of birth
    matches the provided value. This avoids returning a different patient that
    happens to share a name.

    Args:
        name: The patient's full name (first + last).
        date_of_birth: The patient's date of birth. Accepts ISO and common US
            formats; normalized to YYYY-MM-DD before matching.

    Returns:
        dict | None: A dictionary with at least ``patient_id``, ``name``, and
        ``date_of_birth`` if a unique match is found. ``None`` if no match
        could be located.

    Raises:
        HealthieError: If the underlying Healthie session cannot be used.
    """
    target_dob = _normalize_date(date_of_birth)
    cleaned_name = " ".join(name.strip().split())
    logger.debug(
        f"find_patient called with raw_name={name!r}, cleaned_name={cleaned_name!r}, "
        f"raw_dob={date_of_birth!r}, normalized_dob={target_dob!r}"
    )

    async with _session_lock:
        logger.debug("Acquired Healthie session lock for find_patient")
        page = await _ensure_page()

        logger.info(f"Searching Healthie for patient name={cleaned_name!r}")
        logger.debug("Navigating to clients list page")
        await page.goto(
            f"{HEALTHIE_BASE_URL}/clients", wait_until="domcontentloaded"
        )
        logger.debug(f"Clients page loaded url={page.url!r}")

        search_selector = 'input[name="keywords"], input[type="text"]'
        search_input = page.locator(
            search_selector
        ).first
        try:
            logger.debug(f"Waiting for clients search input selector: {search_selector}")
            await search_input.wait_for(state="visible", timeout=15000)
        except PlaywrightTimeoutError:
            await _capture_debug_screenshot(page, "find-patient-search-input-timeout")
            logger.warning(
                f"Could not locate clients search box selector={search_selector!r} "
                f"url={page.url!r}"
            )
            return None

        logger.debug(f"Filling clients search input with query={cleaned_name!r}")
        await search_input.fill(cleaned_name)
        # Healthie's clients list filters as you type; give it a moment to settle.
        await page.wait_for_timeout(1500)

        candidate_selector = 'a[href*="/users/"]'
        candidate_links = page.locator(candidate_selector)
        count = await candidate_links.count()
        logger.debug(f"Found {count} candidate links using selector={candidate_selector!r}")
        logger.debug(f"Candidate links: {candidate_links}")
        if count == 0:
            logger.info("No Healthie candidates returned for that name")
            return None

        seen_ids: set[str] = set()
        for i in range(min(count, 10)):
            link = candidate_links.nth(i)
            href = await link.get_attribute("href") or ""
            patient_id = _extract_patient_id(href)
            if not patient_id or patient_id in seen_ids:
                logger.debug(
                    f"Skipping candidate index={i} href={href!r} "
                    f"patient_id={patient_id!r} already_seen={patient_id in seen_ids if patient_id else False}"
                )
                continue
            seen_ids.add(patient_id)

            displayed = (await link.inner_text()).strip()
            logger.debug(f"Inspecting candidate {patient_id} ({displayed!r})")

            profile = await _open_patient_profile(patient_id)
            if profile is None:
                logger.debug(f"Candidate {patient_id} discarded: could not parse profile")
                continue
            if profile["date_of_birth"] == target_dob:
                logger.info(
                    f"Matched patient_id={patient_id} for {cleaned_name!r} ({target_dob})"
                )
                return profile
            logger.debug(
                f"Candidate {patient_id} DOB mismatch: found={profile['date_of_birth']!r}, "
                f"expected={target_dob!r}"
            )

        logger.info(
            f"No Healthie candidate matched DOB {target_dob} for name {cleaned_name!r}"
        )
        return None


async def _open_patient_profile(patient_id: str) -> dict[str, Any] | None:
    """Visit a patient profile and extract identifying fields.

    Returns a dict with ``patient_id``, ``name``, and ``date_of_birth`` (ISO
    formatted) or ``None`` if the profile could not be parsed.
    """
    page = await _ensure_page()
    logger.debug(f"Opening patient profile for patient_id={patient_id}")
    await page.goto(
        f"{HEALTHIE_BASE_URL}/users/{patient_id}", wait_until="domcontentloaded"
    )
    logger.debug(f"Patient profile page loaded url={page.url!r}")

    # Wait for the profile content to render. The profile heading typically
    # contains the patient's full name.
    try:
        logger.debug("Waiting for profile headings selector: h1, h2")
        await page.wait_for_selector("h1, h2", timeout=10000)
    except PlaywrightTimeoutError:
        await _capture_debug_screenshot(page, f"profile-render-timeout-{patient_id}")
        logger.warning(
            f"Profile {patient_id} did not render in time at url={page.url!r}"
        )
        return None

    body_text = await page.locator("body").inner_text()

    name = await _extract_profile_name(page)
    dob = _extract_profile_dob(body_text)

    if not name:
        logger.debug(f"Could not extract name for patient {patient_id}")
    if not dob:
        logger.debug(f"Could not extract DOB for patient {patient_id}")

    if not dob:
        return None

    return {
        "patient_id": patient_id,
        "name": name or "",
        "date_of_birth": dob,
    }


async def _extract_profile_name(page: Page) -> str | None:
    for selector in ("h1", "h2", '[data-testid*="client-name" i]'):
        loc = page.locator(selector).first
        try:
            logger.debug(f"Trying profile name selector={selector!r}")
            if await loc.count() == 0:
                continue
            text = (await loc.inner_text()).strip()
            if text and len(text) < 120:
                logger.debug(f"Extracted profile name={text!r} using selector={selector!r}")
                return text
        except Exception as e:
            logger.debug(f"Failed reading selector={selector!r}: {e}")
            continue
    return None


_DOB_LABEL_RE = re.compile(
    r"(?:date\s+of\s+birth|dob|birthday)\s*[:\-]?\s*([A-Za-z0-9 ,/\-]+)",
    re.IGNORECASE,
)


def _extract_profile_dob(body_text: str) -> str | None:
    match = _DOB_LABEL_RE.search(body_text)
    if not match:
        logger.debug("Could not locate DOB label in profile body text")
        return None
    raw = match.group(1).split("\n")[0].strip().rstrip(",")
    logger.debug(f"DOB raw extracted value={raw!r}")
    try:
        normalized = _normalize_date(raw)
        logger.debug(f"DOB normalized directly to {normalized!r}")
        return normalized
    except ValueError:
        # Sometimes the value is followed by extra text; try the first 10 chars.
        try:
            normalized = _normalize_date(raw[:10])
            logger.debug(f"DOB normalized using fallback slice to {normalized!r}")
            return normalized
        except ValueError:
            logger.debug("Failed to normalize DOB from profile text")
            return None


async def create_appointment(
    patient_id: str, date: str, time: str
) -> dict[str, Any] | None:
    """Create an appointment in Healthie for the specified patient.

    Args:
        patient_id: Healthie client/user id (returned by ``find_patient``).
        date: Desired appointment date. Accepts ISO and common variants.
        time: Desired appointment start time. Accepts 24-hour or AM/PM.

    Returns:
        dict | None: A dictionary describing the created appointment with at
        least ``appointment_id``, ``patient_id``, ``date``, and ``time``.
        Returns ``None`` if the booking could not be confirmed.

    Raises:
        AppointmentUnavailableError: If the slot is unavailable / conflicting.
        HealthieError: For other Healthie-side failures.
    """
    iso_date = _normalize_date(date)
    iso_time = _normalize_time(time)
    logger.debug(
        f"create_appointment called with patient_id={patient_id!r}, "
        f"raw_date={date!r}, raw_time={time!r}, normalized_date={iso_date!r}, "
        f"normalized_time={iso_time!r}"
    )

    async with _session_lock:
        logger.debug("Acquired Healthie session lock for create_appointment")
        page = await _ensure_page()

        logger.info(
            f"Creating Healthie appointment patient_id={patient_id} "
            f"date={iso_date} time={iso_time}"
        )

        await page.goto(
            f"{HEALTHIE_BASE_URL}/users/{patient_id}", wait_until="domcontentloaded"
        )
        logger.debug(f"Patient profile for booking loaded url={page.url!r}")

        await _open_new_appointment_dialog(page)
        await _fill_appointment_form(page, iso_date, iso_time)
        await _submit_appointment_form(page)

        confirmation_text = await _await_appointment_confirmation(page)
        logger.debug(f"Appointment confirmation text={confirmation_text!r}")
        appointment_id = _parse_appointment_id(confirmation_text or "", page.url)
        logger.debug(f"Parsed appointment_id={appointment_id!r} from url={page.url!r}")

        return {
            "appointment_id": appointment_id,
            "patient_id": patient_id,
            "date": iso_date,
            "time": iso_time,
            "status": "scheduled",
        }


async def _open_new_appointment_dialog(page: Page) -> None:
    """Click the UI affordance that opens the new-appointment form."""
    candidates = (
        'button:has-text("Add Appointment")',
        'button:has-text("New Appointment")',
        'a:has-text("Add Appointment")',
        'a:has-text("New Appointment")',
        '[data-testid*="add-appointment" i]',
    )
    for selector in candidates:
        loc = page.locator(selector).first
        try:
            logger.debug(f"Trying add-appointment selector={selector!r}")
            if await loc.count() == 0:
                continue
            await loc.click(timeout=5000)
            logger.debug(f"Clicked add-appointment selector={selector!r}")
            return
        except Exception as e:
            logger.debug(f"Failed selector={selector!r} while opening dialog: {e}")
            continue
    await _capture_debug_screenshot(page, "open-appointment-dialog-failed")
    logger.error(
        f"Could not open appointment dialog from url={page.url!r}; "
        f"selectors_tried={candidates!r}"
    )
    raise HealthieError(
        "Could not find the 'Add Appointment' affordance on the patient profile"
    )


async def _fill_appointment_form(page: Page, iso_date: str, iso_time: str) -> None:
    """Fill the date and time fields of the new-appointment form."""
    date_input = page.locator(
        'input[name*="date" i], input[placeholder*="date" i], input[type="date"]'
    ).first
    try:
        logger.debug("Waiting for appointment date input")
        await date_input.wait_for(state="visible", timeout=10000)
        await date_input.fill(iso_date)
        logger.debug(f"Filled appointment date={iso_date!r}")
    except PlaywrightTimeoutError:
        await _capture_debug_screenshot(page, "appointment-date-input-timeout")
        logger.error(f"Date input not found on url={page.url!r}")
        raise HealthieError("Appointment form date input did not appear")

    time_input = page.locator(
        'input[name*="time" i], input[placeholder*="time" i], input[type="time"]'
    ).first
    try:
        logger.debug("Waiting for appointment time input")
        await time_input.wait_for(state="visible", timeout=10000)
        await time_input.fill(iso_time)
        logger.debug(f"Filled appointment time={iso_time!r}")
    except PlaywrightTimeoutError:
        await _capture_debug_screenshot(page, "appointment-time-input-timeout")
        logger.error(f"Time input not found on url={page.url!r}")
        raise HealthieError("Appointment form time input did not appear")


async def _submit_appointment_form(page: Page) -> None:
    """Submit the appointment form and surface conflict errors as exceptions."""
    submit = page.locator(
        'button:has-text("Save"), button:has-text("Create"), '
        'button:has-text("Book"), button[type="submit"]'
    ).first
    try:
        logger.debug("Waiting for appointment submit button")
        await submit.wait_for(state="visible", timeout=10000)
        await submit.click()
        logger.debug("Clicked appointment submit button")
    except PlaywrightTimeoutError:
        await _capture_debug_screenshot(page, "appointment-submit-timeout")
        logger.error(f"Submit button not found on url={page.url!r}")
        raise HealthieError("Could not find the appointment submit button")

    # Give Healthie a moment to react and surface any inline errors.
    await page.wait_for_timeout(1500)

    error_locator = page.locator(
        '[role="alert"], .error, .alert-danger, [class*="error" i]'
    )
    if await error_locator.count() > 0:
        try:
            error_text = (await error_locator.first.inner_text()).strip()
        except Exception:
            error_text = ""
        if error_text:
            logger.warning(f"Appointment form returned error text={error_text!r}")
            await _capture_debug_screenshot(page, "appointment-submit-inline-error")
            lowered = error_text.lower()
            if any(
                keyword in lowered
                for keyword in ("unavailable", "conflict", "already", "not available")
            ):
                raise AppointmentUnavailableError(error_text)
            raise HealthieError(error_text)


async def _await_appointment_confirmation(page: Page) -> str | None:
    """Wait for a success indicator to appear after submission."""
    success_candidates = (
        'text=/appointment.*(created|booked|scheduled)/i',
        '[role="status"]',
        '[class*="success" i]',
    )
    for selector in success_candidates:
        loc = page.locator(selector).first
        try:
            logger.debug(f"Waiting for success indicator selector={selector!r}")
            await loc.wait_for(state="visible", timeout=8000)
            text = (await loc.inner_text()).strip()
            logger.debug(f"Success selector matched={selector!r} text={text!r}")
            return text
        except PlaywrightTimeoutError:
            logger.debug(f"Success selector timed out={selector!r}")
            continue
        except Exception as e:
            logger.debug(f"Success selector error={selector!r} error={e}")
            continue
    await _capture_debug_screenshot(page, "appointment-success-indicator-missing")
    logger.warning(f"No appointment success indicator found at url={page.url!r}")
    return None


def _parse_appointment_id(confirmation_text: str, current_url: str) -> str | None:
    """Best-effort extraction of an appointment id from confirmation text or URL."""
    url_match = re.search(r"/appointments/(\d+)", current_url)
    if url_match:
        return url_match.group(1)
    text_match = re.search(r"\b(\d{4,})\b", confirmation_text)
    return text_match.group(1) if text_match else None


async def get_patient_appointments(patient_id: str) -> list[dict[str, Any]]:
    """Return upcoming appointments for a patient.

    The Postgres backend supports this flow today. Healthie appointment listing
    still needs dedicated selectors/API access, so fail explicitly instead of
    silently claiming the patient has no appointments.
    """
    raise HealthieError(
        f"Healthie appointment lookup is not implemented for patient_id={patient_id!r}"
    )


async def modify_appointment(
    patient_id: str, appointment_id: str, date: str, time: str
) -> dict[str, Any] | None:
    """Modify an existing Healthie appointment.

    This needs Healthie-specific appointment edit selectors or API access before
    it can be safely enabled.
    """
    raise HealthieError(
        "Healthie appointment modification is not implemented "
        f"for patient_id={patient_id!r}, appointment_id={appointment_id!r}, "
        f"date={date!r}, time={time!r}"
    )


async def cancel_appointment(
    patient_id: str, appointment_id: str
) -> dict[str, Any] | None:
    """Cancel an existing Healthie appointment.

    This needs Healthie-specific appointment cancellation selectors or API access
    before it can be safely enabled.
    """
    raise HealthieError(
        "Healthie appointment cancellation is not implemented "
        f"for patient_id={patient_id!r}, appointment_id={appointment_id!r}"
    )


async def shutdown() -> None:
    """Close the Playwright session. Useful for graceful bot shutdown."""
    global _browser, _context, _page
    logger.debug("Shutting down Healthie Playwright session")
    try:
        if _page is not None:
            logger.debug("Closing Healthie page")
            await _page.close()
        if _context is not None:
            logger.debug("Closing Healthie browser context")
            await _context.close()
        if _browser is not None:
            logger.debug("Closing Healthie browser")
            await _browser.close()
    finally:
        _page = None
        _context = None
        _browser = None
        logger.debug("Healthie session state cleared")
