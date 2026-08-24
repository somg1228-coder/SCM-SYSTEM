-- SCM Portal Supabase PostgreSQL schema generated from data/scm.db
-- Generated on demand by Codex. No secrets are stored in this file.
-- Run this in Supabase SQL Editor before migrating SQLite data.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

BEGIN;

CREATE TABLE IF NOT EXISTS "cases" (
    "id" BIGSERIAL PRIMARY KEY,
    "case_id" TEXT,
    "category" TEXT,
    "barcode" TEXT,
    "product" TEXT,
    "cause" TEXT,
    "action" TEXT,
    "repair_method" TEXT,
    "prevention" TEXT,
    "product_image" BYTEA,
    "case_image" BYTEA,
    "case_image_original" BYTEA,
    "repair_image" BYTEA,
    "repair_image_original" BYTEA
);
CREATE INDEX IF NOT EXISTS "ix_cases_product" ON "cases" ("product");
CREATE INDEX IF NOT EXISTS "ix_cases_category" ON "cases" ("category");
CREATE INDEX IF NOT EXISTS "ix_cases_case_id" ON "cases" ("case_id");
CREATE INDEX IF NOT EXISTS "ix_cases_barcode" ON "cases" ("barcode");
SELECT setval(pg_get_serial_sequence('cases', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "cases"), 1), 1), (SELECT COUNT(*) FROM "cases") > 0);

CREATE TABLE IF NOT EXISTS "category_bom_items" (
    "id" BIGSERIAL PRIMARY KEY,
    "category_name" VARCHAR(160) NOT NULL,
    "sort_order" INTEGER NOT NULL,
    "item_name" VARCHAR(255) NOT NULL,
    "item_type" VARCHAR(40) NOT NULL,
    "manager" VARCHAR(120) NOT NULL,
    "vendor" VARCHAR(160) NOT NULL,
    "required_stock" INTEGER NOT NULL,
    "barcode_spec" VARCHAR(160) NOT NULL,
    "memo" VARCHAR(500) NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "barcode" VARCHAR(120) DEFAULT '' NOT NULL,
    "spec" VARCHAR(160) DEFAULT '' NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_category_bom_items_item_name" ON "category_bom_items" ("item_name");
CREATE INDEX IF NOT EXISTS "ix_category_bom_items_category_name" ON "category_bom_items" ("category_name");
SELECT setval(pg_get_serial_sequence('category_bom_items', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "category_bom_items"), 1), 1), (SELECT COUNT(*) FROM "category_bom_items") > 0);

CREATE TABLE IF NOT EXISTS "inventory_daily" (
    "id" BIGSERIAL PRIMARY KEY,
    "source_type" VARCHAR(20) NOT NULL,
    "work_date" DATE NOT NULL,
    "category" VARCHAR(120) NOT NULL,
    "product_name" VARCHAR(255) NOT NULL,
    "barcode" VARCHAR(120) NOT NULL,
    "current_stock" INTEGER NOT NULL,
    "safe_stock" INTEGER NOT NULL,
    "stock_status" VARCHAR(40) NOT NULL,
    "outbound_qty" INTEGER NOT NULL,
    "previous_inbound_date" DATE,
    "last_inbound_date" DATE,
    "inbound_qty" INTEGER NOT NULL,
    "inbound_cycle" INTEGER,
    "memo" VARCHAR(500) NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "product_code" VARCHAR(120) DEFAULT '' NOT NULL,
    "available_stock" INTEGER DEFAULT 0 NOT NULL,
    "supplier" VARCHAR(160) DEFAULT '' NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_inventory_daily_source_type" ON "inventory_daily" ("source_type");
CREATE INDEX IF NOT EXISTS "ix_inventory_daily_product_name" ON "inventory_daily" ("product_name");
CREATE INDEX IF NOT EXISTS "ix_inventory_daily_work_date" ON "inventory_daily" ("work_date");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_inventory_daily_source_type_work_date_product_name_barcode" ON "inventory_daily" ("source_type", "work_date", "product_name", "barcode");
SELECT setval(pg_get_serial_sequence('inventory_daily', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "inventory_daily"), 1), 1), (SELECT COUNT(*) FROM "inventory_daily") > 0);

CREATE TABLE IF NOT EXISTS "inventory_inbound" (
    "id" BIGSERIAL PRIMARY KEY,
    "source_type" VARCHAR(20) NOT NULL,
    "inbound_date" DATE NOT NULL,
    "category" VARCHAR(120) NOT NULL,
    "product_name" VARCHAR(255) NOT NULL,
    "barcode" VARCHAR(120) NOT NULL,
    "inbound_qty" INTEGER NOT NULL,
    "vendor" VARCHAR(160) NOT NULL,
    "inbound_type" VARCHAR(80) NOT NULL,
    "memo" VARCHAR(500) NOT NULL,
    "is_applied" BOOLEAN NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "product_code" VARCHAR(120) DEFAULT '' NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_inventory_inbound_product_name" ON "inventory_inbound" ("product_name");
CREATE INDEX IF NOT EXISTS "ix_inventory_inbound_inbound_date" ON "inventory_inbound" ("inbound_date");
CREATE INDEX IF NOT EXISTS "ix_inventory_inbound_source_type" ON "inventory_inbound" ("source_type");
SELECT setval(pg_get_serial_sequence('inventory_inbound', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "inventory_inbound"), 1), 1), (SELECT COUNT(*) FROM "inventory_inbound") > 0);

CREATE TABLE IF NOT EXISTS "inventory_output_histories" (
    "id" BIGSERIAL PRIMARY KEY,
    "source_type" VARCHAR(20) NOT NULL,
    "work_date" DATE,
    "output_type" VARCHAR(20) NOT NULL,
    "created_by" VARCHAR(120) NOT NULL,
    "filter_json" TEXT NOT NULL,
    "item_count" INTEGER NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_inventory_output_histories_source_type" ON "inventory_output_histories" ("source_type");
SELECT setval(pg_get_serial_sequence('inventory_output_histories', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "inventory_output_histories"), 1), 1), (SELECT COUNT(*) FROM "inventory_output_histories") > 0);

