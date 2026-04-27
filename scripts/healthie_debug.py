"""Local CLI harness for debugging Healthie scraping flows.

Run without the LLM/model pipeline to validate selectors and page interactions.

Examples:
    uv run python scripts/healthie_debug.py login
    uv run python scripts/healthie_debug.py find
    uv run python scripts/healthie_debug.py find --name "Pau Test" --dob "1996-09-02"
    uv run python scripts/healthie_debug.py book --patient-id 12345 --date 2026-05-01 --time 10:30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Ensure the project root is importable when running this file directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.integrations import healthie

DEFAULT_TEST_NAME = "Pau Test"
DEFAULT_TEST_DOB = "1996-09-02"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Debug Healthie operations locally.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("login", help="Validate Healthie login flow.")

    find_parser = subparsers.add_parser("find", help="Find a patient by name and DOB.")
    find_parser.add_argument(
        "--name",
        default=DEFAULT_TEST_NAME,
        help=f'Patient full name (default: "{DEFAULT_TEST_NAME}")',
    )
    find_parser.add_argument(
        "--dob",
        default=DEFAULT_TEST_DOB,
        help=f"Date of birth in accepted formats (default: {DEFAULT_TEST_DOB})",
    )

    book_parser = subparsers.add_parser(
        "book", help="Create an appointment for a known patient_id."
    )
    book_parser.add_argument(
        "--patient-id",
        required=True,
        help="Healthie patient_id (typically from the `find` command output).",
    )
    book_parser.add_argument(
        "--date",
        required=True,
        help="Appointment date (recommended ISO: YYYY-MM-DD).",
    )
    book_parser.add_argument(
        "--time",
        required=True,
        help="Appointment time (recommended 24-hour HH:MM).",
    )

    return parser


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, default=str))


async def _run(args: argparse.Namespace) -> int:
    load_dotenv(override=True)
    try:
        if args.command == "login":
            page = await healthie.login_to_healthie()
            _print_json(
                {
                    "status": "ok",
                    "operation": "login",
                    "current_url": page.url,
                }
            )
            return 0

        if args.command == "find":
            patient = await healthie.find_patient(name=args.name, date_of_birth=args.dob)
            _print_json(
                {
                    "status": "ok" if patient else "not_found",
                    "operation": "find_patient",
                    "input": {"name": args.name, "date_of_birth": args.dob},
                    "patient": patient,
                }
            )
            return 0

        if args.command == "book":
            appointment = await healthie.create_appointment(
                patient_id=args.patient_id,
                date=args.date,
                time=args.time,
            )
            _print_json(
                {
                    "status": "ok" if appointment else "error",
                    "operation": "create_appointment",
                    "input": {
                        "patient_id": args.patient_id,
                        "date": args.date,
                        "time": args.time,
                    },
                    "appointment": appointment,
                }
            )
            return 0 if appointment else 1

        _print_json({"status": "error", "message": f"Unsupported command: {args.command}"})
        return 2
    except Exception as e:
        _print_json(
            {
                "status": "error",
                "operation": args.command,
                "error_type": type(e).__name__,
                "error": str(e),
            }
        )
        return 1
    finally:
        await healthie.shutdown()


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
