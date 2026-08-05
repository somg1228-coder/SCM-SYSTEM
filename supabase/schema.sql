create extension if not exists pgcrypto;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table if not exists public.suppliers (
  id uuid primary key default gen_random_uuid(),
  supplier_code text not null default '',
  name text not null unique,
  business_number text not null default '',
  contact_name text not null default '',
  phone text not null default '',
  email text not null default '',
  memo text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.inventory_items (
  id uuid primary key default gen_random_uuid(),
  source_type text not null,
  sku text not null,
  barcode text not null default '',
  product_name text not null,
  category text not null default '',
  medium_category text not null default '',
  small_category text not null default '',
  brand text not null default '',
  supplier_id uuid references public.suppliers(id) on delete set null,
  supplier_name text not null default '',
  pack_qty integer not null default 0 check (pack_qty >= 0),
  box_qty integer not null default 0 check (box_qty >= 0),
  default_lead_time integer not null default 0 check (default_lead_time >= 0),
  min_stock integer not null default 0 check (min_stock >= 0),
  initial_stock integer not null default 0,
  sort_order integer not null default 0,
  is_active boolean not null default true,
  memo text not null default '',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ck_inventory_items_source_type check (source_type in ('3PL', '오프라인', '창고')),
  constraint uq_inventory_items_source_sku unique (source_type, sku),
  constraint uq_inventory_items_source_barcode_name unique (source_type, barcode, product_name)
);

create table if not exists public.warehouse_locations (
  id uuid primary key default gen_random_uuid(),
  location_code text not null unique,
  name text not null,
  building text not null default '',
  floor text not null default '',
  zone text not null default '',
  rack text not null default '',
  bin text not null default '',
  memo text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.warehouse_layouts (
  id uuid primary key default gen_random_uuid(),
  building text not null,
  floor text not null,
  layout_data jsonb not null default '{}'::jsonb,
  is_active boolean not null default true,
  created_by text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint uq_warehouse_layouts_building_floor unique (building, floor),
  constraint ck_warehouse_layouts_layout_object check (jsonb_typeof(layout_data) = 'object')
);

create table if not exists public.import_batches (
  id uuid primary key default gen_random_uuid(),
  batch_type text not null,
  source_type text not null default '',
  file_name text not null default '',
  total_rows integer not null default 0 check (total_rows >= 0),
  matched_rows integer not null default 0 check (matched_rows >= 0),
  failed_rows integer not null default 0 check (failed_rows >= 0),
  status text not null default 'PENDING',
  preview_json jsonb not null default '{}'::jsonb,
  created_by text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.inventory_transactions (
  id uuid primary key default gen_random_uuid(),
  item_id uuid not null references public.inventory_items(id) on delete restrict,
  supplier_id uuid references public.suppliers(id) on delete set null,
  location_id uuid references public.warehouse_locations(id) on delete set null,
  import_batch_id uuid references public.import_batches(id) on delete set null,
  source_type text not null,
  transaction_type text not null,
  quantity integer not null check (quantity > 0),
  transaction_date date not null default current_date,
  reference_no text not null default '',
  source_key text unique,
  note text not null default '',
  created_by text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ck_inventory_transactions_source_type check (source_type in ('3PL', '오프라인', '창고')),
  constraint ck_inventory_transactions_type check (
    transaction_type in ('IN', 'OUT', 'INITIAL', 'ADJUST_PLUS', 'ADJUST_MINUS')
  )
);

create table if not exists public.stock_counts (
  id uuid primary key default gen_random_uuid(),
  item_id uuid not null references public.inventory_items(id) on delete restrict,
  location_id uuid references public.warehouse_locations(id) on delete set null,
  import_batch_id uuid references public.import_batches(id) on delete set null,
  source_type text not null,
  count_date date not null default current_date,
  counted_qty integer not null default 0 check (counted_qty >= 0),
  available_qty integer not null default 0 check (available_qty >= 0),
  note text not null default '',
  created_by text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ck_stock_counts_source_type check (source_type in ('3PL', '오프라인', '창고')),
  constraint uq_stock_counts_item_date_batch unique (item_id, count_date, import_batch_id)
);

create table if not exists public.audit_logs (
  id uuid primary key default gen_random_uuid(),
  action text not null,
  source_type text not null default '',
  table_name text not null default '',
  record_id uuid,
  details jsonb not null default '{}'::jsonb,
  created_by text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_inventory_items_source_active on public.inventory_items(source_type, is_active);
create index if not exists idx_inventory_items_barcode on public.inventory_items(barcode);
create index if not exists idx_inventory_items_product_name on public.inventory_items(product_name);
create index if not exists idx_warehouse_layouts_building_floor on public.warehouse_layouts(building, floor);
create index if not exists idx_inventory_transactions_item_date on public.inventory_transactions(item_id, transaction_date);
create index if not exists idx_inventory_transactions_source_date on public.inventory_transactions(source_type, transaction_date);
create index if not exists idx_inventory_transactions_type on public.inventory_transactions(transaction_type);
create index if not exists idx_stock_counts_source_date on public.stock_counts(source_type, count_date);
create index if not exists idx_import_batches_source_created on public.import_batches(source_type, created_at desc);
create index if not exists idx_audit_logs_created on public.audit_logs(created_at desc);

drop trigger if exists trg_suppliers_updated_at on public.suppliers;
create trigger trg_suppliers_updated_at before update on public.suppliers for each row execute function public.set_updated_at();
drop trigger if exists trg_inventory_items_updated_at on public.inventory_items;
create trigger trg_inventory_items_updated_at before update on public.inventory_items for each row execute function public.set_updated_at();
drop trigger if exists trg_warehouse_locations_updated_at on public.warehouse_locations;
create trigger trg_warehouse_locations_updated_at before update on public.warehouse_locations for each row execute function public.set_updated_at();
drop trigger if exists trg_warehouse_layouts_updated_at on public.warehouse_layouts;
create trigger trg_warehouse_layouts_updated_at before update on public.warehouse_layouts for each row execute function public.set_updated_at();
drop trigger if exists trg_inventory_transactions_updated_at on public.inventory_transactions;
create trigger trg_inventory_transactions_updated_at before update on public.inventory_transactions for each row execute function public.set_updated_at();
drop trigger if exists trg_stock_counts_updated_at on public.stock_counts;
create trigger trg_stock_counts_updated_at before update on public.stock_counts for each row execute function public.set_updated_at();
drop trigger if exists trg_import_batches_updated_at on public.import_batches;
create trigger trg_import_batches_updated_at before update on public.import_batches for each row execute function public.set_updated_at();
drop trigger if exists trg_audit_logs_updated_at on public.audit_logs;
create trigger trg_audit_logs_updated_at before update on public.audit_logs for each row execute function public.set_updated_at();

create or replace view public.inventory_stock_summary
with (security_invoker = true) as
select
  i.id as item_id,
  i.source_type,
  i.sku,
  i.barcode,
  i.product_name,
  i.category,
  i.supplier_name,
  i.min_stock,
  (
    i.initial_stock
    + coalesce(sum(
      case
        when t.transaction_type in ('IN', 'INITIAL', 'ADJUST_PLUS') then t.quantity
        when t.transaction_type in ('OUT', 'ADJUST_MINUS') then -t.quantity
        else 0
      end
    ), 0)
  )::integer as current_stock,
  max(t.transaction_date) as last_transaction_date
from public.inventory_items i
left join public.inventory_transactions t on t.item_id = i.id
group by i.id;

alter table public.suppliers enable row level security;
alter table public.inventory_items enable row level security;
alter table public.warehouse_locations enable row level security;
alter table public.warehouse_layouts enable row level security;
alter table public.inventory_transactions enable row level security;
alter table public.stock_counts enable row level security;
alter table public.audit_logs enable row level security;
alter table public.import_batches enable row level security;

drop policy if exists "dev_select_suppliers" on public.suppliers;
create policy "dev_select_suppliers" on public.suppliers for select to anon, authenticated using (true);
drop policy if exists "dev_insert_suppliers" on public.suppliers;
create policy "dev_insert_suppliers" on public.suppliers for insert to anon, authenticated with check (true);
drop policy if exists "dev_update_suppliers" on public.suppliers;
create policy "dev_update_suppliers" on public.suppliers for update to anon, authenticated using (true) with check (true);

drop policy if exists "dev_select_inventory_items" on public.inventory_items;
create policy "dev_select_inventory_items" on public.inventory_items for select to anon, authenticated using (true);
drop policy if exists "dev_insert_inventory_items" on public.inventory_items;
create policy "dev_insert_inventory_items" on public.inventory_items for insert to anon, authenticated with check (true);
drop policy if exists "dev_update_inventory_items" on public.inventory_items;
create policy "dev_update_inventory_items" on public.inventory_items for update to anon, authenticated using (true) with check (true);

drop policy if exists "dev_select_warehouse_locations" on public.warehouse_locations;
create policy "dev_select_warehouse_locations" on public.warehouse_locations for select to anon, authenticated using (true);
drop policy if exists "dev_insert_warehouse_locations" on public.warehouse_locations;
create policy "dev_insert_warehouse_locations" on public.warehouse_locations for insert to anon, authenticated with check (true);
drop policy if exists "dev_update_warehouse_locations" on public.warehouse_locations;
create policy "dev_update_warehouse_locations" on public.warehouse_locations for update to anon, authenticated using (true) with check (true);

drop policy if exists "dev_select_warehouse_layouts" on public.warehouse_layouts;
create policy "dev_select_warehouse_layouts" on public.warehouse_layouts for select to anon, authenticated using (true);
drop policy if exists "dev_insert_warehouse_layouts" on public.warehouse_layouts;
create policy "dev_insert_warehouse_layouts" on public.warehouse_layouts for insert to anon, authenticated with check (true);
drop policy if exists "dev_update_warehouse_layouts" on public.warehouse_layouts;
create policy "dev_update_warehouse_layouts" on public.warehouse_layouts for update to anon, authenticated using (true) with check (true);

drop policy if exists "dev_select_inventory_transactions" on public.inventory_transactions;
create policy "dev_select_inventory_transactions" on public.inventory_transactions for select to anon, authenticated using (true);
drop policy if exists "dev_insert_inventory_transactions" on public.inventory_transactions;
create policy "dev_insert_inventory_transactions" on public.inventory_transactions for insert to anon, authenticated with check (true);
drop policy if exists "dev_update_inventory_transactions" on public.inventory_transactions;
create policy "dev_update_inventory_transactions" on public.inventory_transactions for update to anon, authenticated using (true) with check (true);

drop policy if exists "dev_select_stock_counts" on public.stock_counts;
create policy "dev_select_stock_counts" on public.stock_counts for select to anon, authenticated using (true);
drop policy if exists "dev_insert_stock_counts" on public.stock_counts;
create policy "dev_insert_stock_counts" on public.stock_counts for insert to anon, authenticated with check (true);
drop policy if exists "dev_update_stock_counts" on public.stock_counts;
create policy "dev_update_stock_counts" on public.stock_counts for update to anon, authenticated using (true) with check (true);

drop policy if exists "dev_select_audit_logs" on public.audit_logs;
create policy "dev_select_audit_logs" on public.audit_logs for select to anon, authenticated using (true);
drop policy if exists "dev_insert_audit_logs" on public.audit_logs;
create policy "dev_insert_audit_logs" on public.audit_logs for insert to anon, authenticated with check (true);
drop policy if exists "dev_update_audit_logs" on public.audit_logs;
create policy "dev_update_audit_logs" on public.audit_logs for update to anon, authenticated using (true) with check (true);

drop policy if exists "dev_select_import_batches" on public.import_batches;
create policy "dev_select_import_batches" on public.import_batches for select to anon, authenticated using (true);
drop policy if exists "dev_insert_import_batches" on public.import_batches;
create policy "dev_insert_import_batches" on public.import_batches for insert to anon, authenticated with check (true);
drop policy if exists "dev_update_import_batches" on public.import_batches;
create policy "dev_update_import_batches" on public.import_batches for update to anon, authenticated using (true) with check (true);
