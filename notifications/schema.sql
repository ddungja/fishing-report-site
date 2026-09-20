-- Supabase/PostgreSQL schema for Kakao fishing-report notifications
create extension if not exists pgcrypto;

create table if not exists notify_users (
  id uuid primary key default gen_random_uuid(),
  kakao_user_id text unique not null,
  nickname text,
  channel_friend boolean not null default false,
  notification_consent boolean not null default false,
  marketing_consent boolean not null default false,
  active boolean not null default true,
  last_digest_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists notify_boats (
  id text primary key,
  name text not null,
  active boolean not null default true
);

create table if not exists notify_subscriptions (
  user_id uuid not null references notify_users(id) on delete cascade,
  boat_id text not null references notify_boats(id) on delete cascade,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  primary key (user_id, boat_id)
);

create table if not exists notify_reports (
  id bigint generated always as identity primary key,
  boat_id text not null references notify_boats(id) on delete cascade,
  report_key text not null,
  title text not null,
  report_date date,
  source_url text,
  discovered_at timestamptz not null default now(),
  unique (boat_id, report_key)
);

create table if not exists notify_delivery_log (
  id bigint generated always as identity primary key,
  user_id uuid not null references notify_users(id) on delete cascade,
  digest_bucket timestamptz not null,
  report_count integer not null default 0,
  status text not null,
  kakao_request_id text,
  error_message text,
  created_at timestamptz not null default now(),
  unique (user_id, digest_bucket)
);

insert into notify_boats (id, name) values
  ('spaceho', '안흥 스페이스호'),
  ('ddoongs', '군산 뚱스호')
on conflict (id) do update set name = excluded.name;
