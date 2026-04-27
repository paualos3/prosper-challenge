# Prosper Challenge

This is a template repository for an AI voice agent that is able to schedule appointments for a health clinic. To do that the agent connects in real-time to the clinic's CRM system, which in the healthcare industry is known as an Electronic Health Record (EHR). The foundations are already set:

- Pipecat is configured with sensible defaults and the bot already introduces itself when initialized
- Playwright is set up so that you can programmatically log into Healthie, the EHR we'll use for this challenge

However, for the agent to be fully functional you'll need to implement the following missig pieces:

- Expand the agent's configuration so that it asks for the patient's name and date of birth
- Once it finds the patient it should ask for the desired date and time of the appointment and create it
- Implement the find patient and create appointment functionalities using Playwright or otherwise

## Setup

To get started, fork this repository so that you can start commiting and pushing changes to your own copy.

### Prerequisites

#### Environment

- Python 3.10 or later
- [uv](https://docs.astral.sh/uv/getting-started/installation/) package manager installed

#### Healthie Account

You'll need a Healthie account for testing, you can create one [here](https://secure.gethealthie.com/users/sign_up/provider).

### Installation

1. Clone this repository

   ```bash
   git clone <repository-url>
   cd prosper-challenge
   ```

2. Copy the API keys we've shared with you, as well as your Healthie credentials:

   Create a `.env` file:

   ```bash
   cp env.example .env
   ```

   Then, add your API keys and credentials:

   ```ini
   ELEVENLABS_API_KEY=your_elevenlabs_api_key
   OPENAI_API_KEY=your_openai_api_key
   HEALTHIE_EMAIL=your_healthie_email
   HEALTHIE_PASSWORD=your_healthie_password
   CLINIC_TIMEZONE=Europe/Madrid
   CLINIC_OPEN_TIME=09:00
   CLINIC_CLOSE_TIME=17:00
   CLINIC_WORKING_DAYS=0,1,2,3,4
   ```

3. Set up a virtual environment and install dependencies

   ```bash
   uv sync
   ```

4. Install Playwright browsers

   ```bash
   uv run playwright install chromium
   ```

### Scheduler backend modes

The bot supports two scheduling backends, selected with `SCHEDULER_BACKEND`:

- `postgres` (default): uses a local Postgres database (`patients` + `appointments` tables).
- `healthie`: legacy Playwright scraping against Healthie, kept for reference but not used because
  Healthie 2FA blocks unattended local runs.

If `SCHEDULER_BACKEND=postgres`, set `DATABASE_URL` (defaults to
`postgresql://postgres:postgres@localhost:5432/prosper`).

Set `CLINIC_TIMEZONE` to control how the agent resolves relative appointment
requests such as "tomorrow" or "next Friday" (defaults to `Europe/Madrid`).
Use `CLINIC_OPEN_TIME`, `CLINIC_CLOSE_TIME`, and `CLINIC_WORKING_DAYS` to
control bookable hours. Working days use Python weekday numbers (`0` Monday
through `6` Sunday).

The Postgres backend supports finding patients, checking existing appointments,
creating new appointments, modifying existing appointments, and cancelling
appointments. The Healthie backend is legacy-only; the existing-appointment
management tools fail explicitly until Healthie-specific appointment
list/edit/cancel selectors or API access are added.

Past appointments are ignored when checking whether a patient already has an
appointment, and the backend rejects attempts to create or move an appointment
into the past.

### Running the Bot

```bash
uv run bot.py
```

**Open http://localhost:7860 in your browser** and click `Connect` to start talking to your bot.

> 💡 First run note: The initial startup may take ~20 seconds as Pipecat downloads required models and imports.

### Running tests

```bash
uv run pytest
```

The default test command prints branch coverage for the deterministic `src/`
modules. The Pipecat runtime entrypoint and Playwright Healthie integration are
excluded from this unit-test coverage report because they need service
credentials, browsers, and live I/O. For an HTML report:

```bash
uv run pytest --cov-report=html
```

### Running with Docker Compose (app + Postgres)

This repository includes `docker-compose.yml` to start both the app and a local Postgres DB:

```bash
docker compose up --build
```

What it does:

- Starts `db` (`postgres:16`) and initializes schema/data from `db/init.sql`.
- Starts `app` and runs `uv run bot.py`.
- Sets `SCHEDULER_BACKEND=postgres` for the app container.
- Seeds a default test patient: `Pau Test`, DOB `1996-09-02`.

### Project layout

The implementation is organized as a small `src` package:

- `bot.py`: compatibility entry point, so `uv run bot.py` still works.
- `src/bot.py`: Pipecat pipeline and runtime wiring.
- `src/tool_handlers.py`: tool schemas, tool handlers, and session guardrails.
- `src/scheduling.py`: scheduler backend selector and shared
  interface.
- `src/integrations/healthie.py`: Playwright integration with
  Healthie, kept as legacy/reference code.
- `src/backends/postgres.py`: local Postgres scheduler
  backend, including existing appointment lookup, modification, and
  cancellation.
- `scripts/healthie_debug.py`: CLI harness for testing Healthie login/search
  and booking flows without the voice pipeline.



## Expectations & Deliverables

To make the agent functional we expect you to implement at least the following missing functionalities:

1. **Conversation Flow**: Modify the agent's behavior to ask for patient name and date of birth, then appointment date and time. [This guide](https://docs.pipecat.ai/guides/learn/function-calling) on function calling from Pipecat is probably a good start.

2. **Find Patient**: Implement `healthie.find_patient(name, date_of_birth)` in `src/integrations/healthie.py` to search for patients in Healthie.

3. **Create Appointment**: Implement `healthie.create_appointment(patient_id, date, time)` in `src/integrations/healthie.py` to create appointments in Healthie.

4. **Integration**: Connect the voice agent to these functions so it can actually schedule appointments during conversations.

We encourage you to use AI tools (Claude Code, Cursor, etc.) to help you with this challenge. We don't mind if you "vibe code" everything, that probably means you have good prompting skills. What we do care about is whether you understand the decisions and trade-offs behind your solution. That's why, apart from the code itself, we'd like you to write a high-level overview of your solution and the decisions you've made to get to it—do this in a `SOLUTION.md` file at the root of your fork. During the interview we'll dive deeper into it and discuss opportunities to improve it in the future.

If you'd like to go further, you can already document some of those potential improvements in your `SOLUTION.md`. Some areas that we'd love to hear your thoughts on are:
- Latency: balancing speed with user experience and accuracy
- Reliability: ensuring that the agent is always available to answer, regardless of external factors (e.g. AI provider unavailable)
- Evaluation: making it easy for us to check that the agent is behaving how it is supposed to


Once you are done, please share the link to your fork so that we can get familiar with it before our chat.
