-- Retreats: one row per retreat, the retreat itself as a JSON document.
create table if not exists public.retreats (
  id uuid primary key,
  user_id uuid not null references auth.users on delete cascade,
  title text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  data jsonb not null
);
create index if not exists retreats_user_idx on public.retreats (user_id, created_at desc);
alter table public.retreats enable row level security;  -- no policies: only the backend's secret key can use it
