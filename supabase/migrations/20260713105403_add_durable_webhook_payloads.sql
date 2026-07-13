alter table public.webhook_events
  add column if not exists raw_payload jsonb not null default '{}'::jsonb,
  add column if not exists attempts integer not null default 0 check (attempts >= 0),
  add column if not exists last_attempted_at timestamptz,
  add column if not exists last_error text;

create index if not exists webhook_events_provider_unprocessed_idx
  on public.webhook_events(provider, created_at)
  where processed_at is null;

revoke all on table public.webhook_events from anon, authenticated;
grant select, insert, update, delete on table public.webhook_events to service_role;