CREATE TABLE IF NOT EXISTS "inventory_upload_histories" (
    "id" BIGSERIAL PRIMARY KEY,
    "source_type" VARCHAR(20) NOT NULL,
    "work_date" DATE NOT NULL,
    "file_name" VARCHAR(255) NOT NULL,
    "uploaded_by" VARCHAR(120) NOT NULL,
    "upload_mode" VARCHAR(20) NOT NULL,
    "total_rows" INTEGER NOT NULL,
    "matched_count" INTEGER NOT NULL,
    "failed_count" INTEGER NOT NULL,
    "duplicate_count" INTEGER NOT NULL,
    "zeroed_count" INTEGER NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_inventory_upload_histories_source_type" ON "inventory_upload_histories" ("source_type");
CREATE INDEX IF NOT EXISTS "ix_inventory_upload_histories_work_date" ON "inventory_upload_histories" ("work_date");
SELECT setval(pg_get_serial_sequence('inventory_upload_histories', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "inventory_upload_histories"), 1), 1), (SELECT COUNT(*) FROM "inventory_upload_histories") > 0);

CREATE TABLE IF NOT EXISTS "inventory_upload_snapshots" (
    "id" BIGSERIAL PRIMARY KEY,
    "upload_history_id" INTEGER NOT NULL,
    "source_type" VARCHAR(20) NOT NULL,
    "work_date" DATE NOT NULL,
    "product_code" VARCHAR(120) NOT NULL,
    "barcode" VARCHAR(120) NOT NULL,
    "product_name" VARCHAR(255) NOT NULL,
    "previous_stock" INTEGER NOT NULL,
    "new_stock" INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_inventory_upload_snapshots_source_type" ON "inventory_upload_snapshots" ("source_type");
CREATE INDEX IF NOT EXISTS "ix_inventory_upload_snapshots_work_date" ON "inventory_upload_snapshots" ("work_date");
CREATE INDEX IF NOT EXISTS "ix_inventory_upload_snapshots_upload_history_id" ON "inventory_upload_snapshots" ("upload_history_id");
SELECT setval(pg_get_serial_sequence('inventory_upload_snapshots', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "inventory_upload_snapshots"), 1), 1), (SELECT COUNT(*) FROM "inventory_upload_snapshots") > 0);

CREATE TABLE IF NOT EXISTS "material_inventory_items" (
    "id" BIGSERIAL PRIMARY KEY,
    "category" VARCHAR(120) NOT NULL,
    "item_type" VARCHAR(40) NOT NULL,
    "related_product" VARCHAR(255) NOT NULL,
    "item_code" VARCHAR(120) NOT NULL,
    "item_name" VARCHAR(255) NOT NULL,
    "spec" VARCHAR(255) NOT NULL,
    "unit" VARCHAR(40) NOT NULL,
    "current_stock" INTEGER NOT NULL,
    "safe_stock" INTEGER NOT NULL,
    "location" VARCHAR(160) NOT NULL,
    "supplier" VARCHAR(160) NOT NULL,
    "lead_time_days" INTEGER NOT NULL,
    "memo" VARCHAR(500) NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_material_inventory_items_item_type" ON "material_inventory_items" ("item_type");
CREATE INDEX IF NOT EXISTS "ix_material_inventory_items_related_product" ON "material_inventory_items" ("related_product");
CREATE INDEX IF NOT EXISTS "ix_material_inventory_items_item_name" ON "material_inventory_items" ("item_name");
CREATE INDEX IF NOT EXISTS "ix_material_inventory_items_category" ON "material_inventory_items" ("category");
CREATE INDEX IF NOT EXISTS "ix_material_inventory_items_item_code" ON "material_inventory_items" ("item_code");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_material_inventory_items_item_code_item_name_related_product" ON "material_inventory_items" ("item_code", "item_name", "related_product");
SELECT setval(pg_get_serial_sequence('material_inventory_items', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "material_inventory_items"), 1), 1), (SELECT COUNT(*) FROM "material_inventory_items") > 0);

CREATE TABLE IF NOT EXISTS "meeting_action_items" (
    "id" BIGSERIAL PRIMARY KEY,
    "report_id" INTEGER NOT NULL,
    "sort_order" INTEGER NOT NULL,
    "owner" TEXT NOT NULL,
    "content" TEXT NOT NULL,
    "quantity" INTEGER NOT NULL,
    "due_date" TEXT NOT NULL,
    "delivery_date" TEXT NOT NULL,
    "status" TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_meeting_action_items_report_id" ON "meeting_action_items" ("report_id");
SELECT setval(pg_get_serial_sequence('meeting_action_items', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "meeting_action_items"), 1), 1), (SELECT COUNT(*) FROM "meeting_action_items") > 0);

CREATE TABLE IF NOT EXISTS "meeting_events" (
    "id" BIGSERIAL PRIMARY KEY,
    "report_id" INTEGER NOT NULL,
    "sort_order" INTEGER NOT NULL,
    "event_name" TEXT NOT NULL,
    "period" TEXT NOT NULL,
    "affected_products" TEXT NOT NULL,
    "request_qty" INTEGER NOT NULL,
    "summary" TEXT NOT NULL,
    "owner" TEXT NOT NULL,
    "memo" TEXT NOT NULL,
    "event_month" VARCHAR(20) NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_meeting_events_report_id" ON "meeting_events" ("report_id");
CREATE INDEX IF NOT EXISTS "ix_meeting_events_event_month" ON "meeting_events" ("event_month");
SELECT setval(pg_get_serial_sequence('meeting_events', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "meeting_events"), 1), 1), (SELECT COUNT(*) FROM "meeting_events") > 0);

CREATE TABLE IF NOT EXISTS "meeting_meta" (
    "key" VARCHAR(120) NOT NULL,
    "value" TEXT NOT NULL,
    PRIMARY KEY ("key")
);

