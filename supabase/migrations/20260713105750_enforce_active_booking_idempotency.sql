drop index if exists public.bookings_one_confirmed_per_conversation_key;

create unique index if not exists bookings_one_active_per_conversation_key
  on public.bookings(conversation_id)
  where status in ('creating', 'confirmed', 'unknown');
