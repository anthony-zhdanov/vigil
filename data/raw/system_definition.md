# Vigil Workflow System Definition

This document defines the ideal architecture Vigil needs in order for the missed-call recovery workflow to be completely operational. It is not only a description of the current code. It is the build spec for the system that should exist.

The current repo already contains the start of this architecture:

- `backend/app/main.py` receives Twilio webhooks, looks up clients, logs calls/messages, sends SMS, and has a placeholder SMS router.
- `backend/app/decision_tree/` contains a typed decision-tree contract, deterministic classifier, interpreter, and template renderer.
- Supabase has the core operational tables plus the new `client_phone_numbers` mapping table.

The system is not complete until the live SMS webhook uses the decision-tree interpreter, conversation state is persisted, decision runs are audited, owner notifications are sent, Twilio webhooks are validated/idempotent, and the missing repository/service boundaries are implemented.

## 1. Complete Product Workflow

The complete Vigil workflow should be:

1. A customer calls a contractor's normal business number.
2. The contractor misses the call.
3. The contractor's carrier or phone system conditionally forwards the missed call to a Vigil-owned Twilio auxiliary number.
4. Twilio receives the forwarded call.
5. Twilio sends a voice webhook to Vigil.
6. Vigil identifies the client by the Twilio number in the webhook `To` field.
7. Vigil logs the call, creates or updates a lead, starts or updates a conversation, and sends a recovery SMS from the same Twilio auxiliary number.
8. The customer replies by SMS or MMS.
9. Twilio sends an SMS webhook to Vigil.
10. Vigil logs the inbound message, loads the conversation state, runs the decision tree, persists an audit record, executes the returned actions, and sends the next approved template message.
11. Vigil keeps collecting missing facts until the lead is closed, opted out, lost, or ready for owner follow-up.
12. When the lead is urgent or complete, Vigil notifies the contractor or founder with a structured summary.

The customer-facing interaction should feel like this:

```text
Customer calls contractor
Contractor misses call
Vigil texts: "Hi, thanks for calling Example Plumbing. Do you still need plumbing help?"
Customer replies: "Yes, basement drain backing up in Scarborough"
Vigil asks only for missing facts, such as address or urgency
Customer provides missing facts
Vigil confirms handoff and alerts the contractor with a summary
```

## 2. Definition Of "Completely Operational"

The workflow is operational when all of the following are true:

- Twilio voice webhooks are configured on each Vigil Twilio auxiliary number.
- Twilio SMS webhooks are configured on each Vigil Twilio auxiliary number.
- The backend validates Twilio request signatures in non-local environments.
- Every inbound call and SMS is idempotently processed, so Twilio retries do not send duplicate texts.
- The webhook `To` number maps to exactly one active client through `client_phone_numbers`.
- The voice webhook creates/updates the lead and conversation, logs the call, sends the first recovery SMS, and suppresses duplicates.
- The SMS webhook logs inbound messages, including MMS metadata when present.
- The SMS webhook loads persistent conversation state and passes it into the decision tree.
- The decision tree returns structured actions, not arbitrary code or arbitrary prose.
- The action executor sends approved templates, updates lead/conversation state, creates opt-outs, and notifies the owner.
- Every decision-tree run is stored in `decision_tree_runs` for debugging and trust.
- Every outbound SMS is stored in `messages` with the Twilio SID and delivery status.
- Owner notifications are stored in `owner_notifications`.
- Opted-out numbers never receive customer-facing automated texts.
- A test suite covers the major routing paths and idempotency behavior.
- There is a deployed HTTPS backend URL that Twilio can reach without ngrok.

## 3. Target Architecture

The target architecture should separate the system into explicit layers:

```text
Twilio
  -> FastAPI webhook routes
    -> request validation and idempotency
      -> Supabase repositories
        -> workflow service
          -> decision_tree classifier/interpreter
          -> action executor
            -> Twilio outbound SMS
            -> Supabase writes
            -> owner notifications
```

The clean backend layout should eventually look like:

```text
backend/app/
  main.py
  core/
    config.py
    security.py
    time.py
  integrations/
    twilio_client.py
    twilio_payloads.py
  repositories/
    clients.py
    client_phone_numbers.py
    leads.py
    conversations.py
    messages.py
    opt_outs.py
    call_events.py
    decision_tree_runs.py
    owner_notifications.py
    webhook_events.py
  services/
    missed_call_recovery.py
    sms_workflow.py
    action_executor.py
    owner_notifications.py
    duplicate_suppression.py
  decision_tree/
    contract.py
    classifier.py
    interpreter.py
    templates/
      base.py
    trees/
      base/
        plumbing_default.py
```

`main.py` should become thin. It should define FastAPI routes, call the correct service, and return TwiML. Business logic should move into services and repositories.

## 4. Runtime Ownership Boundaries

The complete system needs these boundaries:

- FastAPI routes
  - Parse requests.
  - Validate Twilio signatures.
  - Return TwiML quickly and consistently.
  - Delegate to service functions.

- Repositories
  - Own Supabase table access.
  - Return typed Python dictionaries or dataclasses.
  - Hide raw Supabase client calls from workflow code.

- Workflow services
  - Own the application flow.
  - Decide when to load/create leads and conversations.
  - Call the decision tree.
  - Call the action executor.

- `decision_tree/`
  - Own classification and decision selection.
  - Return `DecisionResult` objects.
  - Never call Twilio.
  - Never write to Supabase.

- Action executor
  - Own side effects from decision actions.
  - Render templates.
  - Send SMS.
  - Create opt-outs.
  - Update leads and conversations.
  - Insert notifications.

- Twilio integration
  - Own Twilio SDK calls.
  - Own request signature validation.
  - Own parsing of Twilio webhook fields.

This boundary matters because decision logic must be auditable and safe. The tree should say "send template X" or "mark lead Y"; it should not directly perform network/database operations.

## 5. Twilio Number Architecture

The webhook `To` field is the source of truth for which Vigil number received a call or SMS.

The backend should identify the client like this:

1. Read `To` from the Twilio webhook.
2. Normalize the phone number to E.164 format if needed.
3. Query `client_phone_numbers`.
4. Require:
   - `phone_number = To`
   - `active = true`
   - `voice_enabled = true` for voice webhooks
   - `sms_enabled = true` for SMS webhooks
5. Join or fetch the related `clients` row.
6. If no active matching row exists, log the event as unknown and do not send customer-facing SMS.

The legacy fallback to `clients.twilio_phone` can remain during migration, but the ideal architecture should use `client_phone_numbers` exclusively.

Each client can have multiple phone-number mappings:

- `twilio_aux`
  - Vigil-controlled Twilio number.
  - Receives forwarded calls.
  - Sends and receives SMS.

- `contractor_main`
  - The contractor's public business number.
  - Useful for setup/reference, but not usually a Twilio sender.

- `owner_mobile`
  - The owner notification target, if stored as a phone-number record instead of on `clients`.

Outbound customer SMS should use:

```text
from_phone = webhook To number
to_phone = customer From number
```

This guarantees the customer receives replies from the same Twilio number that handled the missed call.

## 6. Required Supabase Schema

The current database has a useful start. The complete workflow needs these tables and behaviors.

### `clients`

Stores contractor accounts.

Required fields:

- `id`
- `business_name`
- `owner_name`
- `owner_phone`
- `vertical`
- optional default settings, such as timezone and notification preferences

Build work:

- Keep current table.
- Decide whether `clients.twilio_phone` remains as a legacy field or gets removed after full migration to `client_phone_numbers`.

### `client_phone_numbers`

Maps phone numbers to clients.

Required fields already created:

- `id`
- `client_id`
- `phone_number`
- `phone_type`
- `label`
- `voice_enabled`
- `sms_enabled`
- `active`
- `created_at`
- `updated_at`

Build work:

- Update client lookup to enforce `voice_enabled` or `sms_enabled`.
- Remove reliance on `clients.twilio_phone` once all clients have rows here.
- Add tests for client lookup by active Twilio number.

### `leads`

Tracks the customer/contact for a client.

Required fields:

- `id`
- `client_id`
- `phone_number`
- `status`
- `summary`
- `created_at`
- `updated_at`

Build work:

- Ensure there is a unique constraint on `(client_id, phone_number)` because `upsert_lead()` depends on it.
- Decide the canonical status vocabulary.
- Separate routing statuses from sales lifecycle statuses if needed.

### `conversations`

Tracks the active SMS workflow for a lead.

Required fields:

