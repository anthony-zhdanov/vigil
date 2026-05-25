# Rough Backend Structure — Vigil Python/FastAPI Backend

> Purpose: define the backend shape for Vigil's missed-call recovery MVP using Python, FastAPI, Twilio, and Supabase Postgres.
>
> Stack decision: use **Python + FastAPI** for the backend.

---

## 1. Backend responsibility

The backend is the product brain. Twilio handles the phone network; FastAPI handles the business logic.

The backend should:

1. Receive Twilio webhooks for calls and SMS.
2. Identify which Vigil client owns the Twilio auxiliary number.
3. Log every call and message.
4. Create/update leads and conversations.
5. Suppress duplicates.
6. Respect opt-outs and wrong-number requests.
7. Apply SMS decision logic.
8. Send approved SMS templates through Twilio.
9. Notify owner/founder when human action is needed.
10. Store enough data to produce weekly recovered-lead reports.

---

## 2. Recommended app structure

```text
backend/
  app/
    main.py                         # FastAPI app creation, router registration
    core/
      config.py                     # environment variables/settings
      logging.py                    # logging setup
      security.py                   # Twilio signature validation, if enabled
    api/
      routes/
        health.py                   # GET /health
        twilio_voice.py             # POST /webhooks/twilio/voice
        twilio_sms.py               # POST /webhooks/twilio/sms
        twilio_status.py            # optional delivery/call status webhooks
    db/
      session.py                    # database connection/session
      models.py                     # SQLAlchemy/SQLModel models, if used
      migrations/                   # Alembic migrations, if used
    schemas/
      twilio.py                     # parsed Twilio webhook payload models
      decision_tree.py              # decision tree config schema
      lead.py
      message.py
    services/
      clients.py                    # client lookup by Twilio number
      leads.py                      # create/update leads
      conversations.py              # conversation lifecycle
      calls.py                      # call-event logging
      messages.py                   # message logging
      opt_outs.py                   # opt-out checks/writes
      duplicate_suppression.py      # recent-message suppression
      twilio_service.py             # Twilio Python SDK wrapper
      notifications.py              # owner/founder/n8n alerts
      classifier.py                 # regex/LLM classification wrapper
      decision_engine.py            # executes decision tree/config
      templates.py                  # message template rendering
    tests/
      test_voice_webhook.py
      test_sms_webhook.py
      test_decision_engine.py
      test_opt_outs.py
      test_duplicate_suppression.py
  pyproject.toml
  README.md
  .env.example
```

Keep the backend simple. Do not add queues, Kubernetes, microservices, or a visual workflow builder for the MVP.

---

## 3. HTTP endpoints

### `GET /health`

Simple uptime check.

Response:

```json
{ "status": "ok" }
```

### `POST /webhooks/twilio/voice`

Called by Twilio when the aux number receives a forwarded missed call.

Input is Twilio form data, usually `application/x-www-form-urlencoded`.

Important fields:

- `From`: caller/customer number
- `To`: Twilio aux number
- `CallSid`: Twilio's unique call ID
- `CallStatus`: call state
- `Direction`: call direction

Backend flow:

1. Parse Twilio form data.
2. Optionally validate Twilio request signature.
3. Find client by `To` number.
4. Log call event.
5. Check opt-out list.
6. Check duplicate suppression window.
7. Create/update lead.
8. Create/open conversation.
9. Send initial recovery SMS if allowed.
10. Return TwiML to Twilio.

Return type: `text/xml`.

Example TwiML:

```xml
<Response>
  <Hangup/>
</Response>
```

### `POST /webhooks/twilio/sms`

Called by Twilio when the aux number receives an SMS reply.

Important fields:

- `From`: customer number
- `To`: Twilio aux number
- `Body`: message body
- `MessageSid`: Twilio's unique SMS ID

Backend flow:

1. Parse Twilio form data.
2. Optionally validate Twilio request signature.
3. Find client by `To` number.
4. Find/create lead by client + `From` number.
5. Open/create conversation.
6. Save inbound message.
7. Classify message intent/urgency.
8. Run decision engine.
9. Execute selected actions: send SMS, mark status, opt out, notify owner, schedule follow-up.
10. Save decision-tree run audit record.
11. Return empty TwiML response.

Return type: `text/xml`.

Example:

```xml
<Response></Response>
```

### `POST /webhooks/twilio/status` optional

Used later for SMS delivery status or call status updates.

---

## 4. Database structure

Use Supabase Postgres as the source of truth.

### `clients`

