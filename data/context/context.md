# Vigil Project Context

> **Purpose.** This document defines Vigil's current product direction, business context, system behavior, and implementation state. It is written to give founders and coding agents enough context to make consistent product and engineering decisions without carrying forward obsolete planning material.
>
> **Last updated:** 2026-07-13
>
> **Status:** Active development. Missed-call recovery and the Google/Jobber automated-booking vertical slice are implemented in code and covered by automated tests. Booking migrations have not been applied through this implementation task, provider credentials are not configured, and live provider conformance tests are still required before rollout.

## 1. Document authority

This document is no longer intended to be the project's only source of truth.

- `data/context/context.md` defines the product, business intent, technical architecture, and current implementation state.
- Root-level `AGENTS.md` defines coding-agent instructions, repository conventions, safety requirements, and verification practices.
- The code, tests, and Supabase migrations describe the implementation that actually exists. When this document disagrees with the repository about current behavior, verify the code and update this document.
- `data/raw/` is a historical archive from the initial research, planning, and synthesis phase. Its PDFs, spreadsheets, images, and rough notes are not authoritative and must not change product direction unless the founders explicitly revisit them.

The goal is to keep this document focused on decisions that help design and implement the current product. Historical planning material and speculative features should not be carried forward without an explicit founder decision.

## 2. Product definition

### 2.1 One-line description

Vigil is a managed missed-call recovery and automated booking system for independent contractors that turns unanswered phone calls into asynchronous SMS conversations and booked calendar appointments.

### 2.2 Founders

Vigil has two founders:

- one technical founder responsible primarily for product and engineering;
- one business-minded founder responsible primarily for customer discovery, operations, and commercialization.

Responsibilities may overlap as the product develops.

### 2.3 Initial market and long-term scope

Plumbing is the initial vertical and the first workflow being implemented and tested. It provides a concrete environment for designing job-type, urgency, location, and booking behavior.

The product itself is not intended to remain plumbing-specific. Vigil is being designed for independent contractors who rely on phone calls as a primary source of leads and appointments. This can include plumbers and other home-service or field-service contractors with similar missed-call and scheduling workflows.

Vertical-specific language, classification rules, booking durations, service areas, and intake questions should be configurable. Shared call, messaging, conversation, calendar, and booking infrastructure should remain vertical-agnostic wherever practical.

## 3. MVP

The MVP is an SMS missed-call follow-up workflow with calendar booking integration. Calendar booking is a core part of the MVP, not an optional enhancement: collecting lead details without allowing the customer to choose an appointment would leave too much conversion dependent on a later phone call.

### 3.1 Intended customer workflow

```text
Customer calls a contractor's existing business number
  -> contractor does not answer
  -> conditional forwarding sends the missed call to Vigil's Twilio number
  -> Vigil sends an immediate SMS in the contractor's name
  -> customer and Vigil continue asynchronously by SMS
  -> Vigil gathers the information required by that contractor
  -> Vigil checks the contractor's connected calendar and scheduling rules
  -> Vigil offers valid appointment times
  -> customer chooses a time
  -> Vigil revalidates availability and creates the calendar event
  -> customer receives a booking confirmation
  -> contractor receives the booking in the calendar they already use
```

The contractor should not need to answer the original call or manually copy lead information into a calendar for a normal booking to complete.

### 3.2 Included in the MVP

- Conditional forwarding of unanswered calls to a client-specific Twilio auxiliary number
- Immediate SMS follow-up from that number
- Persistent lead and SMS conversation state
- Deterministic handling of opt-outs, wrong numbers, and common lead intents
- Collection of vertical-specific booking details such as location, job type, and urgency
- MMS metadata capture so customer photos can be associated with the conversation
- Owner notification for urgent or completed intake paths where appropriate
- Google Calendar and Jobber connection, resource selection, and availability lookup
- Appointment-slot selection within the SMS conversation
- Calendar-event creation and customer booking confirmation
- Traceable records for calls, messages, workflow decisions, notifications, and bookings

### 3.3 Voice boundary

There are no plans to add an AI receptionist or automated voice conversation. Vigil handles the missed call after forwarding; it does not answer or converse with the customer on the voice call. The brief TwiML response exists only to terminate the forwarded call cleanly before the SMS workflow begins.

## 4. Client experience

Vigil should require as little operational change as possible, but automated booking necessarily introduces a new workflow.

The intended client experience is:

- The contractor keeps the existing public business number.
- Conditional forwarding sends only missed calls to Vigil.
- The contractor connects a supported calendar and configures booking rules.
- Vigil conducts SMS intake and creates valid appointments automatically.
- The contractor treats Vigil-created calendar events or Jobber Visits as real bookings and keeps provider availability accurate.
- Exceptional, urgent, unsupported, or ambiguous conversations can be handed to the contractor.

