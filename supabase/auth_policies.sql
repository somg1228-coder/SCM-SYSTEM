-- Run this later when Supabase Auth is introduced.
-- These examples assume an organization/user ownership model that does not exist yet.
-- Do not run until user_id / org_id columns are added and backfilled.

-- Example shape:
-- alter table public.inventory_items add column if not exists user_id uuid references auth.users(id);
-- create policy "auth_select_own_inventory_items"
-- on public.inventory_items for select
-- to authenticated
-- using (auth.uid() = user_id);