CREATE TABLE IF NOT EXISTS "meeting_production_requests" (
    "id" BIGSERIAL PRIMARY KEY,
    "report_id" INTEGER NOT NULL,
    "sort_order" INTEGER NOT NULL,
    "production_code" TEXT NOT NULL,
    "product_name" TEXT NOT NULL,
    "current_qty" INTEGER NOT NULL,
    "request_qty" INTEGER NOT NULL,
    "due_date" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "memo" TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_meeting_production_requests_report_id" ON "meeting_production_requests" ("report_id");
SELECT setval(pg_get_serial_sequence('meeting_production_requests', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "meeting_production_requests"), 1), 1), (SELECT COUNT(*) FROM "meeting_production_requests") > 0);

CREATE TABLE IF NOT EXISTS "meeting_reports" (
    "id" BIGSERIAL PRIMARY KEY,
    "meeting_date" VARCHAR(20) NOT NULL,
    "author" TEXT NOT NULL,
    "event_detail" TEXT NOT NULL,
    "issue_delay" TEXT NOT NULL,
    "issue_inventory" TEXT NOT NULL,
    "issue_special" TEXT NOT NULL,
    "created_at" VARCHAR(40) NOT NULL,
    "updated_at" VARCHAR(40) NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_meeting_reports_meeting_date" ON "meeting_reports" ("meeting_date");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_meeting_reports_meeting_date" ON "meeting_reports" ("meeting_date");
SELECT setval(pg_get_serial_sequence('meeting_reports', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "meeting_reports"), 1), 1), (SELECT COUNT(*) FROM "meeting_reports") > 0);

CREATE TABLE IF NOT EXISTS "offline_product_master" (
    "id" BIGSERIAL PRIMARY KEY,
    "sku" VARCHAR(120) NOT NULL,
    "barcode" VARCHAR(120) NOT NULL,
    "product_name" VARCHAR(255) NOT NULL,
    "large_category" VARCHAR(120) NOT NULL,
    "medium_category" VARCHAR(120) NOT NULL,
    "small_category" VARCHAR(120) NOT NULL,
    "brand" VARCHAR(120) NOT NULL,
    "supplier" VARCHAR(160) NOT NULL,
    "pack_qty" INTEGER NOT NULL,
    "box_qty" INTEGER NOT NULL,
    "default_lead_time" INTEGER NOT NULL,
    "min_stock" INTEGER NOT NULL,
    "is_active" VARCHAR(20) NOT NULL,
    "location_registered" BOOLEAN DEFAULT false NOT NULL,
    "memo" VARCHAR(500) NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "sort_order" INTEGER DEFAULT 0 NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_offline_product_master_product_name" ON "offline_product_master" ("product_name");
CREATE INDEX IF NOT EXISTS "ix_offline_product_master_barcode" ON "offline_product_master" ("barcode");
CREATE INDEX IF NOT EXISTS "ix_offline_product_master_sku" ON "offline_product_master" ("sku");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_offline_product_master_barcode_product_name" ON "offline_product_master" ("barcode", "product_name");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_offline_product_master_sku" ON "offline_product_master" ("sku");
SELECT setval(pg_get_serial_sequence('offline_product_master', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "offline_product_master"), 1), 1), (SELECT COUNT(*) FROM "offline_product_master") > 0);

CREATE TABLE IF NOT EXISTS "product_master" (
    "id" BIGSERIAL PRIMARY KEY,
    "sku" VARCHAR(120) NOT NULL,
    "barcode" VARCHAR(120) NOT NULL,
    "product_name" VARCHAR(255) NOT NULL,
    "large_category" VARCHAR(120) NOT NULL,
    "medium_category" VARCHAR(120) NOT NULL,
    "small_category" VARCHAR(120) NOT NULL,
    "brand" VARCHAR(120) NOT NULL,
    "supplier" VARCHAR(160) NOT NULL,
    "pack_qty" INTEGER NOT NULL,
    "box_qty" INTEGER NOT NULL,
    "default_lead_time" INTEGER NOT NULL,
    "min_stock" INTEGER NOT NULL,
    "is_active" VARCHAR(20) NOT NULL,
    "location_registered" BOOLEAN DEFAULT false NOT NULL,
    "memo" VARCHAR(500) NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_product_master_sku" ON "product_master" ("sku");
CREATE INDEX IF NOT EXISTS "ix_product_master_barcode" ON "product_master" ("barcode");
CREATE INDEX IF NOT EXISTS "ix_product_master_product_name" ON "product_master" ("product_name");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_product_master_barcode" ON "product_master" ("barcode");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_product_master_sku" ON "product_master" ("sku");
SELECT setval(pg_get_serial_sequence('product_master', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "product_master"), 1), 1), (SELECT COUNT(*) FROM "product_master") > 0);

CREATE TABLE IF NOT EXISTS "production_plans" (
    "id" BIGSERIAL PRIMARY KEY,
    "plan_number" VARCHAR(40) NOT NULL,
    "product_name" VARCHAR(255) NOT NULL,
    "plan_qty" INTEGER NOT NULL,
    "due_date" DATE NOT NULL,
    "status" VARCHAR(40) NOT NULL,
    "memo" VARCHAR(500) NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_production_plans_product_name" ON "production_plans" ("product_name");
CREATE INDEX IF NOT EXISTS "ix_production_plans_due_date" ON "production_plans" ("due_date");
CREATE INDEX IF NOT EXISTS "ix_production_plans_plan_number" ON "production_plans" ("plan_number");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_production_plans_plan_number" ON "production_plans" ("plan_number");
SELECT setval(pg_get_serial_sequence('production_plans', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "production_plans"), 1), 1), (SELECT COUNT(*) FROM "production_plans") > 0);