- `id`
- `client_id`
- `lead_id`
- `channel`
- `status`
- `current_state`
- `collected_info`
- `summary`
- `started_at`
- `last_message_at`
- `closed_at`

Build work:

- Implement repository helpers:
  - get active conversation by `client_id`, `lead_id`, `channel`
  - create conversation
  - update state/status/collected info
  - close conversation
- Ensure there is at most one active SMS conversation per client/lead unless multi-threading is explicitly desired.

### `messages`

Stores inbound and outbound SMS/MMS records.

Required fields:

- `id`
- `client_id`
- `lead_id`
- `conversation_id`
- `direction`
- `from_phone`
- `to_phone`
- `body`
- `twilio_message_sid`
- `template_key`
- `status`
- `raw_payload`
- `created_at`

Build work:

- Change `insert_message()` so it returns the inserted row or at least the inserted `id`.
- Enforce idempotency around inbound `twilio_message_sid`.
- Add delivery status updates from Twilio callbacks.
- Link every decision-tree inbound message to `decision_tree_runs.inbound_message_id`.

### `message_media`

Needed for photos and MMS.

Target fields:

- `id`
- `message_id`
- `client_id`
- `lead_id`
- `twilio_media_url`
- `content_type`
- `storage_url`
- `created_at`

Build work:

- Add a migration for `message_media`.
- Parse `NumMedia`, `MediaUrl0`, and `MediaContentType0` style fields from Twilio.
- Copy Twilio media into durable storage if Twilio URLs are not sufficient.
- Include photo presence in owner summaries.

### `opt_outs`

Prevents further automated texts to a phone number for a client.

Required fields:

- `id`
- `client_id`
- `lead_id`
- `phone_number`
- `reason`
- `source`
- `notes`
- `created_at`

Build work:

- Ensure `(client_id, phone_number)` is unique or create idempotent insert logic.
- Check opt-outs before every customer-facing outbound SMS.
- Still process inbound STOP/wrong-number messages so the opt-out can be recorded.

### `decision_tree_versions`

Stores versioned client-specific decision workflows.

Target fields:

- `id`
- `client_id`
- `tree_key`
- `version`
- `status`
- `definition_json`
- `created_at`
- `approved_at`
- `archived_at`

Build work:

- Add this table if client-specific configurable trees are needed.
- For the MVP, code-defined base trees can be used first, but `decision_tree_runs` should still store `tree_key` and `tree_version`.
- Never silently overwrite active behavior. Add a new version instead.

### `decision_tree_runs`

Audits every automated decision.

Required fields:

- `id`
- `client_id`
- `lead_id`
- `conversation_id`
- `inbound_message_id`
- `decision_tree_key`
- `decision_tree_version`
- `classifier_output`
- `matched_node_key`
- `actions_json`
- `result_status`
- `created_at`

Build work:

- Insert one row for every inbound SMS that reaches the decision tree.
- Store `ClassifierOutput.to_dict()`.
- Store `DecisionResult.actions_json()`.
- Store the matched node key and final result status.

### `owner_notifications`

Tracks notifications sent or queued to the contractor/founder.

Required fields:

- `id`
- `client_id`
- `lead_id`
- `conversation_id`
- `channel`
- `recipient`
- `priority`
- `body`
- `status`
- `raw_response`
- `created_at`

Build work:

- Implement action execution for `notify_owner`.
- Send via Twilio SMS for MVP, or queue for n8n/email/Slack later.
- Insert a row whether send succeeds or fails.

### `webhook_events`

Needed for idempotency.

Target fields:

- `id`
- `provider`
- `event_type`
- `provider_event_id`
- `request_hash`
- `processed_at`
- `created_at`

Build work:

- Add this table.
- For voice events, use `CallSid` plus event type where appropriate.
- For inbound SMS, use `MessageSid`.
- On duplicate webhook delivery, return TwiML without re-sending SMS.

## 7. Ideal Voice Webhook Flow

Endpoint:

```text
POST /webhooks/twilio/voice
```

Complete flow:

1. Parse Twilio form payload.
2. Validate Twilio signature unless running in local development with validation explicitly disabled.
3. Extract:
   - `From`
   - `To`
   - `CallSid`
   - `CallStatus`
4. Check idempotency with `webhook_events`.
5. Look up client by `To` in `client_phone_numbers` with `voice_enabled=true`.
6. If no client is found:
   - Insert an unknown `call_events` row if possible.
   - Return answer-and-hangup TwiML.
