create extension if not exists vector;

drop table if exists provenance cascade;
drop table if exists audit_rejects cascade;
drop table if exists signals cascade;
drop table if exists principals cascade;
drop table if exists firms cascade;
drop table if exists candidates cascade;

-- STAGE 1 OF PIPELINE: raw discovery output. Nothing here is proven yet.
create table candidates (
  candidate_id   bigserial primary key,
  source_class   text not null,          -- irs_990pf | job_posting | press
  raw_name       text not null,
  surname        text,                   -- extracted family surname, if any
  city           text,
  state          text,
  street         text,
  ein            text,
  assets_usd     numeric,
  assets_year    int,
  source_url     text,
  raw_payload    jsonb,
  discovered_at  timestamptz default now()
);

-- STAGE 2: firms that survived classification
create table firms (
  firm_id            bigserial primary key,
  candidate_id       bigint references candidates(candidate_id),
  legal_name         text not null,
  fo_type            text not null default 'undetermined',   -- single_family | multi_family | undetermined
  fo_type_evidence   text,
  fo_type_confidence numeric(3,2),
  hq_city            text,
  hq_state           text,
  hq_country         text default 'US',
  aum_usd            numeric,
  aum_asof           date,
  aum_basis          text,
  website            text,
  linkedin_url       text,
  investing_thesis   text,
  asset_classes      text[],
  background         text,
  inclusion_status   text not null default 'rejected_type_unproven',
  inclusion_reason   text,
  discovery_source_class text not null,
  discovery_source_url   text,
  created_at         timestamptz default now()
);

create table principals (
  principal_id      bigserial primary key,
  firm_id           bigint references firms(firm_id) on delete cascade,
  full_name         text not null,
  title             text,
  linkedin_url      text,
  work_email        text,               -- NULL if verification failed
  email_status      text,               -- verified | risky | undeliverable | not_found
  email_verify_method text,
  email_verified_at timestamptz,
  direct_phone      text,
  phone_status      text,
  source_url        text
);

create table signals (
  signal_id    bigserial primary key,
  firm_id      bigint references firms(firm_id) on delete cascade,
  signal_type  text,                    -- investment | commitment | hire | news
  description  text,
  signal_date  date not null,
  source_url   text not null,
  retrieved_at timestamptz default now()
);

-- RULE ONE: every value carries its basis
create table provenance (
  prov_id             bigserial primary key,
  entity_type         text not null,     -- firms | principals | signals
  entity_id           bigint not null,
  field_name          text not null,
  source_url          text,
  src_class           text not null,
  extraction_method   text not null,     -- api | parser | llm_extract | manual_check
  verification_method text,
  verification_result text,              -- confirmed | contradicted | unverified
  confidence          numeric(3,2),
  checked_at          timestamptz default now()
);

-- Rejected values live here, NOT in customer-facing fields
create table audit_rejects (
  reject_id      bigserial primary key,
  entity_type    text,
  entity_id      bigint,
  field_name     text,
  rejected_value text,
  reject_reason  text,
  rejected_at    timestamptz default now()
);

-- NOTE: a UNIQUE constraint cannot contain a function like coalesce(),
-- so it must be declared as a unique INDEX instead. This index is what
-- makes `on conflict do nothing` work in the insert.
create unique index candidates_uniq
  on candidates (source_class, raw_name, coalesce(ein,''));

create index on candidates (surname);
create index on candidates (source_class);
create index on firms (fo_type);
create index on firms (inclusion_status);

-- Dedupes re-runs: classify INSERT uses on conflict do nothing against this.
create unique index firms_uniq
  on firms (lower(legal_name), coalesce(hq_state, ''));

-- provenance uses polymorphic (entity_type, entity_id) — no FK to firms,
-- because the same table also holds rows for principals and signals.
-- Deleting from firms does NOT auto-clean provenance. After manual firm
-- deletes, run:
--   delete from provenance p
--   where p.entity_type = 'firms'
--     and not exists (select 1 from firms f where f.firm_id = p.entity_id);

create index on provenance (entity_type, entity_id);