alter table public.conversations
  drop constraint if exists conversations_client_id_lead_id_channel_key;

drop index if exists public.conversations_client_id_lead_id_channel_key;

create unique index if not exists conversations_one_active_per_lead_channel_key
  on public.conversations(client_id, lead_id, channel)
  where status <> 'closed' and closed_at is null;