CREATE TABLE IF NOT EXISTS "purchase_budget_stores" (
    "id" BIGSERIAL PRIMARY KEY,
    "store_key" VARCHAR(80) NOT NULL,
    "payload" JSONB NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS "ix_purchase_budget_stores_store_key" ON "purchase_budget_stores" ("store_key");
SELECT setval(pg_get_serial_sequence('purchase_budget_stores', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "purchase_budget_stores"), 1), 1), (SELECT COUNT(*) FROM "purchase_budget_stores") > 0);

CREATE TABLE IF NOT EXISTS "purchase_documents" (
    "id" BIGSERIAL PRIMARY KEY,
    "document_type" VARCHAR(80) NOT NULL,
    "document_number" VARCHAR(60) NOT NULL,
    "version" INTEGER NOT NULL,
    "creator" VARCHAR(120) NOT NULL,
    "pr_number" VARCHAR(40) NOT NULL,
    "quote_number" VARCHAR(40) NOT NULL,
    "po_number" VARCHAR(40) NOT NULL,
    "supplier_name" VARCHAR(160) NOT NULL,
    "file_name" VARCHAR(255) NOT NULL,
    "file_mime" VARCHAR(120) NOT NULL,
    "file_bytes" BYTEA NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_purchase_documents_document_type" ON "purchase_documents" ("document_type");
CREATE INDEX IF NOT EXISTS "ix_purchase_documents_po_number" ON "purchase_documents" ("po_number");
CREATE INDEX IF NOT EXISTS "ix_purchase_documents_supplier_name" ON "purchase_documents" ("supplier_name");
CREATE INDEX IF NOT EXISTS "ix_purchase_documents_pr_number" ON "purchase_documents" ("pr_number");
CREATE INDEX IF NOT EXISTS "ix_purchase_documents_document_number" ON "purchase_documents" ("document_number");
CREATE INDEX IF NOT EXISTS "ix_purchase_documents_quote_number" ON "purchase_documents" ("quote_number");
SELECT setval(pg_get_serial_sequence('purchase_documents', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "purchase_documents"), 1), 1), (SELECT COUNT(*) FROM "purchase_documents") > 0);

CREATE TABLE IF NOT EXISTS "purchase_orders" (
    "id" BIGSERIAL PRIMARY KEY,
    "po_number" VARCHAR(40) NOT NULL,
    "pr_number" VARCHAR(40) NOT NULL,
    "supplier_name" VARCHAR(160) NOT NULL,
    "item_name" VARCHAR(255) NOT NULL,
    "spec" VARCHAR(160) NOT NULL,
    "quantity" INTEGER NOT NULL,
    "unit_price" INTEGER NOT NULL,
    "shipping_fee" INTEGER NOT NULL,
    "order_date" DATE NOT NULL,
    "expected_inbound_date" DATE,
    "actual_inbound_date" DATE,
    "inbound_status" VARCHAR(40) NOT NULL,
    "progress_status" VARCHAR(40) NOT NULL,
    "order_amount" INTEGER NOT NULL,
    "memo" VARCHAR(500) NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "currency" VARCHAR(10) DEFAULT 'KRW' NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_purchase_orders_order_date" ON "purchase_orders" ("order_date");
CREATE INDEX IF NOT EXISTS "ix_purchase_orders_po_number" ON "purchase_orders" ("po_number");
CREATE INDEX IF NOT EXISTS "ix_purchase_orders_item_name" ON "purchase_orders" ("item_name");
CREATE INDEX IF NOT EXISTS "ix_purchase_orders_supplier_name" ON "purchase_orders" ("supplier_name");
CREATE INDEX IF NOT EXISTS "ix_purchase_orders_pr_number" ON "purchase_orders" ("pr_number");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_purchase_orders_po_number" ON "purchase_orders" ("po_number");
SELECT setval(pg_get_serial_sequence('purchase_orders', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "purchase_orders"), 1), 1), (SELECT COUNT(*) FROM "purchase_orders") > 0);

CREATE TABLE IF NOT EXISTS "purchase_requests" (
    "id" BIGSERIAL PRIMARY KEY,
    "pr_number" VARCHAR(40) NOT NULL,
    "department" VARCHAR(120) NOT NULL,
    "item_name" VARCHAR(255) NOT NULL,
    "spec" VARCHAR(160) NOT NULL,
    "quantity" INTEGER NOT NULL,
    "request_date" DATE NOT NULL,
    "requester" VARCHAR(120) NOT NULL,
    "approval_status" VARCHAR(40) NOT NULL,
    "source_type" VARCHAR(40) NOT NULL,
    "linked_po_number" VARCHAR(40) NOT NULL,
    "memo" VARCHAR(500) NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "item_code" VARCHAR(120) DEFAULT '' NOT NULL,
    "unit" VARCHAR(40) DEFAULT 'EA' NOT NULL,
    "reply_due_date" DATE,
    "desired_due_date" DATE,
    "delivery_place" VARCHAR(160) DEFAULT '로긴 물류센터' NOT NULL,
    "request_notes" VARCHAR(500) DEFAULT '' NOT NULL,
    "approver" VARCHAR(120) DEFAULT '' NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_purchase_requests_request_date" ON "purchase_requests" ("request_date");
CREATE INDEX IF NOT EXISTS "ix_purchase_requests_pr_number" ON "purchase_requests" ("pr_number");
CREATE INDEX IF NOT EXISTS "ix_purchase_requests_item_name" ON "purchase_requests" ("item_name");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_purchase_requests_pr_number" ON "purchase_requests" ("pr_number");
SELECT setval(pg_get_serial_sequence('purchase_requests', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "purchase_requests"), 1), 1), (SELECT COUNT(*) FROM "purchase_requests") > 0);

