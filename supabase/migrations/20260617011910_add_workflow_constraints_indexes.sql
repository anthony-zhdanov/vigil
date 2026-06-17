create unique index if not exists leads_client_id_phone_number_key
  on public.leads(client_id, phone_number);

create index if not exists leads_client_updated_at_idx
  on public.leads(client_id, updated_at desc);

create unique index if not exists messages_twilio_message_sid_key
  on public.messages(twilio_message_sid)
  where twilio_message_sid is not null;

create index if not exists messages_client_lead_created_at_idx
  on public.messages(client_id, lead_id, created_at desc);

create index if not exists messages_conversation_created_at_idx
  on public.messages(conversation_id, created_at desc)
  where conversation_id is not null;

create unique index if not exists conversations_one_active_per_lead_channel_key
  on public.conversations(client_id, lead_id, channel)
  where status <> 'closed' and closed_at is null;

create index if not exists conversations_client_lead_channel_idx
  on public.conversations(client_id, lead_id, channel, last_message_at desc);

create unique index if not exists opt_outs_client_id_phone_number_key
  on public.opt_outs(client_id, phone_number);

create index if not exists opt_outs_client_created_at_idx
  on public.opt_outs(client_id, created_at desc);

