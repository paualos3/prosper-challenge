# Solution Overview

This document describes how the Prosper Health scheduling voice agent was built
on top of the provided template, the trade-offs made along the way, and the
opportunities I would prioritize if this were to be hardened for production.

## What the bot does

A caller connects via the WebRTC web client at `http://localhost:7860` and is
greeted by a Pipecat-driven voice assistant. The agent walks them through a
strict scheduling workflow:

1. Greeting and intent confirmation.
2. Collect the patient's full name.
3. Collect the patient's date of birth (normalized to ISO `YYYY-MM-DD`).
4. Call the `find_patient` tool against the active scheduling backend. On
   success, confirm the matched patient out loud.
5. Call `get_patient_appointments` before asking for a new appointment slot.
   Past appointments are ignored here. If the patient already has a current or
   future appointment, summarize it and ask whether they want to modify it,
   cancel it, or book a separate new appointment.
6. For a new appointment, collect the desired appointment date and time.
7. Call the `create_appointment` tool with the resolved `patient_id` and the
   normalized date/time.
8. For an existing appointment, call `modify_appointment` or
   `cancel_appointment` only after the caller explicitly chooses that action.
9. Confirm the booking/change/cancellation or recover gracefully on
   conflicts/errors.

The agent never invents identifiers, dates, or confirmation numbers, and never
calls `create_appointment` before a successful `find_patient`.

## Architecture

```
[ Browser / WebRTC ]
        │
        ▼
[ Pipecat Pipeline ]
        │
        ├─ ElevenLabs Realtime STT
        ├─ Smart Turn V3 + Silero VAD (turn-taking)
        ├─ OpenAI LLM (with tool calling)
        │       ├─ tool: get_current_date
        │       ├─ tool: find_patient
        │       ├─ tool: get_patient_appointments
        │       ├─ tool: modify_appointment
        │       ├─ tool: cancel_appointment
        │       └─ tool: create_appointment
        └─ ElevenLabs TTS
                │
                ▼
        [ src/tool_handlers.py ]
                │
                ▼
        [ src/scheduling.py ]
                │
                ▼
        [ Postgres backend ]
                │
                ▼
          [ PostgreSQL ]
```

### Files of interest

- [`bot.py`](bot.py): compatibility entry point so the original `uv run bot.py`
  command still works.
- [`src/bot.py`](src/bot.py): Pipecat pipeline and runtime wiring.
- [`src/tool_handlers.py`](src/tool_handlers.py): `FunctionSchema` definitions,
  tool handlers, and per-call guardrail state that bridges the LLM to the
  selected backend.
- [`src/scheduling.py`](src/scheduling.py):
  backend selector and common contract that lets the bot switch between
  Healthie and Postgres without changing function-calling behavior.
- [`src/integrations/healthie.py`](src/integrations/healthie.py):
  legacy Playwright-driven Healthie integration kept for reference only. Healthie
  is no longer the active path because 2FA blocks unattended local runs.
- [`src/backends/postgres.py`](src/backends/postgres.py):
  Postgres implementation of patient lookup plus appointment lookup, creation,
  modification, and cancellation with slot-collision protection.
- [`docker-compose.yml`](docker-compose.yml) and [`db/init.sql`](db/init.sql):
  local two-container runtime (app + db) and seeded test data.

## Key decisions

### Tool calling via Pipecat `FunctionSchema`

I exposed the scheduling operations (`find_patient`, `get_patient_appointments`,
`create_appointment`, `modify_appointment`, and `cancel_appointment`) plus
`get_current_date` as standard Pipecat `FunctionSchema` tools attached to the
`LLMContext`, and registered async handlers via `llm.register_function`. This
keeps the LLM's tool descriptions in one place, makes the schema portable across
providers, and keeps the backend side effects out of the conversation loop.
The handlers live outside `src/bot.py` so they can be unit tested without
starting Pipecat or importing voice/model services.

### Strict, deterministic conversation contract