CREATE TABLE IF NOT EXISTS "rfq_quotes" (
    "id" BIGSERIAL PRIMARY KEY,
    "pr_number" VARCHAR(40) NOT NULL,
    "item_name" VARCHAR(255) NOT NULL,
    "supplier_name" VARCHAR(160) NOT NULL,
    "unit_price" INTEGER NOT NULL,
    "moq" INTEGER NOT NULL,
    "lead_time_days" INTEGER NOT NULL,
    "shipping_fee" INTEGER NOT NULL,
    "memo" VARCHAR(500) NOT NULL,
    "is_recommended" BOOLEAN NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "quote_number" VARCHAR(40) DEFAULT '' NOT NULL,
    "supplier_manager" VARCHAR(120) DEFAULT '' NOT NULL,
    "supplier_phone" VARCHAR(80) DEFAULT '' NOT NULL,
    "supplier_email" VARCHAR(160) DEFAULT '' NOT NULL,
    "payment_terms" VARCHAR(120) DEFAULT '' NOT NULL,
    "quote_valid_until" DATE,
    "is_selected" BOOLEAN DEFAULT FALSE NOT NULL,
    "selection_reason" VARCHAR(500) DEFAULT '' NOT NULL,
    "currency" VARCHAR(10) DEFAULT 'KRW' NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_rfq_quotes_item_name" ON "rfq_quotes" ("item_name");
CREATE INDEX IF NOT EXISTS "ix_rfq_quotes_supplier_name" ON "rfq_quotes" ("supplier_name");
CREATE INDEX IF NOT EXISTS "ix_rfq_quotes_pr_number" ON "rfq_quotes" ("pr_number");
SELECT setval(pg_get_serial_sequence('rfq_quotes', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "rfq_quotes"), 1), 1), (SELECT COUNT(*) FROM "rfq_quotes") > 0);

CREATE TABLE IF NOT EXISTS "schedule_highlights" (
    "id" BIGSERIAL PRIMARY KEY,
    "week_id" INTEGER NOT NULL,
    "sort_order" INTEGER NOT NULL,
    "title" TEXT NOT NULL,
    "checked" INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_schedule_highlights_week_id" ON "schedule_highlights" ("week_id");
SELECT setval(pg_get_serial_sequence('schedule_highlights', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "schedule_highlights"), 1), 1), (SELECT COUNT(*) FROM "schedule_highlights") > 0);

CREATE TABLE IF NOT EXISTS "schedule_slots" (
    "id" BIGSERIAL PRIMARY KEY,
    "week_id" INTEGER NOT NULL,
    "sort_order" INTEGER NOT NULL,
    "time_label" TEXT NOT NULL,
    "mon" TEXT NOT NULL,
    "tue" TEXT NOT NULL,
    "wed" TEXT NOT NULL,
    "thu" TEXT NOT NULL,
    "fri" TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_schedule_slots_week_id" ON "schedule_slots" ("week_id");
SELECT setval(pg_get_serial_sequence('schedule_slots', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "schedule_slots"), 1), 1), (SELECT COUNT(*) FROM "schedule_slots") > 0);

CREATE TABLE IF NOT EXISTS "schedule_weeks" (
    "id" BIGSERIAL PRIMARY KEY,
    "week_start" VARCHAR(20) NOT NULL,
    "title" TEXT NOT NULL,
    "owner" TEXT NOT NULL,
    "comment" TEXT NOT NULL,
    "created_at" VARCHAR(40) NOT NULL,
    "updated_at" VARCHAR(40) NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_schedule_weeks_week_start" ON "schedule_weeks" ("week_start");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_schedule_weeks_week_start" ON "schedule_weeks" ("week_start");
SELECT setval(pg_get_serial_sequence('schedule_weeks', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "schedule_weeks"), 1), 1), (SELECT COUNT(*) FROM "schedule_weeks") > 0);

CREATE TABLE IF NOT EXISTS "supplier_approval_history" (
    "id" BIGSERIAL PRIMARY KEY,
    "evaluation_id" INTEGER NOT NULL,
    "supplier_id" INTEGER NOT NULL,
    "action_type" VARCHAR(40) NOT NULL,
    "status_from" VARCHAR(40) NOT NULL,
    "status_to" VARCHAR(40) NOT NULL,
    "reason" TEXT NOT NULL,
    "actor" VARCHAR(120) NOT NULL,
    "acted_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_supplier_approval_history_action_type" ON "supplier_approval_history" ("action_type");
CREATE INDEX IF NOT EXISTS "ix_supplier_approval_history_acted_at" ON "supplier_approval_history" ("acted_at");
CREATE INDEX IF NOT EXISTS "ix_supplier_approval_history_evaluation_id" ON "supplier_approval_history" ("evaluation_id");
CREATE INDEX IF NOT EXISTS "ix_supplier_approval_history_supplier_id" ON "supplier_approval_history" ("supplier_id");
SELECT setval(pg_get_serial_sequence('supplier_approval_history', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "supplier_approval_history"), 1), 1), (SELECT COUNT(*) FROM "supplier_approval_history") > 0);

CREATE TABLE IF NOT EXISTS "supplier_evaluation_categories" (
    "id" BIGSERIAL PRIMARY KEY,
    "criteria_version" VARCHAR(40) NOT NULL,
    "category_order" INTEGER NOT NULL,
    "category_name" VARCHAR(120) NOT NULL,
    "category_weight" DOUBLE PRECISION NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluation_categories_criteria_version" ON "supplier_evaluation_categories" ("criteria_version");
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluation_categories_category_name" ON "supplier_evaluation_categories" ("category_name");
SELECT setval(pg_get_serial_sequence('supplier_evaluation_categories', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "supplier_evaluation_categories"), 1), 1), (SELECT COUNT(*) FROM "supplier_evaluation_categories") > 0);