The product should integrate with the contractor's existing behavior instead of requiring a separate lead-management dashboard for normal operation. Calendar connection, scheduling preferences, and booking visibility are unavoidable onboarding requirements and should be made explicit.

## 5. Calendar booking

### 5.1 Product direction

Google Calendar and Jobber are the first supported booking providers behind one shared booking contract. Each client selects exactly one active provider and one calendar or Jobber user. There is no automatic cross-provider failover.

Google Calendar remains important for contractors who use a general calendar. Jobber supports contractors already managing clients, properties, Jobs, and Visits in field-service software. Jobber bookings create a one-off Job and scheduled Visit assigned to the configured Jobber user.

The assumption that these systems cover a meaningful share of the target market still requires customer validation. Ordinary Jobber-to-Google calendar sync is not used as a booking write path.

The booking architecture should isolate provider-specific API code so additional calendar systems can be supported later without rewriting the SMS conversation engine.

### 5.2 Minimum booking capabilities

The implemented booking foundation supports:

- secure per-client Google or Jobber authorization;
- selection of the Google calendar or Jobber user Vigil is allowed to use;
- client-specific timezone, working hours, service duration, and scheduling constraints;
- free/busy lookup without exposing unrelated calendar-event details to customers;
- generation of a small set of valid appointment choices;
- slot selection through SMS;
- immediate availability revalidation before booking;
- idempotent provider creation so retries cannot create duplicate appointments;
- storage of the provider event ID and relevant booking state;
- customer confirmation and contractor-visible event details;
- a safe handoff when authorization expires, availability changes, or booking fails.

OAuth tokens and provider credentials must be treated as secrets. The implementation must use the minimum Google and Jobber scopes required and must not place credentials in SMS content, logs, or committed files.

### 5.3 Conversation extension

The plumbing decision tree now enters booking after required intake facts have been collected when the service and provider are configured in live mode. Shadow mode computes availability but preserves the existing owner handoff.

The intended state progression is:

```text
awaiting_initial_reply
  -> awaiting_location
  -> awaiting_job_type
  -> awaiting_urgency
  -> awaiting_customer_name
  -> finding_availability
  -> awaiting_slot_selection
  -> booking
  -> booked
```

Terminal alternatives include opt-out, wrong number, no longer needed, manual handoff, and `booking_handoff`. Customers can reply `1`, `2`, or `3` to choose a slot and `MORE` to request the next page. Offers expire after 15 minutes and selected slots are revalidated immediately before creation.

## 6. Technical architecture

### 6.1 Stack

- Python 3.13
- FastAPI
- Uvicorn
- Twilio Voice and Messaging APIs
- Supabase Postgres through the Supabase Python client
- Pydantic/FastAPI request handling
- Python `unittest` and FastAPI `TestClient`
- Pyright in basic type-checking mode
- Docker deployment using `backend/Dockerfile`

n8n may be used later for non-critical operational glue, but it is not part of the current product implementation and must not own conversation, booking, or customer-consent state.

### 6.2 Code boundaries

```text
backend/app/main.py
  HTTP transport, environment configuration, Twilio signature validation,
  webhook idempotency entry points, TwiML responses, and endpoint wiring

backend/app/services/
  missed-call recovery, inbound SMS orchestration, booking orchestration,
  and action execution

backend/app/booking/
  provider-neutral contracts, slot generation, encrypted credentials, OAuth,
  Google and Jobber adapters, guided setup, and Jobber webhook handling

backend/app/repositories/
  Supabase reads and writes

backend/app/decision_tree/
  deterministic classification, workflow interpretation, action contracts,
  vertical-specific rules, and approved message templates

backend/tests/
  webhook, service, repository, decision-tree, and template tests

supabase/migrations/
  tracked database migrations and workflow constraints
```

Keep HTTP handlers thin, persistence inside repositories, and decision evaluation separate from side-effect execution. Calendar provider access should follow the same boundary: provider adapters call external calendar APIs, booking services orchestrate the workflow, and repositories persist connections and bookings.

### 6.3 Missed-call workflow currently implemented

```text
Forwarded call reaches a Twilio auxiliary number
  -> Twilio POSTs to /webhooks/twilio/voice
  -> FastAPI validates the Twilio signature
  -> FastAPI strictly authorizes the destination number
  -> webhook event is deduplicated
  -> client and lead are resolved
  -> opt-out and active/recent-conversation suppression are checked
  -> call event and conversation are recorded
  -> approved missed-call SMS is sent and recorded
  -> Twilio receives TwiML that answers briefly and hangs up
```

The voice response uses `<Say>` followed by `<Hangup>`. Answering the forwarded leg allows carrier forwarding to terminate cleanly; rejecting an authorized forwarded call can cause the carrier to treat forwarding as failed and continue ringing or route differently.

