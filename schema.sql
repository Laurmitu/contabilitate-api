create table if not exists companies (
  id bigserial primary key,
  name text not null,
  cui text not null unique,
  invoice_series text not null,
  created_at timestamptz not null default now()
);

create table if not exists customers (
  id bigserial primary key,
  company_id bigint not null references companies(id) on delete cascade,
  name text not null,
  cui text,
  address text,
  created_at timestamptz not null default now()
);

create table if not exists invoices (
  id bigserial primary key,
  company_id bigint not null references companies(id) on delete cascade,
  number bigint not null,
  series text not null,
  issue_date date not null default current_date,
  customer_id bigint references customers(id),
  currency text not null default 'RON',
  total numeric(12,2) not null default 0,
  created_at timestamptz not null default now(),
  unique(company_id, series, number)
);

create table if not exists invoice_lines (
  id bigserial primary key,
  invoice_id bigint not null references invoices(id) on delete cascade,
  name text not null,
  qty numeric(12,3) not null default 1,
  unit text default 'buc',
  unit_price numeric(12,2) not null default 0,
  vat_rate numeric(5,2) not null default 19,
  line_total numeric(12,2) not null default 0
);