The system prompt encodes the workflow as numbered steps with explicit recovery
behavior for each tool result status (`ok`, `not_found`, `unavailable`,
`invalid_request`, `error`). Tool handlers return structured dicts with a
`status` field so the model has no ambiguity about how to react. The prompt also
requires the assistant to briefly tell the caller what is happening before
slow tool calls, so the caller is not left waiting in silence while a patient
lookup or appointment booking runs. Two design choices reinforce this:

- The LLM is told to normalize dates to `YYYY-MM-DD` and times to 24-hour
  `HH:MM` *before* calling tools. The Healthie module also re-normalizes
  defensively, so a small drift in the model's output does not break the call.
- The LLM is given clinic-local date context at startup and has a
  `get_current_date` tool for relative dates like "tomorrow", "today",
  "next Friday", or "in two weeks". This prevents it from relying on model
  memory or inventing a date when the caller uses relative language.
- The model is told to ignore past appointments in the existing-appointment
  flow and never book or modify an appointment into the past. The Postgres
  backend enforces the same rule so it is not only prompt-dependent.
- The model is instructed to never call `create_appointment` before a
  successful `find_patient` and never to invent IDs.
- The model is instructed to call `get_patient_appointments` immediately after
  patient confirmation. If an existing appointment is found, it must ask the
  caller whether they want to modify it, cancel it, or book separately, and it
  cannot modify/cancel until the caller explicitly chooses that action.
- Tool handlers keep a small session state (`confirmed_patient_id` and known
  current appointment IDs). Even if the LLM tries to call create/modify/cancel
  out of order, the handler rejects the request before it reaches the backend.
- Bookable slots are constrained by configurable clinic hours
  (`CLINIC_OPEN_TIME`, `CLINIC_CLOSE_TIME`, and `CLINIC_WORKING_DAYS`) in
  addition to the "not in the past" rule.

### Healthie integration status

The challenge originally pointed at Healthie and the template included a
Playwright login path. During testing, Healthie introduced an email OTP / 2FA
verification gate that blocks unattended login, making scraping unsuitable for
the final runnable solution. I kept the Healthie module in the repository as
reference work, but the active implementation is now Postgres-backed.

The Healthie backend is still selectable, but appointment lookup/modification
and cancellation fail explicitly with `HealthieError`. That is intentional: it
is safer than pretending Healthie support is complete when the auth flow cannot
be automated reliably.

### Postgres backend as the active scheduler

Instead of letting the challenge be blocked by Healthie 2FA, I introduced a
backend abstraction and made Postgres the default:

- `SCHEDULER_BACKEND=postgres` is the default and routes tool calls to a local DB.
- `SCHEDULER_BACKEND=healthie` remains as a legacy/reference path only.

The LLM and tool schemas stay unchanged, so the conversational behavior is
decoupled from the storage/provider choice.

For the Postgres mode, I modeled:

- `patients(id, first_name, last_name, date_of_birth, ...)`
- `appointments(id, patient_id, appointment_date, appointment_time, ...)`
  with a unique constraint on `(appointment_date, appointment_time)` to enforce
  no double-booking.

Lookup first matches name/surname, then verifies DOB exactly, mirroring the
challenge requirement. Appointment creation checks slot availability before
insert. Existing appointment lookup lists only current/future appointments in
date/time order, ignoring historical appointments so they do not derail the
booking flow. Creation and modification reject past date/time slots using the
  configured clinic timezone and clinic business hours. Modification verifies
  the appointment belongs to that patient, checks the new slot for conflicts,
  then updates the appointment.
Cancellation also verifies ownership before deleting the row. The compose stack
seeds a default patient (`Pau Test`, `1996-09-02`) for deterministic local
debugging.

Healthie appointment lookup/modification/cancellation are intentionally explicit
`HealthieError` paths for now. That is safer than returning an empty list or
pretending the operation succeeded while the selectors/API work is still
missing.

