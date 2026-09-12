create table if not exists public.booking_connections (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients(id) on delete cascade,
  provider text not null check (provider in ('google', 'jobber')),
  status text not null default 'pending' check (
    status in (
      'pending',
      'connected',
      'expired',
      'revoked',
      'availability_unsupported',
      'error',
      'disconnected'
    )
  ),
  provider_account_id text,
  provider_account_name text,
  access_token_encrypted text,
  refresh_token_encrypted text,
  token_expires_at timestamptz,
  scopes text[] not null default '{}',
  token_version integer not null default 1 check (token_version > 0),
  metadata jsonb not null default '{}'::jsonb,
  last_validated_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (client_id, provider)
);

create table if not exists public.booking_configs (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null unique references public.clients(id) on delete cascade,
  connection_id uuid references public.booking_connections(id) on delete set null,
  resource_id text,
  resource_name text,
  timezone text not null default 'America/Toronto',
  mode text not null default 'disabled' check (mode in ('disabled', 'shadow', 'live')),
  enabled boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.booking_services (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients(id) on delete cascade,
  service_key text not null,
  display_name text not null,
  provider_service_id text,
  duration_minutes integer not null check (duration_minutes between 15 and 1440),
  lead_time_minutes integer not null default 60 check (lead_time_minutes >= 0),
  buffer_before_minutes integer not null default 0 check (buffer_before_minutes >= 0),
  buffer_after_minutes integer not null default 0 check (buffer_after_minutes >= 0),
  horizon_days integer not null default 30 check (horizon_days between 1 and 365),
  slot_interval_minutes integer not null default 30 check (slot_interval_minutes between 5 and 1440),
  weekly_hours jsonb not null default '{}'::jsonb,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (client_id, service_key)
);

create table if not exists public.booking_slot_offers (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients(id) on delete cascade,
  lead_id uuid not null references public.leads(id) on delete cascade,
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  connection_id uuid not null references public.booking_connections(id) on delete cascade,
  service_id uuid not null references public.booking_services(id) on delete restrict,
  resource_id text not null,
  slots jsonb not null,
  page_index integer not null default 0 check (page_index >= 0),
  expires_at timestamptz not null,
  status text not null default 'pending' check (
    status in ('pending', 'selected', 'expired', 'replaced', 'booked')
  ),
  selected_slot_index integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.bookings (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients(id) on delete cascade,
  lead_id uuid not null references public.leads(id) on delete cascade,
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  connection_id uuid not null references public.booking_connections(id) on delete restrict,
  service_id uuid not null references public.booking_services(id) on delete restrict,
  slot_offer_id uuid references public.booking_slot_offers(id) on delete set null,
  provider text not null check (provider in ('google', 'jobber')),
  status text not null default 'pending' check (
    status in ('pending', 'creating', 'confirmed', 'unknown', 'failed', 'cancelled')
  ),
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  timezone text not null,
  customer_name text not null,
  customer_phone text not null,
  customer_address text not null,
  urgency text,
  idempotency_key text not null unique,
  external_client_id text,
  external_property_id text,
  external_job_id text,
  external_visit_id text,
  external_event_id text,
  external_data jsonb not null default '{}'::jsonb,
  failure_code text,
  failure_message text,
  confirmed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (ends_at > starts_at)
);

create table if not exists public.booking_setup_tokens (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients(id) on delete cascade,
  token_hash text not null unique,
  expires_at timestamptz not null,
  claimed_at timestamptz,
  revoked_at timestamptz,
  session_hash text unique,
  session_expires_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists public.booking_oauth_states (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients(id) on delete cascade,
  provider text not null check (provider in ('google', 'jobber')),
  state_hash text not null unique,
  code_verifier_encrypted text,
  redirect_uri text not null,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);

create unique index if not exists booking_slot_offers_one_pending_per_conversation_key
  on public.booking_slot_offers(conversation_id)
  where status = 'pending';

create unique index if not exists bookings_one_confirmed_per_conversation_key
  on public.bookings(conversation_id)
  where status = 'confirmed';

create unique index if not exists bookings_provider_event_id_key
  on public.bookings(provider, external_event_id)
  where external_event_id is not null;

create unique index if not exists bookings_provider_visit_id_key
  on public.bookings(provider, external_visit_id)
  where external_visit_id is not null;

create index if not exists booking_connections_client_status_idx
  on public.booking_connections(client_id, status);

create index if not exists booking_services_client_enabled_idx
  on public.booking_services(client_id, enabled);

create index if not exists booking_slot_offers_expiry_idx
  on public.booking_slot_offers(status, expires_at);

create index if not exists bookings_client_created_at_idx
  on public.bookings(client_id, created_at desc);

alter table public.booking_connections enable row level security;
alter table public.booking_configs enable row level security;
alter table public.booking_services enable row level security;
alter table public.booking_slot_offers enable row level security;
alter table public.bookings enable row level security;
alter table public.booking_setup_tokens enable row level security;
alter table public.booking_oauth_states enable row level security;

revoke all on table public.booking_connections from anon, authenticated;
revoke all on table public.booking_configs from anon, authenticated;
revoke all on table public.booking_services from anon, authenticated;
revoke all on table public.booking_slot_offers from anon, authenticated;
revoke all on table public.bookings from anon, authenticated;
revoke all on table public.booking_setup_tokens from anon, authenticated;
revoke all on table public.booking_oauth_states from anon, authenticated;

grant select, insert, update, delete on table public.booking_connections to service_role;
grant select, insert, update, delete on table public.booking_configs to service_role;
grant select, insert, update, delete on table public.booking_services to service_role;
grant select, insert, update, delete on table public.booking_slot_offers to service_role;
grant select, insert, update, delete on table public.bookings to service_role;
grant select, insert, update, delete on table public.booking_setup_tokens to service_role;
grant select, insert, update, delete on table public.booking_oauth_states to service_role;