7. Upsert the lead for `(client_id, From)`.
8. Get or create an SMS conversation for the lead.
9. Insert a `call_events` row.
10. Check `opt_outs`.
11. Check duplicate suppression for recent `missed_call_initial`.
12. If sending is allowed:
   - Render `missed_call_initial`.
   - Send SMS from `To` to `From`.
   - Insert outbound row in `messages`.
   - Update conversation:
     - `status = waiting_for_customer`
     - `current_state = awaiting_initial_reply`
     - `last_message_at = now()`
13. Return TwiML that answers and hangs up.

The first recovery SMS should be rendered by the shared template system, not the inline `main.py` renderer.

## 8. Ideal SMS Webhook Flow

Endpoint:

```text
POST /webhooks/twilio/sms
```

Complete flow:

1. Parse Twilio form payload.
2. Validate Twilio signature unless disabled for local development.
3. Extract:
   - `From`
   - `To`
   - `Body`
   - `MessageSid`
   - `NumMedia`
   - media URL/content-type fields
4. Check idempotency by `MessageSid`.
5. Look up client by `To` in `client_phone_numbers` with `sms_enabled=true`.
6. If no client is found, log enough context and return empty TwiML.
7. Upsert the lead for `(client_id, From)`.
8. Get or create active conversation.
9. Insert inbound row in `messages` and retain the inserted message ID.
10. Insert any MMS rows in `message_media`.
11. If the customer is opted out:
    - Still allow STOP/wrong-number style processing.
    - Otherwise do not send automated replies.
12. Run the decision tree:

```python
result = run_plumbing_decision_tree(
    message_body=body,
    current_state=conversation["current_state"],
    collected_info=conversation["collected_info"],
)
```

13. Insert `decision_tree_runs` audit row.
14. Execute each returned action in order.
15. Update `conversations` with:
    - `current_state`
    - `status`
    - `collected_info`
    - `summary`
    - `last_message_at`
    - `closed_at` if closed
16. Return empty TwiML.

The SMS webhook is complete only when `route_sms_placeholder()` is gone or bypassed and every normal inbound SMS is handled by `run_plumbing_decision_tree()`.

## 9. Decision Tree Architecture

The decision-tree package is the workflow brain. It should remain deterministic and auditable.

Current files:

- `contract.py`
  - Defines `ClassifierOutput`, `DecisionAction`, and `DecisionResult`.

- `trees/base/plumbing_default.py`
  - Defines the base plumbing tree key/version and keyword vocabulary.

- `classifier.py`
  - Turns SMS body plus conversation state into a structured classifier output.

- `interpreter.py`
  - Turns classifier output plus collected facts into a decision result.

- `templates/base.py`
  - Renders approved template text.

Complete decision-tree execution should look like:

```text
Inbound SMS body
  -> classifier
    -> ClassifierOutput
      -> interpreter
        -> DecisionResult
          -> action executor
            -> Twilio/Supabase side effects
```

The tree should never:

- generate arbitrary customer-facing prose
- call Twilio directly
- call Supabase directly
- decide to execute arbitrary code from a database field

The tree should only return allowed actions:

- `send_sms_template`
- `mark_lead_status`
- `create_opt_out`
- `notify_owner`
- `close_conversation`

Any new action type must be added to `contract.py`, tested, and explicitly handled by the action executor.

## 10. Message Template Architecture

The template architecture should guarantee that customer-facing copy is approved.

For MVP:

- Keep base templates in `decision_tree/templates/base.py`.
- Use template keys returned by the decision tree.
- Render using client and conversation context.

For client-specific workflows:

- Add a `message_templates` or `template_versions` table.
- Store:
  - `client_id`
  - `template_key`
  - `version`
  - `body`
  - `status`
  - `approved_at`
  - `archived_at`
- Resolve templates in this order:
  1. active client-specific template
  2. vertical-specific default template
  3. base default template

Required build work:

- Replace `main.py::render_template()` with `decision_tree/templates/base.py::render_template()`.
- Ensure every `send_sms_template` action has a valid template key.
- Add tests proving every template key emitted by the tree can be rendered.

## 11. Action Executor Architecture

The action executor is the bridge between the decision tree and side effects.