Vigil's actual customers, e.g. plumbing businesses.

Suggested fields:

- `id` uuid primary key
- `business_name` text not null
- `owner_name` text
- `owner_phone` text
- `owner_email` text
- `contractor_main_phone` text
- `default_service_area` text
- `timezone` text default `America/Toronto`
- `status` text: `audit`, `pilot`, `active`, `paused`, `churned`
- `created_at` timestamptz
- `updated_at` timestamptz

### `client_phone_numbers`

Maps Twilio aux numbers to clients. This is cleaner than storing only one Twilio number directly on `clients`, because a future client may have multiple locations/numbers.

Suggested fields:

- `id` uuid primary key
- `client_id` uuid references `clients(id)`
- `phone_number` text not null unique
- `phone_type` text: `twilio_aux`, `contractor_main`, `owner_mobile`
- `voice_enabled` boolean
- `sms_enabled` boolean
- `active` boolean
- `created_at` timestamptz

### `leads`

Customers who call/text a Vigil client.

Suggested fields:

- `id` uuid primary key
- `client_id` uuid references `clients(id)`
- `phone_number` text not null
- `status` text: `new`, `texted`, `needs_address`, `needs_owner_call`, `emergency`, `price_question`, `lost`, `no_response`, `booked`, `wrong_number`, `opted_out`
- `first_seen_at` timestamptz
- `last_seen_at` timestamptz
- `last_inbound_at` timestamptz
- `last_outbound_at` timestamptz
- `source` text: `missed_call`, `sms_reply`, `manual`, etc.
- `summary` text
- unique constraint on `(client_id, phone_number)`

### `conversations`

Tracks an SMS thread as a whole. This answers: where did the conversation start, is it still open, and what lead does it belong to?

Suggested fields:

- `id` uuid primary key
- `client_id` uuid references `clients(id)`
- `lead_id` uuid references `leads(id)`
- `channel` text: `sms`
- `status` text: `open`, `waiting_for_customer`, `waiting_for_owner`, `closed`
- `started_at` timestamptz
- `last_message_at` timestamptz
- `closed_at` timestamptz
- `current_decision_tree_version_id` uuid references `decision_tree_versions(id)`

Important: messages do **not** need a “next message” pointer. Store every message with `conversation_id` and `created_at`. The conversation order is determined by timestamp and, if needed, an increasing sequence number.

### `call_events`

Every individual call event Vigil handles.

Suggested fields:

- `id` uuid primary key
- `client_id` uuid references `clients(id)`
- `lead_id` uuid references `leads(id)` nullable until matched
- `twilio_call_sid` text unique
- `from_number` text
- `to_number` text
- `call_status` text
- `direction` text
- `raw_payload` jsonb
- `created_at` timestamptz

### `messages`

Every inbound and outbound SMS.

Suggested fields:

- `id` uuid primary key
- `client_id` uuid references `clients(id)`
- `lead_id` uuid references `leads(id)`
- `conversation_id` uuid references `conversations(id)`
- `direction` text: `inbound` or `outbound`
- `from_number` text
- `to_number` text
- `body` text
- `twilio_message_sid` text unique nullable
- `template_key` text nullable
- `status` text: `received`, `queued`, `sent`, `delivered`, `failed`
- `raw_payload` jsonb
- `created_at` timestamptz

### `opt_outs`

Suppression list. If a customer opts out, do not send them recovery texts for that client.

Suggested fields:

- `id` uuid primary key
- `client_id` uuid references `clients(id)`
- `lead_id` uuid references `leads(id)` nullable
- `phone_number` text not null
- `reason` text: `stop`, `wrong_number`, `manual`, `complaint`
- `created_at` timestamptz
- unique constraint on `(client_id, phone_number)`

### `message_templates`

Approved reusable messages. These should be client-specific when needed.

Suggested fields:

- `id` uuid primary key
- `client_id` uuid references `clients(id)` nullable for global defaults
- `template_key` text not null
- `body` text not null
- `active` boolean
- `created_at` timestamptz
- `updated_at` timestamptz

Example template keys:

- `missed_call_initial`
- `missed_call_second_followup`
- `opt_out_confirm`
- `no_longer_needed`
- `emergency_ack`
- `price_question`
- `request_address_details`
- `handoff_to_team`
- `clarification`

### `decision_tree_versions`

Stores client-specific decision logic configuration.

Suggested fields:

- `id` uuid primary key
- `client_id` uuid references `clients(id)`
- `version` integer not null
- `status` text: `draft`, `active`, `archived`
- `definition_json` jsonb not null
- `created_by` text
- `approved_by` text
- `created_at` timestamptz
- `approved_at` timestamptz
- unique constraint on `(client_id, version)`

### `decision_tree_runs`

Audit trail for each automated decision.

Suggested fields:

- `id` uuid primary key
- `client_id` uuid references `clients(id)`
- `lead_id` uuid references `leads(id)`
- `conversation_id` uuid references `conversations(id)`
- `inbound_message_id` uuid references `messages(id)` nullable
- `decision_tree_version_id` uuid references `decision_tree_versions(id)`
- `classifier_output` jsonb
- `matched_node_key` text
- `actions_json` jsonb
- `result_status` text
- `created_at` timestamptz

### `owner_notifications`

Records notifications sent to the founder, owner, or external systems.

Suggested fields:

- `id` uuid primary key
- `client_id` uuid references `clients(id)`
- `lead_id` uuid references `leads(id)`
- `channel` text: `sms`, `email`, `slack`, `n8n`
- `recipient` text
- `body` text
- `status` text: `queued`, `sent`, `failed`
- `raw_response` jsonb
- `created_at` timestamptz

---

## 5. How to store conversations

Do not store conversations as one giant text blob.

Do not use linked-list fields like “is there another message after this?”

Use:

- `conversations` for the overall thread
- `messages` for each individual SMS
- `messages.created_at` for ordering
- optional `messages.sequence_number` later if timestamp ordering is not enough

Example:

```text
conversation_id = abc
  message 1: outbound, missed_call_initial, 10:00:00
  message 2: inbound, "Yes basement flooding", 10:02:00
  message 3: outbound, emergency_ack, 10:02:05
```

This makes reporting, debugging, and weekly summaries much easier.

---

## 6. Decision-tree storage strategy

Question: what is the best way to define and store custom SMS response decision logic for each client?

Recommended answer for MVP:

1. **Decision engine in Python code.** The Python code knows how to evaluate conditions and execute allowed actions.
2. **Client-specific configuration in Postgres JSONB.** The database stores each client's keywords, templates, escalation preferences, and enabled branches.
3. **Versioned tree definitions.** Never silently overwrite live logic. Each client has active/draft/archived versions.
4. **Limited action types.** The database should not contain arbitrary Python code. It should only contain safe declarative actions like `send_sms_template`, `notify_owner`, and `mark_lead_status`.
5. **Audit every run.** Store what the classifier saw, what node matched, what action happened, and why.

Avoid for MVP:

- Hardcoding every client's decision tree directly in Python.
- Letting an LLM decide and send arbitrary customer-facing messages.
- Building a visual workflow editor before proving the business.
- Storing executable code in the database.

---

## 7. Example decision-tree definition

A simplified `definition_json` could look like this:

```json
{
  "tree_key": "missed_call_recovery_v1",
  "duplicate_suppression_minutes": 60,
  "followups": {
    "second_followup_after_minutes": 15,
    "close_after_hours": 24
  },
  "classifiers": {
    "emergency_keywords": ["burst", "flood", "leak", "sewage", "no water", "pipe broke"],
    "opt_out_keywords": ["stop", "unsubscribe", "wrong number"],
    "price_keywords": ["price", "cost", "how much", "quote"]
  },
  "nodes": [
    {
      "key": "opt_out",
      "when": { "intent": "opt_out" },
      "actions": [
        { "type": "send_sms_template", "template_key": "opt_out_confirm" },
        { "type": "create_opt_out", "reason": "customer_request" },
        { "type": "mark_lead_status", "status": "opted_out" },
        { "type": "end_conversation" }
      ]
    },
    {
      "key": "emergency",
      "when": { "urgency": "emergency" },
      "actions": [
        { "type": "send_sms_template", "template_key": "emergency_ack" },
        { "type": "notify_owner", "priority": "urgent" },
        { "type": "mark_lead_status", "status": "emergency" }
      ]
    },
    {
      "key": "price_question",
      "when": { "intent": "price_question" },
      "actions": [
        { "type": "send_sms_template", "template_key": "price_question" },
        { "type": "notify_owner", "priority": "normal" },
        { "type": "mark_lead_status", "status": "price_question" }
      ]
    },
    {
      "key": "needs_address",
      "when": { "has_address": false },
      "actions": [
        { "type": "send_sms_template", "template_key": "request_address_details" },
        { "type": "mark_lead_status", "status": "needs_address" }
      ]
    },
    {
      "key": "handoff_to_team",
      "when": { "has_address": true, "has_job_details": true },
      "actions": [
        { "type": "notify_owner", "priority": "normal" },
        { "type": "send_sms_template", "template_key": "handoff_to_team" },
        { "type": "mark_lead_status", "status": "needs_owner_call" }
      ]
    },
    {
      "key": "fallback_unclear",
      "when": { "always": true },
      "actions": [
        { "type": "send_sms_template", "template_key": "clarification" },
        { "type": "mark_lead_status", "status": "needs_clarification" }
      ]
    }
  ]
}
```