CREATE TABLE IF NOT EXISTS "supplier_evaluation_criteria" (
    "id" BIGSERIAL PRIMARY KEY,
    "criteria_version" VARCHAR(40) NOT NULL,
    "category_name" VARCHAR(120) NOT NULL,
    "category_weight" DOUBLE PRECISION NOT NULL,
    "item_name" VARCHAR(160) NOT NULL,
    "item_weight" DOUBLE PRECISION NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "category_order" INTEGER DEFAULT 0 NOT NULL,
    "item_order" INTEGER DEFAULT 0 NOT NULL,
    "item_description" VARCHAR(500) DEFAULT '' NOT NULL,
    "is_required" BOOLEAN DEFAULT TRUE NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluation_criteria_criteria_version" ON "supplier_evaluation_criteria" ("criteria_version");
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluation_criteria_item_name" ON "supplier_evaluation_criteria" ("item_name");
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluation_criteria_category_name" ON "supplier_evaluation_criteria" ("category_name");
SELECT setval(pg_get_serial_sequence('supplier_evaluation_criteria', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "supplier_evaluation_criteria"), 1), 1), (SELECT COUNT(*) FROM "supplier_evaluation_criteria") > 0);

CREATE TABLE IF NOT EXISTS "supplier_evaluation_criteria_versions" (
    "id" BIGSERIAL PRIMARY KEY,
    "version_code" VARCHAR(40) NOT NULL,
    "version_name" VARCHAR(120) NOT NULL,
    "status" VARCHAR(40) NOT NULL,
    "note" VARCHAR(500) NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "created_by" VARCHAR(120) NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluation_criteria_versions_status" ON "supplier_evaluation_criteria_versions" ("status");
CREATE UNIQUE INDEX IF NOT EXISTS "ix_supplier_evaluation_criteria_versions_version_code" ON "supplier_evaluation_criteria_versions" ("version_code");
SELECT setval(pg_get_serial_sequence('supplier_evaluation_criteria_versions', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "supplier_evaluation_criteria_versions"), 1), 1), (SELECT COUNT(*) FROM "supplier_evaluation_criteria_versions") > 0);

CREATE TABLE IF NOT EXISTS "supplier_evaluation_history" (
    "id" BIGSERIAL PRIMARY KEY,
    "evaluation_id" INTEGER NOT NULL,
    "supplier_id" INTEGER NOT NULL,
    "action_type" VARCHAR(40) NOT NULL,
    "before_data" TEXT NOT NULL,
    "after_data" TEXT NOT NULL,
    "changed_by" VARCHAR(120) NOT NULL,
    "changed_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "change_reason" VARCHAR(500) DEFAULT '' NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluation_history_action_type" ON "supplier_evaluation_history" ("action_type");
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluation_history_supplier_id" ON "supplier_evaluation_history" ("supplier_id");
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluation_history_changed_at" ON "supplier_evaluation_history" ("changed_at");
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluation_history_evaluation_id" ON "supplier_evaluation_history" ("evaluation_id");
SELECT setval(pg_get_serial_sequence('supplier_evaluation_history', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "supplier_evaluation_history"), 1), 1), (SELECT COUNT(*) FROM "supplier_evaluation_history") > 0);

CREATE TABLE IF NOT EXISTS "supplier_evaluation_items" (
    "id" BIGSERIAL PRIMARY KEY,
    "evaluation_id" INTEGER NOT NULL,
    "category_id" INTEGER NOT NULL,
    "item_id" INTEGER NOT NULL,
    "category_name" VARCHAR(120) NOT NULL,
    "item_name" VARCHAR(160) NOT NULL,
    "selected_rating" VARCHAR(40) NOT NULL,
    "item_score" DOUBLE PRECISION NOT NULL,
    "item_weight" DOUBLE PRECISION NOT NULL,
    "not_applicable" BOOLEAN NOT NULL,
    "comment" VARCHAR(500) NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluation_items_evaluation_id" ON "supplier_evaluation_items" ("evaluation_id");
SELECT setval(pg_get_serial_sequence('supplier_evaluation_items', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "supplier_evaluation_items"), 1), 1), (SELECT COUNT(*) FROM "supplier_evaluation_items") > 0);

CREATE TABLE IF NOT EXISTS "supplier_evaluations" (
    "id" BIGSERIAL PRIMARY KEY,
    "supplier_id" INTEGER NOT NULL,
    "evaluation_year" INTEGER NOT NULL,
    "evaluation_quarter" VARCHAR(10) NOT NULL,
    "period_start" DATE,
    "period_end" DATE,
    "evaluation_date" DATE NOT NULL,
    "evaluator" VARCHAR(120) NOT NULL,
    "status" VARCHAR(40) NOT NULL,
    "quality_score" DOUBLE PRECISION NOT NULL,
    "delivery_score" DOUBLE PRECISION NOT NULL,
    "price_score" DOUBLE PRECISION NOT NULL,
    "service_score" DOUBLE PRECISION NOT NULL,
    "stability_score" DOUBLE PRECISION NOT NULL,
    "total_score" DOUBLE PRECISION NOT NULL,
    "final_grade" VARCHAR(20) NOT NULL,
    "previous_grade" VARCHAR(20) NOT NULL,
    "special_flags" TEXT NOT NULL,
    "special_warning" BOOLEAN NOT NULL,
    "overall_comment" TEXT NOT NULL,
    "improvement_request" TEXT NOT NULL,
    "improvement_due_date" DATE,
    "criteria_version" VARCHAR(40) NOT NULL,
    "is_deleted" BOOLEAN NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "next_evaluation_date" DATE,
    "applicable_weight" DOUBLE PRECISION DEFAULT 0 NOT NULL,
    "earned_score" DOUBLE PRECISION DEFAULT 0 NOT NULL,
    "base_grade" VARCHAR(20) DEFAULT '미평가' NOT NULL,
    "grade_limit_reason" VARCHAR(500) DEFAULT '' NOT NULL,
    "special_reasons" TEXT DEFAULT '' NOT NULL,
    "excellent_points" TEXT DEFAULT '' NOT NULL,
    "problem_points" TEXT DEFAULT '' NOT NULL,
    "improvement_owner" VARCHAR(120) DEFAULT '' NOT NULL,
    "improvement_status" VARCHAR(40) DEFAULT '해당 없음' NOT NULL,
    "attachment_ref" VARCHAR(500) DEFAULT '' NOT NULL,
    "internal_memo" TEXT DEFAULT '' NOT NULL,
    "rejection_reason" TEXT DEFAULT '' NOT NULL,
    "inactive_reason" TEXT DEFAULT '' NOT NULL,
    "inactive_at" TIMESTAMP WITHOUT TIME ZONE,
    "created_by" VARCHAR(120) DEFAULT '' NOT NULL,
    "updated_by" VARCHAR(120) DEFAULT '' NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluations_final_grade" ON "supplier_evaluations" ("final_grade");
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluations_evaluation_quarter" ON "supplier_evaluations" ("evaluation_quarter");
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluations_evaluation_year" ON "supplier_evaluations" ("evaluation_year");
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluations_evaluator" ON "supplier_evaluations" ("evaluator");
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluations_evaluation_date" ON "supplier_evaluations" ("evaluation_date");
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluations_status" ON "supplier_evaluations" ("status");
CREATE INDEX IF NOT EXISTS "ix_supplier_evaluations_supplier_id" ON "supplier_evaluations" ("supplier_id");
SELECT setval(pg_get_serial_sequence('supplier_evaluations', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "supplier_evaluations"), 1), 1), (SELECT COUNT(*) FROM "supplier_evaluations") > 0);

