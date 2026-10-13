-- Vanta Friends: Supabase schema. Paste into Supabase -> SQL Editor -> Run (safe to re-run).
-- Everything is protected by row-level security. Clients only hold the public anon key and
-- the signed-in user's own session; writes that touch two users go through the
-- security-definer functions below, which check auth.uid() themselves.

-- One row per Vanta user (created after their email is verified).
create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  username text not null check (username ~ '^[A-Za-z0-9_]{3,16}$'),
  invite_code text not null unique default upper(substr(md5(gen_random_uuid()::text), 1, 8)),
  created_at timestamptz not null default now()
);
create unique index if not exists profiles_username_ci on public.profiles (lower(username));

create table if not exists public.friend_requests (
  id bigint generated always as identity primary key,
  from_id uuid not null references public.profiles (id) on delete cascade,
  to_id uuid not null references public.profiles (id) on delete cascade,
  status text not null default 'pending' check (status in ('pending', 'accepted', 'declined')),
  created_at timestamptz not null default now(),
  responded_at timestamptz,
  check (from_id <> to_id)
);
create unique index if not exists friend_requests_pending_pair
  on public.friend_requests (least(from_id, to_id), greatest(from_id, to_id)) where status = 'pending';

-- Symmetric: one row per direction, so "my friends" is a simple filter.
create table if not exists public.friendships (
  user_id uuid not null references public.profiles (id) on delete cascade,
  friend_id uuid not null references public.profiles (id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, friend_id),
  check (user_id <> friend_id)
);

-- Heartbeat presence (the launcher upserts every ~30 s). Online = updated in the last 90 s.
create table if not exists public.presence (
  user_id uuid primary key references public.profiles (id) on delete cascade,
  status text not null default 'online' check (status in ('online', 'playing', 'away', 'offline')),
  playing text check (char_length(playing) <= 80),          -- e.g. 'Minecraft 1.21.11 (Fabric)'
  game_version text check (char_length(game_version) <= 20),
  join_address text check (join_address is null or join_address ~ '^[A-Za-z0-9.-]{3,253}(:[0-9]{1,5})?$'),
  updated_at timestamptz not null default now()
);

alter table public.profiles enable row level security;
alter table public.friend_requests enable row level security;
alter table public.friendships enable row level security;
alter table public.presence enable row level security;

create or replace function public.is_friend(other uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from friendships where user_id = auth.uid() and friend_id = other)
$$;

-- profiles: you see yourself and your friends; you can create/update only your own row.
drop policy if exists profiles_select on public.profiles;
create policy profiles_select on public.profiles for select to authenticated
  using (id = auth.uid() or public.is_friend(id)
         or exists (select 1 from friend_requests r where r.status = 'pending'
                    and ((r.from_id = auth.uid() and r.to_id = profiles.id) or (r.to_id = auth.uid() and r.from_id = profiles.id))));
drop policy if exists profiles_insert on public.profiles;
create policy profiles_insert on public.profiles for insert to authenticated with check (id = auth.uid());
drop policy if exists profiles_update on public.profiles;
create policy profiles_update on public.profiles for update to authenticated using (id = auth.uid()) with check (id = auth.uid());

-- requests: visible to both sides; created/answered only through the functions below.
drop policy if exists requests_select on public.friend_requests;
create policy requests_select on public.friend_requests for select to authenticated
  using (from_id = auth.uid() or to_id = auth.uid());

-- friendships: read your own; delete either direction of your own (remove friend).
drop policy if exists friendships_select on public.friendships;
create policy friendships_select on public.friendships for select to authenticated using (user_id = auth.uid());

-- presence: write only your own row; read yourself and friends.
drop policy if exists presence_select on public.presence;
create policy presence_select on public.presence for select to authenticated
  using (user_id = auth.uid() or public.is_friend(user_id));
drop policy if exists presence_upsert on public.presence;
create policy presence_upsert on public.presence for insert to authenticated with check (user_id = auth.uid());
drop policy if exists presence_update on public.presence;
create policy presence_update on public.presence for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());