The engine evaluates nodes in order and executes the first matching node unless configured otherwise.

---

## 8. Core SMS decision tree

```text
Missed call captured
├─ Caller opted out
│  ├─ Log ignored call
│  └─ End
├─ Caller/client recently received recovery SMS
│  ├─ Suppress duplicate
│  └─ End
└─ Valid missed call
   ├─ Create/update lead
   ├─ Create/open conversation
   ├─ Send template: missed_call_initial
   └─ Wait for reply

Customer replies
├─ Opt-out / wrong number
│  ├─ Send template: opt_out_confirm
│  ├─ Mark opted_out or wrong_number
│  └─ End
├─ No longer needed / already handled
│  ├─ Send template: no_longer_needed
│  ├─ Mark lost
│  └─ End
├─ Emergency keyword or LLM emergency
│  ├─ Send template: emergency_ack
│  ├─ Notify owner/founder immediately
│  ├─ Mark emergency
│  └─ End
├─ Price question
│  ├─ Send template: price_question
│  ├─ Notify owner/founder
│  └─ Mark price_question
├─ No address/details
│  ├─ Send template: request_address_details
│  └─ Mark needs_address
├─ Address/details provided
│  ├─ Notify owner/founder with customer number, message, job type, urgency, summary
│  ├─ Send template: handoff_to_team
│  └─ Mark needs_owner_call
└─ Unclear
   ├─ Send template: clarification
   └─ Mark needs_clarification
```

If no reply after 15 minutes, send one second follow-up:

```text
Just checking — if you still need help, reply here with what’s going on and we’ll get back to you.
```

If no reply after the final follow-up window, mark `no_response`.

---

## 9. Classifier strategy

Use deterministic logic first:

- Opt-out keywords: `STOP`, `unsubscribe`, `wrong number`, etc.
- Emergency keywords: `flood`, `burst`, `sewage`, `gas`, `no water`, etc.
- Price keywords: `cost`, `price`, `quote`, `how much`, etc.

Use an LLM only as a classifier/summarizer, not as a free-form conversation agent.

Possible classifier output:

```json
{
  "intent": "emergency",
  "urgency": "emergency",
  "job_type": "burst_pipe",
  "has_address": false,
  "has_job_details": true,
  "summary": "Customer says a pipe burst in the basement.",
  "confidence": 0.92
}
```

The decision engine uses this structured output to select an approved template.

---

## 10. Allowed decision actions

Initial safe action types:

- `send_sms_template`
- `mark_lead_status`
- `create_opt_out`
- `notify_owner`
- `schedule_followup`
- `suppress_duplicate`
- `end_conversation`

Do not allow arbitrary code execution from decision-tree JSON.

---

## 11. Minimal first implementation order

Build in this order:

1. FastAPI app with `GET /health`.
2. Twilio voice webhook that logs request body and returns TwiML.
3. ngrok tunnel to local FastAPI app.
4. Twilio Console webhook pointed to ngrok URL.
5. Twilio SMS webhook that logs inbound messages.
6. Twilio outbound SMS wrapper.
7. Supabase tables for clients, leads, call events, messages, conversations, opt-outs.
8. Client lookup by Twilio aux number.
9. Initial missed-call recovery SMS.
10. Duplicate suppression and opt-out handling.
11. Template rendering.
12. Basic decision engine.
13. Owner/founder notification.
14. Decision-tree run audit records.

---

## 12. Minimal local test flow

```text
1. Run FastAPI locally on port 8000.
2. Run ngrok: ngrok http 8000.
3. Put ngrok HTTPS URL into Twilio voice and SMS webhook settings.
4. Call the Twilio aux number.
5. Confirm FastAPI logs the voice webhook.
6. Confirm Twilio receives valid TwiML.
7. Text the Twilio aux number.
8. Confirm FastAPI logs the SMS webhook.
9. Add outbound SMS sending.
10. Confirm customer receives recovery text.
```