### Docker Compose runtime for app + DB

I added `docker-compose.yml` to launch two services together:

- `db`: Postgres 16, initialized by `db/init.sql`.
- `app`: this bot, configured with `SCHEDULER_BACKEND=postgres` and
  `DATABASE_URL=postgresql://postgres:postgres@db:5432/prosper`.

This gives a single command (`docker compose up --build`) to run the voice
agent against a local datastore without relying on Healthie.

### Defensive normalization helpers

`_normalize_date` and `_normalize_time` accept several common formats. This
guards against the LLM occasionally producing "March 14, 1990" or "2:30 PM"
even when the prompt asks for ISO. The helpers are also reused on the search
result side when extracting DOB from the profile page.

### Unit tests

I added a pytest suite for the deterministic parts of the app, with coverage
enabled by default through `pytest-cov`:

- Date/time normalization and invalid input handling in the Postgres backend.
- Clinic-local date context for resolving relative appointment requests.
- Rejection of past create/modify slots and filtering of past appointments from
  existing-appointment lookup.
- Rejection of out-of-hours and closed-day appointment slots.
- Backend selection from `SCHEDULER_BACKEND`.
- Existing appointment lookup, modification, cancellation, and conflict
  handling in the Postgres backend.
- Tool handler guardrails and error mapping without starting Pipecat.
- Prompt requirements that keep the caller informed before slow tool calls.

The coverage target intentionally focuses on the deterministic scheduling
contract (`src/scheduling.py`, `src/backends/postgres.py`,
`src/tool_handlers.py`, prompts, and date helpers), which can be tested fully
without network, audio, or browser state. `src/bot.py` and
`src/integrations/healthie.py` are omitted from the unit coverage report because
meaningful coverage there would require Pipecat runtime services,
model/provider credentials, Playwright browser state, and a working Healthie
account.

## Future improvements

### Latency

- **Connection pooling.** The Postgres backend currently opens short-lived
  connections per operation, which is fine for a challenge but not ideal under
  load. A small `psycopg_pool` wrapper would reduce connection overhead.
- **Streaming TTS confirmations.** While a tool call is running, emit a brief
  filler ("one moment while I look that up") so the caller does not perceive
  dead air.
- **Smaller / faster LLM for routing.** A two-tier setup (cheap model to
  classify intent and emit tool calls, larger model only for free-form
  responses) can reduce per-turn latency.

### Reliability

- **Provider failover.** Wrap STT/LLM/TTS in adapters that fall back to a
  secondary provider on timeout or 5xx (e.g. Deepgram STT + Anthropic LLM as
  backups). Pipecat already supports swapping services; what's needed is a
  small supervisor.
- **Idempotency for `create_appointment`.** Generate a client-side request id
  per booking attempt and pass it through, so a transient network blip on the
  database side does not result in a double booking on retry.
- **Stronger availability model.** Add provider/location availability tables
  rather than only validating business hours and global slot conflicts.

### Evaluation

- **Scenario-based tests.** Author scripted dialogues (happy path, wrong DOB,
  conflicting slot, mid-call corrections) and replay them with synthetic audio
  to assert the bot reaches the expected tool calls and confirmations.
- **Tool-call assertions.** Pipecat exposes function-call frames; an
  `EvaluationObserver` could record every tool call with arguments and
  results, then compare against expected traces in CI.
- **Transcript grading.** Use an LLM-as-judge pass over the conversation log
  to score adherence to the scheduling contract (asked for both name and DOB,
  confirmed the patient, did not invent IDs, etc.).
- **End-to-end against Postgres mode.** A CI smoke test that runs the app in
  Postgres mode and drives a scripted conversation would catch regressions in
  prompt flow, tool schemas, and backend behavior together.
- **Dev-container setup.** Add a checked-in `.devcontainer/` that builds on the
  same Pipecat base image, syncs locked dependencies, installs Playwright
  Chromium, and forwards port `7860` for one-click remote launch.
