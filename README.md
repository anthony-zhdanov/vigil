# Vigil

Vigil helps service businesses recover missed calls by starting a structured SMS conversation and guiding customers toward booking or a human handoff.

## Features

- Sends an automatic SMS after a missed call
- Classifies replies and extracts service, urgency, and location details
- Uses a deterministic decision tree with safe human handoffs
- Supports Google Calendar and Jobber booking workflows
- Prevents duplicate processing and respects customer opt-outs
- Records messages, decisions, and outcomes for auditing

## Tech Stack

Python, FastAPI, Supabase/PostgreSQL, Twilio, Google Calendar API, Jobber GraphQL API, and Docker.

## Local Setup

Requires Python 3.13.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Create `backend/.env` and add the provider credentials required for the features you want to use. At minimum, SMS workflows require Supabase and Twilio credentials.

## Tests

From `backend/`:

```bash
.venv/bin/python -m compileall -q app
.venv/bin/python -m unittest discover -s tests -v
```

From the repository root:

```bash
npx --yes pyright
```

## Status

Vigil is under active development.
