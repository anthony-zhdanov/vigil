# Vigil Agent Guide

## Authority

- `data/context/context.md` defines the current product direction, business intent, architecture, and implementation status.
- This file defines coding-agent behavior, repository conventions, safety rules, and verification expectations.
- Code, tests, and migrations are authoritative for behavior that actually exists. Update the context document when implementation status changes.
- `data/raw/` is a historical planning archive. Do not use it to change product direction unless a founder explicitly asks to revisit it.

## Product Boundaries

- Vigil recovers missed calls through SMS intake and automated booking for independent contractors.
- Plumbing is the first vertical, but shared messaging and booking infrastructure should remain vertical-agnostic.
- Do not add an AI receptionist, automated voice conversation, stale-quote follow-up, review automation, reactivation campaign, pricing, or outreach workflow without a new founder decision.
- Routine, explicitly configured services may be booked automatically. Emergencies, unsupported work, ambiguous identity, incomplete availability, and uncertain provider outcomes must hand off to the contractor.
- V1 creates appointments. Cancellation and rescheduling requests hand off to the contractor.

## Repository Map

- `backend/app/main.py`: FastAPI wiring, environment configuration, and provider webhook entry points.
- `backend/app/decision_tree/`: deterministic SMS classification, state transitions, action contracts, and approved templates.
- `backend/app/services/`: missed-call, SMS, booking, and side-effect orchestration.
- `backend/app/booking/`: provider-neutral booking domain, OAuth/security, provider adapters, setup routes, and Jobber webhooks.
- `backend/app/repositories/`: all Supabase reads and writes.
- `backend/tests/`: unit and transport-level tests using mocked external systems.
- `supabase/migrations/`: imperative database changes and security constraints.

Keep HTTP handlers thin, external-provider details behind adapters, persistence in repositories, and deterministic decisions separate from side effects.

## Safety Invariants

- Never send automated SMS to a client-scoped opt-out.
- Validate Twilio signatures outside explicit local/test environments.
- Validate Jobber webhook HMAC against the raw request body before storage or processing.
- Treat Twilio and Jobber deliveries as duplicated and retryable.
- Store OAuth tokens only as AES-GCM ciphertext. Never log tokens, authorization codes, client secrets, setup tokens, or Supabase service-role credentials.
- Keep the Supabase service-role credential server-side. New public-schema tables require RLS and explicit privilege review.
- Revalidate a selected slot immediately before creation and preserve booking idempotency across retries.
- Never retry a Jobber create mutation when the outcome is ambiguous. Reconcile by the Vigil marker or hand off.
- Jobber availability must remain disabled until all schedule-blocking objects and mutations are verified in authenticated GraphiQL for the pinned API version.
- Do not run live call, SMS, calendar, or database tests against real customers.

## Development Workflow

1. Read this file, the relevant `data/context/context.md` sections, and the modules/tests surrounding the change.
2. Check `git status`; do not overwrite unrelated user changes.
3. Prefer existing boundaries and helpers over new cross-cutting abstractions.
4. Add or update tests with each behavior change, including failure and duplicate-delivery paths.
5. For Supabase work, read `.agents/skills/supabase/SKILL.md`, review current Supabase documentation, add an imperative migration, enable RLS, and run security checks when tooling is available.
6. Keep commits cohesive so schema, provider adapters, setup, webhooks, and SMS behavior can evolve independently.
7. Update `data/context/context.md` when product behavior or implementation status changes.

## Verification

From `backend/`:

```text
.venv/bin/python -m compileall -q app
.venv/bin/python -m unittest discover -s tests -v
```

From the repository root:

```text
npx --yes pyright
git diff --check
```

Provider tests must use `httpx.MockTransport` or equivalent mocks unless a supervised test-account run is explicitly requested. Apply migrations and run Supabase advisors before enabling the feature in a deployed environment.

## Booking Operations

- Booking modes are `disabled`, `shadow`, and `live`. New connections start disabled; test availability in shadow before live SMS offers.
- Create a single-use contractor setup link from `backend/` with:

```text
.venv/bin/python scripts/create_booking_setup_link.py --client-id <uuid>
```

- Google uses deterministic event IDs derived from the Vigil booking UUID.
- Jobber creates a one-off Job and assigned Visit, with a visible `[Vigil:<booking-id>]` marker for reconciliation.
- A Jobber connection cannot become live merely because OAuth succeeded. Its stored schema-verification metadata and connection status must confirm complete availability for `JOBBER_GRAPHQL_VERSION`.