CREATE TABLE IF NOT EXISTS "supplier_grade_rules" (
    "id" BIGSERIAL PRIMARY KEY,
    "grade" VARCHAR(20) NOT NULL,
    "minimum_score" DOUBLE PRECISION NOT NULL,
    "maximum_score" DOUBLE PRECISION NOT NULL,
    "label" VARCHAR(80) NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "auto_downgrade_enabled" BOOLEAN NOT NULL,
    "major_quality_max_grade" VARCHAR(20) NOT NULL,
    "contract_violation_max_grade" VARCHAR(20) NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_supplier_grade_rules_grade" ON "supplier_grade_rules" ("grade");
SELECT setval(pg_get_serial_sequence('supplier_grade_rules', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "supplier_grade_rules"), 1), 1), (SELECT COUNT(*) FROM "supplier_grade_rules") > 0);

CREATE TABLE IF NOT EXISTS "supplier_special_rules" (
    "id" BIGSERIAL PRIMARY KEY,
    "flag_name" VARCHAR(120) NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "show_warning" BOOLEAN NOT NULL,
    "reason_required" BOOLEAN NOT NULL,
    "grade_limit_enabled" BOOLEAN NOT NULL,
    "max_grade" VARCHAR(20) NOT NULL,
    "reflect_to_supplier" BOOLEAN NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS "ix_supplier_special_rules_flag_name" ON "supplier_special_rules" ("flag_name");
SELECT setval(pg_get_serial_sequence('supplier_special_rules', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "supplier_special_rules"), 1), 1), (SELECT COUNT(*) FROM "supplier_special_rules") > 0);

CREATE TABLE IF NOT EXISTS "suppliers" (
    "id" BIGSERIAL PRIMARY KEY,
    "supplier_name" VARCHAR(160) NOT NULL,
    "manager" VARCHAR(120) NOT NULL,
    "phone" VARCHAR(80) NOT NULL,
    "email" VARCHAR(160) NOT NULL,
    "avg_lead_time_days" INTEGER NOT NULL,
    "avg_unit_price" INTEGER NOT NULL,
    "rating" VARCHAR(40) NOT NULL,
    "memo" VARCHAR(500) NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "handled_items" VARCHAR(500) DEFAULT '' NOT NULL,
    "avg_unit_price_currency" VARCHAR(10) DEFAULT 'KRW' NOT NULL,
    "moq_terms" VARCHAR(500) DEFAULT '' NOT NULL,
    "payment_terms" VARCHAR(120) DEFAULT '' NOT NULL,
    "supplier_code" VARCHAR(40) DEFAULT '' NOT NULL,
    "business_number" VARCHAR(80) DEFAULT '' NOT NULL,
    "transaction_status" VARCHAR(40) DEFAULT '거래중' NOT NULL,
    "current_grade" VARCHAR(20) DEFAULT '미평가' NOT NULL,
    "latest_score" DOUBLE PRECISION DEFAULT 0 NOT NULL,
    "latest_evaluation_date" DATE,
    "next_evaluation_date" DATE,
    "special_management" BOOLEAN DEFAULT FALSE NOT NULL,
    "special_reason" VARCHAR(500) DEFAULT '' NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_suppliers_supplier_name" ON "suppliers" ("supplier_name");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_suppliers_supplier_name" ON "suppliers" ("supplier_name");
SELECT setval(pg_get_serial_sequence('suppliers', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "suppliers"), 1), 1), (SELECT COUNT(*) FROM "suppliers") > 0);

CREATE TABLE IF NOT EXISTS "thirdparty_product_master" (
    "id" BIGSERIAL PRIMARY KEY,
    "sku" VARCHAR(120) NOT NULL,
    "barcode" VARCHAR(120) NOT NULL,
    "product_name" VARCHAR(255) NOT NULL,
    "large_category" VARCHAR(120) NOT NULL,
    "medium_category" VARCHAR(120) NOT NULL,
    "small_category" VARCHAR(120) NOT NULL,
    "brand" VARCHAR(120) NOT NULL,
    "supplier" VARCHAR(160) NOT NULL,
    "pack_qty" INTEGER NOT NULL,
    "box_qty" INTEGER NOT NULL,
    "default_lead_time" INTEGER NOT NULL,
    "min_stock" INTEGER NOT NULL,
    "sort_order" INTEGER DEFAULT 0 NOT NULL,
    "is_active" VARCHAR(20) NOT NULL,
    "location_registered" BOOLEAN DEFAULT false NOT NULL,
    "memo" VARCHAR(500) NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_thirdparty_product_master_product_name" ON "thirdparty_product_master" ("product_name");
CREATE INDEX IF NOT EXISTS "ix_thirdparty_product_master_barcode" ON "thirdparty_product_master" ("barcode");
CREATE INDEX IF NOT EXISTS "ix_thirdparty_product_master_sku" ON "thirdparty_product_master" ("sku");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_thirdparty_product_master_sku" ON "thirdparty_product_master" ("sku");
SELECT setval(pg_get_serial_sequence('thirdparty_product_master', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "thirdparty_product_master"), 1), 1), (SELECT COUNT(*) FROM "thirdparty_product_master") > 0);

