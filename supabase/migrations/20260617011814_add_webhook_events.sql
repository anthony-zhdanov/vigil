create table if not exists public.webhook_events (
  id uuid primary key default gen_random_uuid(),
  provider text not null,
  event_type text not null,
  provider_event_id text,
  request_hash text not null,
  processed_at timestamptz,
  created_at timestamptz not null default now()
);

create unique index if not exists webhook_events_provider_event_id_key
  on public.webhook_events(provider, event_type, provider_event_id)
  where provider_event_id is not null;

create unique index if not exists webhook_events_provider_request_hash_key
  on public.webhook_events(provider, event_type, request_hash);

create index if not exists webhook_events_created_at_idx
  on public.webhook_events(created_at desc);

create index if not exists webhook_events_unprocessed_idx
  on public.webhook_events(created_at)
  where processed_at is null;

alter table public.webhook_events enable row level security;

