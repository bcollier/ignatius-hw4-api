-- Every model call: who, what for, the prompt, the response, tokens, cost, timing.
create table if not exists public.llm_calls (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  user_id uuid references auth.users on delete set null,
  email text,                         -- email at the time of the call, or 'guest'
  retreat_id uuid references public.retreats on delete set null,
  day int,
  purpose text not null,              -- plan | heart | deep | search_queries
  provider text not null,             -- openrouter | anthropic | jetstream
  model text not null,
  request jsonb not null,             -- {system, messages}; images replaced by {type, media_type, bytes}
  response_text text,
  response jsonb not null default '{}'::jsonb,  -- stop reason, web search queries, reasoning (Jetstream)
  input_tokens int not null default 0,
  output_tokens int not null default 0,
  web_searches int not null default 0,
  usd numeric(12, 6) not null default 0,
  duration_ms int not null default 0,
  status text not null check (status in ('ok', 'error')),
  error text
);
create index if not exists llm_calls_user_idx on public.llm_calls (user_id, created_at desc);
create index if not exists llm_calls_retreat_idx on public.llm_calls (retreat_id);
create index if not exists llm_calls_created_idx on public.llm_calls (created_at desc);
alter table public.llm_calls enable row level security;  -- no policies: only the backend writes, you read in the dashboard
revoke all on public.llm_calls from anon, authenticated;

-- One line per user: calls, tokens, cost, last call.
create or replace view public.llm_usage_by_user with (security_invoker = true) as
select
  coalesce(email, user_id::text, 'unknown') as who,
  count(*)                                  as calls,
  count(*) filter (where status = 'error')  as errors,
  sum(input_tokens)                         as input_tokens,
  sum(output_tokens)                        as output_tokens,
  sum(web_searches)                         as web_searches,
  round(sum(usd), 4)                        as usd,
  max(created_at)                           as last_call
from public.llm_calls
group by 1
order by last_call desc;
revoke all on public.llm_usage_by_user from anon, authenticated;