Unknown or unauthorized Twilio destination numbers are rejected before workflow side effects. Duplicate suppression currently prevents a new recovery SMS when an active conversation exists or a recent recovery message was already sent.

### 6.4 SMS workflow currently implemented

```text
Customer replies to the Twilio number
  -> Twilio POSTs to /webhooks/twilio/sms
  -> FastAPI validates the signature and deduplicates the event
  -> client, lead, and active conversation are resolved
  -> inbound SMS and any MMS metadata are recorded
  -> deterministic plumbing classifier extracts intent and intake details
  -> decision-tree run and selected actions are recorded
  -> action executor sends approved replies, updates lead state, creates opt-outs,
     offers booking slots, creates appointments, notifies the owner, or closes
  -> conversation state and summary are updated
```

The classifier is deterministic, not LLM-backed. It recognizes opt-out, wrong-number, no-longer-needed, lead, and unclear intents, and extracts location, plumbing job type, and urgency. Routine configured work proceeds through customer-name collection and booking. Emergencies, unsupported work, disabled providers, incomplete availability, and failures hand off.

Customer-facing copy is selected from approved templates in `backend/app/decision_tree/templates/base.py`. The action executor supports:

- `send_sms_template`
- `mark_lead_status`
- `create_opt_out`
- `notify_owner`
- `close_conversation`
- `offer_booking_slots`
- `create_booking`
- `booking_handoff`

External booking outcomes can override transient decision-tree states before the conversation is persisted once. Duplicate slot replies reuse an existing booking confirmation.

### 6.5 HTTP endpoints

- `GET /health` returns `{"status": "ok"}`.
- `POST /webhooks/twilio/voice` handles forwarded missed calls.
- `POST /webhooks/twilio/sms` handles inbound SMS and MMS metadata.
- `POST /webhooks/twilio/status` updates outbound SMS delivery status.
- `GET /booking/setup/claim` exchanges a single-use setup token for a secure session.
- `GET /booking/setup` renders the contractor booking configuration flow.
- `POST /booking/setup/oauth/{provider}` and the corresponding callback run OAuth.
- `POST /booking/setup/config` saves the active resource and per-service rules.
- `POST /booking/setup/disconnect` disconnects a provider and disables booking.
- `POST /webhooks/jobber` validates, persists, and asynchronously processes Jobber lifecycle webhooks.

Twilio request-signature validation is enabled by default. It can be disabled only when the runtime environment is explicitly local, development, or test. `PUBLIC_BASE_URL` is used to reconstruct the public webhook URL correctly behind a proxy.

### 6.6 Database

Supabase Postgres is the source of truth for product state. The deployed public schema currently contains:

- `clients`
- `client_phone_numbers`
- `leads`
- `call_events`
- `messages`
- `message_media`
- `conversations`
- `decision_tree_runs`
- `owner_notifications`
- `opt_outs`
- `webhook_events`

Row-level security is enabled on these tables. The backend currently uses the Supabase service-role credential and therefore all externally reachable operations must remain behind authenticated provider webhooks and strict client-number authorization.

Important database invariants include unique client/lead phone pairs, unique Twilio message IDs, one active conversation per client/lead/channel, unique opt-outs per client/phone pair, and unique webhook-provider event identities or request hashes.

The tracked migrations add client phone numbers, webhook idempotency, MMS metadata, workflow indexes and constraints, and support for reopening conversations after earlier conversations close. The reopening migration (`20260708011000_allow_reopened_conversations.sql`) exists in the repository but was not present in the remote Supabase migration history when checked on 2026-07-12. The repository also does not currently contain the complete migration history that originally created every base table; this should be corrected before relying on migrations to reproduce the database from scratch.

Tracked booking migrations now add `booking_connections`, `booking_configs`, `booking_services`, `booking_slot_offers`, `bookings`, `booking_setup_tokens`, and `booking_oauth_states`, plus durable Jobber webhook payload fields and active-booking uniqueness. All new booking tables enable RLS, revoke `anon` and `authenticated`, and explicitly grant backend access to `service_role`.

These new migrations were created but not applied to the remote project during this implementation. Apply them in order and run Supabase security/performance advisors before deploying booking code.

### 6.7 Reliability and safety invariants

- Never send an automated SMS to a number that has opted out for that client.
- Never process a voice call for an unknown or disabled Twilio destination number.
- Validate Twilio signatures in every non-local environment.
- Treat Twilio webhooks as retryable and potentially duplicated.
- Record outbound attempts even when Twilio sending fails.
- Keep customer-facing messages constrained to approved templates.
- Do not expose the Supabase service-role key or provider credentials.
- Revalidate calendar availability immediately before creating an event.
- Make booking creation idempotent across message and webhook retries.
- Preserve enough event, message, decision, and booking history to explain every automated action.

