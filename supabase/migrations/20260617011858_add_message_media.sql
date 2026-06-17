create table if not exists public.message_media (
  id uuid primary key default gen_random_uuid(),
  message_id uuid not null references public.messages(id) on delete cascade,
  client_id uuid not null references public.clients(id) on delete cascade,
  lead_id uuid not null references public.leads(id) on delete cascade,
  twilio_media_url text not null,
  content_type text,
  storage_url text,
  created_at timestamptz not null default now()
);

create unique index if not exists message_media_message_url_key
  on public.message_media(message_id, twilio_media_url);

create index if not exists message_media_message_id_idx
  on public.message_media(message_id);

create index if not exists message_media_client_lead_idx
  on public.message_media(client_id, lead_id, created_at desc);

alter table public.message_media enable row level security;