CREATE TABLE IF NOT EXISTS "warehouse_layouts" (
    "id" VARCHAR(36) NOT NULL,
    "building" VARCHAR(120) NOT NULL,
    "floor" VARCHAR(40) NOT NULL,
    "layout_data" JSONB NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_warehouse_layouts_building" ON "warehouse_layouts" ("building");
CREATE INDEX IF NOT EXISTS "ix_warehouse_layouts_floor" ON "warehouse_layouts" ("floor");
CREATE INDEX IF NOT EXISTS "ix_warehouse_layouts_is_active" ON "warehouse_layouts" ("is_active");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_warehouse_layouts_building_floor" ON "warehouse_layouts" ("building", "floor");
ALTER TABLE "warehouse_layouts" ALTER COLUMN "id" SET DEFAULT gen_random_uuid()::text;

CREATE TABLE IF NOT EXISTS "warehouse_racks" (
    "id" VARCHAR(64) NOT NULL,
    "layout_id" VARCHAR(36) NOT NULL REFERENCES "warehouse_layouts" ("id") ON DELETE CASCADE,
    "rack_code" VARCHAR(120) NOT NULL,
    "rack_name" VARCHAR(160) DEFAULT '' NOT NULL,
    "x" DOUBLE PRECISION DEFAULT 0 NOT NULL,
    "y" DOUBLE PRECISION DEFAULT 0 NOT NULL,
    "z" DOUBLE PRECISION DEFAULT 0 NOT NULL,
    "rotation" DOUBLE PRECISION DEFAULT 0 NOT NULL,
    "width" DOUBLE PRECISION DEFAULT 0 NOT NULL,
    "depth" DOUBLE PRECISION DEFAULT 0 NOT NULL,
    "height" DOUBLE PRECISION DEFAULT 0 NOT NULL,
    "shelf_count" INTEGER DEFAULT 1 NOT NULL,
    "rack_type" VARCHAR(60) DEFAULT '' NOT NULL,
    "sort_order" INTEGER DEFAULT 0 NOT NULL,
    "rack_data" JSONB DEFAULT '{}'::jsonb NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_warehouse_racks_layout_id" ON "warehouse_racks" ("layout_id");
CREATE INDEX IF NOT EXISTS "ix_warehouse_racks_rack_code" ON "warehouse_racks" ("rack_code");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_warehouse_racks_layout_rack_code" ON "warehouse_racks" ("layout_id", "rack_code");

CREATE TABLE IF NOT EXISTS "warehouse_inventory_positions" (
    "id" VARCHAR(80) NOT NULL,
    "rack_id" VARCHAR(64) NOT NULL REFERENCES "warehouse_racks" ("id") ON DELETE CASCADE,
    "shelf_no" INTEGER DEFAULT 1 NOT NULL,
    "sku" VARCHAR(120) DEFAULT '' NOT NULL,
    "item_name" VARCHAR(255) DEFAULT '' NOT NULL,
    "quantity" INTEGER DEFAULT 0 NOT NULL,
    "sort_order" INTEGER DEFAULT 0 NOT NULL,
    "position_data" JSONB DEFAULT '{}'::jsonb NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "ix_warehouse_inventory_positions_rack_id" ON "warehouse_inventory_positions" ("rack_id");
CREATE INDEX IF NOT EXISTS "ix_warehouse_inventory_positions_sku" ON "warehouse_inventory_positions" ("sku");
CREATE INDEX IF NOT EXISTS "ix_warehouse_inventory_positions_item_name" ON "warehouse_inventory_positions" ("item_name");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_warehouse_inventory_positions_rack_item" ON "warehouse_inventory_positions" ("rack_id", "shelf_no", "sku", "item_name");

CREATE TABLE IF NOT EXISTS "warehouse_product_master" (
    "id" BIGSERIAL PRIMARY KEY,
    "sku" VARCHAR(120) NOT NULL,
    "barcode" VARCHAR(120) NOT NULL,
    "product_name" VARCHAR(255) NOT NULL,
    "large_category" VARCHAR(120) NOT NULL,
    "medium_category" VARCHAR(120) NOT NULL,
    "small_category" VARCHAR(120) NOT NULL,
    "brand" VARCHAR(120) NOT NULL,
    "supplier" VARCHAR(160) NOT NULL,
    "pack_qty" INTEGER NOT NULL,
    "box_qty" INTEGER NOT NULL,
    "default_lead_time" INTEGER NOT NULL,
    "min_stock" INTEGER NOT NULL,
    "is_active" VARCHAR(20) NOT NULL,
    "location_registered" BOOLEAN DEFAULT false NOT NULL,
    "memo" VARCHAR(500) NOT NULL,
    "created_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "updated_at" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    "sort_order" INTEGER DEFAULT 0 NOT NULL
);
CREATE INDEX IF NOT EXISTS "ix_warehouse_product_master_product_name" ON "warehouse_product_master" ("product_name");
CREATE INDEX IF NOT EXISTS "ix_warehouse_product_master_barcode" ON "warehouse_product_master" ("barcode");
CREATE INDEX IF NOT EXISTS "ix_warehouse_product_master_sku" ON "warehouse_product_master" ("sku");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_warehouse_product_master_barcode_product_name" ON "warehouse_product_master" ("barcode", "product_name");
CREATE UNIQUE INDEX IF NOT EXISTS "uq_warehouse_product_master_sku" ON "warehouse_product_master" ("sku");
SELECT setval(pg_get_serial_sequence('warehouse_product_master', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM "warehouse_product_master"), 1), 1), (SELECT COUNT(*) FROM "warehouse_product_master") > 0);

COMMIT;