-- Claim a username after email verification.
create or replace function public.claim_username(p_username text) returns public.profiles
language plpgsql security definer set search_path = public as $$
declare me uuid := auth.uid(); row profiles;
begin
  if me is null then raise exception 'not signed in'; end if;
  if not exists (select 1 from auth.users where id = me and email_confirmed_at is not null) then
    raise exception 'verify your email first';
  end if;
  insert into profiles (id, username) values (me, p_username)
    on conflict (id) do update set username = excluded.username
    returning * into row;
  return row;
end $$;

-- Send a request by username or invite code. Auto-accepts if they already asked you.
create or replace function public.send_friend_request(p_target text) returns text
language plpgsql security definer set search_path = public as $$
declare me uuid := auth.uid(); other uuid; existing bigint;
begin
  if me is null or not exists (select 1 from profiles where id = me) then raise exception 'claim a username first'; end if;
  select id into other from profiles where lower(username) = lower(p_target) or invite_code = upper(p_target);
  if other is null then raise exception 'no Vanta user with that name or code'; end if;
  if other = me then raise exception 'that is you'; end if;
  if exists (select 1 from friendships where user_id = me and friend_id = other) then return 'already friends'; end if;
  select id into existing from friend_requests where from_id = other and to_id = me and status = 'pending';
  if existing is not null then perform respond_friend_request(existing, true); return 'accepted'; end if;
  insert into friend_requests (from_id, to_id) values (me, other) on conflict do nothing;
  return 'sent';
end $$;

create or replace function public.respond_friend_request(p_request bigint, p_accept boolean) returns text
language plpgsql security definer set search_path = public as $$
declare me uuid := auth.uid(); req friend_requests;
begin
  select * into req from friend_requests where id = p_request and to_id = me and status = 'pending' for update;
  if req.id is null then raise exception 'no such pending request'; end if;
  update friend_requests set status = case when p_accept then 'accepted' else 'declined' end, responded_at = now()
    where id = req.id;
  if p_accept then
    insert into friendships (user_id, friend_id) values (req.from_id, req.to_id), (req.to_id, req.from_id)
      on conflict do nothing;
    return 'accepted';
  end if;
  return 'declined';
end $$;

create or replace function public.remove_friend(p_friend uuid) returns text
language plpgsql security definer set search_path = public as $$
begin
  delete from friendships where (user_id = auth.uid() and friend_id = p_friend) or (user_id = p_friend and friend_id = auth.uid());
  return 'removed';
end $$;

-- Friend list with presence in one call.
create or replace function public.my_friends() returns table (
  id uuid, username text, status text, playing text, game_version text, join_address text, updated_at timestamptz, online boolean)
language sql stable security definer set search_path = public as $$
  select p.id, p.username,
         coalesce(case when pr.updated_at > now() - interval '90 seconds' then pr.status end, 'offline'),
         case when pr.updated_at > now() - interval '90 seconds' then pr.playing end,
         case when pr.updated_at > now() - interval '90 seconds' then pr.game_version end,
         case when pr.updated_at > now() - interval '90 seconds' then pr.join_address end,
         pr.updated_at,
         coalesce(pr.updated_at > now() - interval '90 seconds', false)
  from friendships f join profiles p on p.id = f.friend_id left join presence pr on pr.user_id = p.id
  where f.user_id = auth.uid()
  order by 8 desc, p.username
$$;

create or replace function public.my_requests() returns table (
  id bigint, direction text, username text, created_at timestamptz)
language sql stable security definer set search_path = public as $$
  select r.id, case when r.to_id = auth.uid() then 'incoming' else 'outgoing' end,
         p.username, r.created_at
  from friend_requests r join profiles p on p.id = case when r.to_id = auth.uid() then r.from_id else r.to_id end
  where r.status = 'pending' and (r.to_id = auth.uid() or r.from_id = auth.uid())
  order by r.created_at desc
$$;

revoke all on function public.claim_username(text), public.send_friend_request(text),
  public.respond_friend_request(bigint, boolean), public.remove_friend(uuid), public.my_friends(), public.my_requests()
  from public, anon;
grant execute on function public.claim_username(text), public.send_friend_request(text),
  public.respond_friend_request(bigint, boolean), public.remove_friend(uuid), public.my_friends(), public.my_requests(),
  public.is_friend(uuid) to authenticated;

-- Optional: live updates for presence (clients that can open a websocket).
do $$ begin
  alter publication supabase_realtime add table public.presence;
exception when others then null; end $$;