### 6.8 Configuration and deployment

The backend reads operating-system environment variables first and falls back to `backend/.env` for local development. Current variables include:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` (`SUPABASE_KEY` remains a legacy fallback)
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `PUBLIC_BASE_URL`
- `TWILIO_STATUS_CALLBACK_URL` when explicitly configured
- `TWILIO_VALIDATE_SIGNATURE`
- `TWILIO_FORCE_IPV4`
- `APP_ENV`, `ENVIRONMENT`, or `ENV`
- `BOOKING_TOKEN_ENCRYPTION_KEY`
- `BOOKING_SESSION_SECRET`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `JOBBER_CLIENT_ID`
- `JOBBER_CLIENT_SECRET`
- `JOBBER_GRAPHQL_VERSION` (currently pinned by default to `2025-04-16`)

Secrets and `.env` files must not be committed. OAuth tokens are encrypted with AES-GCM before persistence. Setup links, setup sessions, and OAuth state use random bearer values whose hashes are stored in Postgres.

The Docker image starts the service with:

```text
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
```

Twilio requires a stable public HTTPS endpoint in deployed environments. A local tunnel may be used for supervised development testing, but it is not production infrastructure.

## 7. Current implementation state

### Implemented and covered by the current code

- FastAPI health, voice, SMS, and delivery-status endpoints
- Twilio request-signature validation with guarded local opt-out
- Strict authorization of voice destination numbers
- Client-specific Twilio number mapping with legacy `clients.twilio_phone` fallback
- Persistent and process-local webhook duplicate protection
- Missed-call logging, lead upsert, active-conversation suppression, and 60-minute recent-message suppression
- Recovery SMS creation and outbound status recording
- Persistent SMS conversations that can be closed and later reopened as a new conversation once the pending reopening migration is applied
- Deterministic plumbing SMS classification and multi-step intake
- Approved customer and owner SMS templates
- Opt-out and wrong-number suppression
- Owner SMS notifications, including urgent notifications
- Inbound MMS metadata recording
- Decision-tree run audit records
- Twilio delivery-status updates
- Repository, service, decision-tree, and webhook unit tests
- Docker runtime definition and pinned Python dependencies
- Provider-neutral booking contracts and timezone/DST-aware slot generation
- AES-GCM token storage, OAuth state replay protection, CSRF, and rotating refresh-token persistence
- Google calendar discovery, FreeBusy lookup, deterministic event creation, and duplicate reconciliation
- Jobber OAuth/GraphQL adapter, account and user discovery, phone identity matching, property reuse, and Job-plus-Visit creation
- Jobber schema-version safety gate that prevents incomplete availability from going live
- Single-use guided setup links, provider OAuth, resource selection, per-service scheduling rules, and disconnect flows
- `disabled`, `shadow`, and `live` booking rollout modes
- SMS customer-name collection, three-slot offers, `MORE` paging, expiry, final revalidation, booking confirmation, and handoff
- Durable HMAC-validated Jobber webhooks with replay and `APP_DISCONNECT` handling
- Booking-specific migrations and automated unit/integration coverage using mocked providers

### Not implemented

- Applied remote booking migrations and Supabase advisor verification
- Configured Google and Jobber developer/test credentials
- Live Google OAuth, FreeBusy, event-create, and revoke conformance tests
- Authenticated Jobber GraphiQL verification of the pinned availability and create operations
- Live Jobber client/property/Job/Visit and webhook conformance tests
- Production monitoring and a controlled contractor pilot
- Automated cancellation or rescheduling; these requests intentionally hand off in V1
- A complete reproducible migration history for the original base schema

## 8. Next implementation focus

The next focus is provider conformance and controlled rollout:

1. Apply all pending migrations, including conversation reopening and booking migrations, to a controlled Supabase environment.
2. Run Supabase advisors and verify service-role access plus public-role denial.
3. Configure stable HTTPS callbacks and Google/Jobber developer credentials.
4. Run Google OAuth, calendar discovery, FreeBusy, event-create, duplicate, expiry, and revoke tests.
5. Verify Jobber queries, mutations, scopes, schedule blockers, and input shapes in authenticated GraphiQL for the pinned version.
6. Update the Jobber adapter if the authenticated schema differs; only then store matching schema-verification metadata and move a test connection from disabled to shadow.
7. Run shadow mode with controlled data, then enable live booking for one contractor.

No Jobber account should enter live mode merely because OAuth succeeded.

## 9. Verification

From `backend/`, run:

```text
.venv/bin/python -m unittest discover -s tests -v
```

Run type checking from the repository root:

```text
npx --yes pyright
```

Automated tests should mock Twilio, Supabase, Google, and Jobber calls unless a supervised integration test is explicitly being performed. Live SMS, call, database, and provider tests must use controlled test accounts and must not contact real customers unintentionally.