Input:

- `DecisionResult`
- client
- lead
- conversation
- inbound message
- webhook `From` and `To`

For each `DecisionAction`:

### `send_sms_template`

Required behavior:

1. Check opt-out before sending customer-facing SMS.
2. Render the template.
3. Send SMS through Twilio:

```python
twilio_client.messages.create(
    from_=twilio_number,
    to=customer_phone,
    body=rendered_body,
)
```

4. Insert outbound row in `messages`.
5. Store `template_key`, status, body, and Twilio SID.

### `mark_lead_status`

Required behavior:

1. Update `leads.status`.
2. Update `leads.updated_at`.
3. Optionally update `leads.summary`.

### `create_opt_out`

Required behavior:

1. Insert opt-out idempotently.
2. Include reason, source, and lead ID.
3. Ensure future customer-facing SMS is blocked.

### `notify_owner`

Required behavior:

1. Resolve owner recipient.
2. Render `owner_notification`.
3. Send or queue the notification.
4. Insert `owner_notifications` row.
5. Mark failures as `failed`, not silently successful.

### `close_conversation`

Required behavior:

1. Set conversation status to `closed`.
2. Set `closed_at`.
3. Preserve final collected info and summary.

## 12. Conversation State Architecture

The decision tree needs persistent state across SMS replies.

Expected states:

- `awaiting_initial_reply`
- `awaiting_location`
- `awaiting_job_type`
- `awaiting_urgency`
- `closed`

Expected statuses:

- `open`
- `waiting_for_customer`
- `closed`

`collected_info` should accumulate facts:

```json
{
  "location": "123 King St W",
  "job_type": "drain_or_sewer",
  "urgency": "today",
  "latest_summary": "Customer said the basement drain is backing up.",
  "emergency_notified": false,
  "photo_received": true
}
```

Build work:

- Implement `get_or_create_active_conversation()`.
- Pass `current_state` and `collected_info` into the decision tree.
- Persist `DecisionResult.conversation_state`.
- Persist `DecisionResult.conversation_status`.
- Persist `DecisionResult.collected_info`.
- Persist `DecisionResult.summary`.
- Close conversations when the tree returns `close_conversation`.

Without persistent conversation state, the system cannot ask only for missing facts and cannot complete the multi-message workflow.

## 13. Owner Notification Architecture

Owner notification should be a first-class workflow output, not an ad hoc print statement.

Notification trigger cases:

- Emergency detected.
- Lead has all required facts.
- Customer needs human follow-up.
- Customer asks something outside the automation scope.
- Twilio send fails or the workflow errors in a way that needs human intervention.

For MVP:

- Recipient can be `clients.owner_phone`.
- Channel can be SMS through Twilio.
- Body can come from `owner_notification` in `decision_tree/templates/base.py`.

Required owner notification content:

- Client business name.
- Lead phone number.
- Priority.
- Location.
- Job type.
- Urgency.
- Latest inbound message.
- Conversation summary.
- Link to dashboard later, if a dashboard exists.

Build work:

- Implement `notify_owner` action.
- Insert `owner_notifications` rows.
- Avoid duplicate emergency notifications by preserving `emergency_notified=true` in `collected_info`.

## 14. MMS And Photo Architecture

Photo collection is part of the product goal, but not yet implemented.

Twilio MMS webhook fields include:

- `NumMedia`
- `MediaUrl0`
- `MediaContentType0`
- `MediaUrl1`
- `MediaContentType1`

Complete behavior:

1. Parse media fields in the SMS webhook.
2. Store inbound message first.
3. Insert one `message_media` row per attachment.
4. Optionally copy media into Supabase Storage or another durable store.
5. Add `photo_received=true` to `collected_info`.
6. Include photo presence in owner notifications.

The base tree does not yet require a photo. A future tree can add:

- `request_photo` template
- `awaiting_photo` state
- classifier logic for detecting media-only replies

## 15. Security Architecture

Required security work:

- Use `RequestValidator` to validate Twilio signatures.
- Make validation mandatory outside local development.
- Keep `SUPABASE_SERVICE_ROLE_KEY` server-side only.
- Never expose service-role keys to frontend code.
- Avoid logging secrets.
- Reduce PII in logs where possible.
- Add RLS policies or keep service-role-only access clearly documented.

