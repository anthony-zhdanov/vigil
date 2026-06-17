create table if not exists public.client_phone_numbers (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients(id) on delete cascade,
  phone_number text not null,
  phone_type text not null default 'twilio_aux',
  label text,
  voice_enabled boolean not null default true,
  sms_enabled boolean not null default true,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (phone_number)
);

insert into public.client_phone_numbers (
  client_id,
  phone_number,
  phone_type,
  label,
  voice_enabled,
  sms_enabled,
  active
)
select
  clients.id,
  clients.twilio_phone,
  'twilio_aux',
  'Default Twilio recovery number',
  true,
  true,
  true
from public.clients
where clients.twilio_phone is not null
  and btrim(clients.twilio_phone) <> ''
on conflict (phone_number) do update
set
  client_id = excluded.client_id,
  phone_type = excluded.phone_type,
  label = coalesce(public.client_phone_numbers.label, excluded.label),
  voice_enabled = excluded.voice_enabled,
  sms_enabled = excluded.sms_enabled,
  active = excluded.active,
  updated_at = now();

create index if not exists client_phone_numbers_client_id_idx
  on public.client_phone_numbers(client_id);

create index if not exists client_phone_numbers_active_phone_idx
  on public.client_phone_numbers(phone_number)
  where active = true;

alter table public.client_phone_numbers enable row level security;