The code already imports `RequestValidator`, but signature validation is not fully wired into the webhook flow. This must be built before production use.

## 16. Idempotency Architecture

Twilio can retry webhooks. Without idempotency, retries can create duplicate logs and send duplicate texts.

Required behavior:

- Inbound SMS idempotency key: `MessageSid`.
- Voice webhook idempotency key: `CallSid` plus relevant event type/status.
- Store processed events in `webhook_events`.
- If an event already exists, return TwiML without re-running side effects.

Build work:

- Add `webhook_events` migration.
- Implement `begin_webhook_event()` helper.
- Implement `mark_webhook_event_processed()` helper.
- Wrap side effects so duplicate inbound SMS cannot send duplicate outbound SMS.

## 17. Delivery Status Architecture

The current code logs outbound Twilio SIDs, but delivery status tracking should be added.

Target Twilio status callback endpoint:

```text
POST /webhooks/twilio/status
```

Complete behavior:

1. Twilio sends message status updates.
2. Backend validates signature.
3. Backend finds `messages` row by `twilio_message_sid`.
4. Backend updates `messages.status`.
5. Failures can trigger owner notification or retry logic if appropriate.

Build work:

- Add status callback route.
- Configure Twilio outbound SMS to use `status_callback`.
- Add tests for delivered/failed updates.

## 18. Testing Requirements

Minimum unit tests:

- `classify_plumbing_sms()`:
  - opt-out
  - wrong number
  - no longer needed
  - location extraction
  - job type extraction
  - urgency extraction
  - unclear message

- `run_plumbing_decision_tree()`:
  - opt-out route
  - wrong-number route
  - no-longer-needed route
  - unclear initial reply
  - collect location
  - collect job type
  - collect urgency
  - emergency route
  - complete handoff route

- Template rendering:
  - every emitted template key renders successfully
  - owner notification renders with missing optional fields

- Repositories:
  - client lookup by `client_phone_numbers`
  - active/inactive number behavior
  - voice/sms enabled flags
  - lead upsert
  - conversation get/create/update
  - idempotent opt-out insert

- Webhooks:
  - voice webhook sends initial SMS once
  - duplicate voice webhook does not duplicate SMS
  - SMS webhook logs inbound message
  - duplicate SMS webhook does not duplicate response
  - opted-out customer receives no automated response
  - unknown Twilio number does not send SMS

Minimum integration tests:

- FastAPI `TestClient` tests for `/health`, voice webhook, SMS webhook, status callback.
- Mock Twilio client so tests do not send real SMS.
- Mock or isolated Supabase/local database layer.

Manual end-to-end test:

1. Deploy backend to public HTTPS URL.
2. Configure Twilio voice webhook.
3. Configure Twilio messaging webhook.
4. Configure status callback.
5. Call the Twilio auxiliary number.
6. Confirm call event, lead, conversation, and outbound SMS.
7. Reply with a normal lead message.
8. Confirm inbound message, decision run, next SMS, and state update.
9. Reply with missing facts until handoff.
10. Confirm owner notification.
11. Test STOP.
12. Test duplicate webhook retry behavior.

## 19. Build Plan

### Phase 1: Finish Data Model

Build:

- `webhook_events` table.
- `message_media` table.
- Any missing constraints/indexes for:
  - `(client_id, phone_number)` on `leads`
  - inbound `twilio_message_sid`
  - active conversation lookup
  - opt-out uniqueness
- Optional `decision_tree_versions` and `message_templates` if client-specific configuration is required immediately.

Definition of done:

- Migrations are checked into `supabase/migrations`.
- Remote Supabase migration history matches local files.
- MCP `list_tables` shows expected tables.
- Schema supports idempotency and persistent conversation state.

### Phase 2: Create Repository Layer

Build repository functions for:

- client lookup by Twilio number
- lead upsert
- call event insert
- message insert returning row/id
- media insert
- opt-out lookup/create
- active conversation get/create/update/close
- decision-tree run insert
- owner notification insert
- webhook event idempotency

Definition of done:

- `main.py` no longer contains raw Supabase query chains for every operation.
- Repository functions are unit-tested or covered through service tests.

### Phase 3: Create Workflow Services

Build:

- `missed_call_recovery.py` for voice webhook orchestration.
- `sms_workflow.py` for SMS webhook orchestration.
- `action_executor.py` for executing `DecisionAction` objects.
- `duplicate_suppression.py` for recovery SMS suppression.

Definition of done:

- Voice and SMS route functions in `main.py` are thin.
- Workflow services can be tested without constructing raw FastAPI requests.

### Phase 4: Wire Decision Tree Into Live SMS

Build:

- Replace `route_sms_placeholder()` in the SMS path.
- Load current conversation state.
- Call `run_plumbing_decision_tree()`.
- Insert `decision_tree_runs`.
- Execute returned actions.
- Persist resulting conversation state.

Definition of done:

- A normal SMS reply can move through location, job type, urgency, and handoff states.
- `decision_tree_runs` contains the classifier output and actions for each inbound SMS.
- All emitted template keys can be rendered.

### Phase 5: Unify Templates

Build:

- Use `decision_tree/templates/base.py::render_template()` for initial recovery SMS and decision-tree replies.
- Remove or deprecate `main.py::render_template()`.
- Add missing templates if the tree emits new keys.

Definition of done:

- There is one rendering path for approved templates.
- The first missed-call SMS and follow-up SMS use the same template system.

### Phase 6: Owner Notifications

Build:

- Execute `notify_owner` actions.
- Render `owner_notification`.
- Send to `clients.owner_phone` or configured recipient.
- Insert `owner_notifications` row.
- Mark failures.

Definition of done:

- Emergency messages notify the owner once.
- Complete handoff messages notify the owner with summary.
- Notification rows are created for success and failure.

### Phase 7: Twilio Hardening

Build:

- Twilio signature validation.
- `webhook_events` idempotency.
- SMS status callback route.
- Delivery status updates.
- Voice/SMS enabled checks on `client_phone_numbers`.

Definition of done:

- Twilio retries do not duplicate outbound messages.
- Invalid signatures are rejected outside local dev.
- Outbound message delivery statuses update in Supabase.

### Phase 8: MMS/Photos

Build:

- Parse Twilio MMS fields.
- Store media metadata.
- Optionally copy media to durable storage.
- Include media presence in owner summaries.

Definition of done:

- A customer can text a photo.
- The inbound message and media metadata are linked.
- Owner notification can mention that a photo was received.

### Phase 9: End-To-End Verification

Build/verify:

- Hosted backend.
- Twilio voice webhook.
- Twilio SMS webhook.
- Twilio status callback.
- Full call to handoff flow.
- STOP/opt-out.
- Duplicate webhook retry.
- Unknown number behavior.

Definition of done:

- A real missed call triggers a real recovery SMS.
- A real SMS reply advances the decision tree.
- A real owner notification is sent.
- Supabase audit tables prove what happened.

## 20. Current Gaps In The Repo

The most important gaps are:

- `main.py` still uses `route_sms_placeholder()` instead of `run_plumbing_decision_tree()`.
- Conversation state is not yet loaded and updated by the SMS webhook.
- `insert_message()` does not return the inserted message ID.
- `decision_tree_runs` is not written by live webhook code.
- `owner_notifications` is not executed from `notify_owner` actions.
- `main.py::render_template()` duplicates the decision-tree template renderer and only supports three templates.
- Twilio signature validation is imported but not fully enforced.
- Webhook idempotency is missing.
- MMS/photo parsing is missing.
- Delivery status callback is missing.
- Repository/service boundaries are not yet split out of `main.py`.

## 21. Implementation Priority

The highest-leverage sequence is:

1. Add idempotency and missing repository helpers.
2. Make `insert_message()` return an ID.
3. Implement conversation get/create/update.
4. Wire `run_plumbing_decision_tree()` into `/webhooks/twilio/sms`.
5. Execute `DecisionAction` objects.
6. Persist `decision_tree_runs`.
7. Replace inline templates with shared decision-tree templates.
8. Add owner notification execution.
9. Add Twilio signature validation and status callbacks.
10. Add MMS/photo support.

This sequence gets the workflow operational before expanding into richer media, dashboards, or client-specific editable tree versions.

## 22. One-Sentence Target

The target Vigil architecture is a Twilio-triggered FastAPI workflow where the webhook `To` number identifies the client, Supabase stores every lead/conversation/message/decision, `decision_tree/` returns audited action objects based on persistent conversation state, and a dedicated executor sends only approved template messages through the correct Twilio number.
