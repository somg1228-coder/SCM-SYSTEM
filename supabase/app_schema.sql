-- SCM-SYSTEM application schema for Supabase PostgreSQL
-- Generated from backend.models. Run this before using SCM_DATABASE_URL.
-- This file intentionally follows the existing SQLAlchemy app tables, not the older REST inventory_items schema.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS category_bom_items (
	id SERIAL NOT NULL, 
	category_name VARCHAR(160) NOT NULL, 
	sort_order INTEGER NOT NULL, 
	item_name VARCHAR(255) NOT NULL, 
	item_type VARCHAR(40) NOT NULL, 
	manager VARCHAR(120) NOT NULL, 
	vendor VARCHAR(160) NOT NULL, 
	required_stock INTEGER NOT NULL, 
	barcode VARCHAR(120) NOT NULL, 
	spec VARCHAR(160) NOT NULL, 
	barcode_spec VARCHAR(160) NOT NULL, 
	memo VARCHAR(500) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_category_bom_items_category_name ON category_bom_items (category_name);
CREATE INDEX IF NOT EXISTS ix_category_bom_items_id ON category_bom_items (id);
CREATE INDEX IF NOT EXISTS ix_category_bom_items_item_name ON category_bom_items (item_name);

CREATE TABLE IF NOT EXISTS inventory_daily (
	id SERIAL NOT NULL, 
	source_type VARCHAR(20) NOT NULL, 
	work_date DATE NOT NULL, 
	category VARCHAR(120) NOT NULL, 
	product_code VARCHAR(120) NOT NULL, 
	product_name VARCHAR(255) NOT NULL, 
	barcode VARCHAR(120) NOT NULL, 
	supplier VARCHAR(160) NOT NULL, 
	current_stock INTEGER NOT NULL, 
	available_stock INTEGER NOT NULL, 
	safe_stock INTEGER NOT NULL, 
	stock_status VARCHAR(40) NOT NULL, 
	outbound_qty INTEGER NOT NULL, 
	previous_inbound_date DATE, 
	last_inbound_date DATE, 
	inbound_qty INTEGER NOT NULL, 
	inbound_cycle INTEGER, 
	memo VARCHAR(500) NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_inventory_daily_source_date_item UNIQUE (source_type, work_date, product_name), 
	CONSTRAINT ck_inventory_daily_source_type CHECK (source_type IN ('3PL', '오프라인', '창고'))
);

CREATE INDEX IF NOT EXISTS ix_inventory_daily_id ON inventory_daily (id);
CREATE INDEX IF NOT EXISTS ix_inventory_daily_product_name ON inventory_daily (product_name);
CREATE INDEX IF NOT EXISTS ix_inventory_daily_source_type ON inventory_daily (source_type);
CREATE INDEX IF NOT EXISTS ix_inventory_daily_work_date ON inventory_daily (work_date);

CREATE TABLE IF NOT EXISTS inventory_inbound (
	id SERIAL NOT NULL, 
	source_type VARCHAR(20) NOT NULL, 
	inbound_date DATE NOT NULL, 
	category VARCHAR(120) NOT NULL, 
	product_code VARCHAR(120) NOT NULL, 
	product_name VARCHAR(255) NOT NULL, 
	barcode VARCHAR(120) NOT NULL, 
	inbound_qty INTEGER NOT NULL, 
	vendor VARCHAR(160) NOT NULL, 
	inbound_type VARCHAR(80) NOT NULL, 
	memo VARCHAR(500) NOT NULL, 
	is_applied BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_inventory_inbound_source_type CHECK (source_type IN ('3PL', '오프라인', '창고'))
);

CREATE INDEX IF NOT EXISTS ix_inventory_inbound_id ON inventory_inbound (id);
CREATE INDEX IF NOT EXISTS ix_inventory_inbound_inbound_date ON inventory_inbound (inbound_date);
CREATE INDEX IF NOT EXISTS ix_inventory_inbound_product_name ON inventory_inbound (product_name);
CREATE INDEX IF NOT EXISTS ix_inventory_inbound_source_type ON inventory_inbound (source_type);

CREATE TABLE IF NOT EXISTS inventory_output_histories (
	id SERIAL NOT NULL, 
	source_type VARCHAR(20) NOT NULL, 
	work_date DATE, 
	output_type VARCHAR(20) NOT NULL, 
	created_by VARCHAR(120) NOT NULL, 
	filter_json TEXT NOT NULL, 
	item_count INTEGER NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_inventory_output_histories_id ON inventory_output_histories (id);
CREATE INDEX IF NOT EXISTS ix_inventory_output_histories_source_type ON inventory_output_histories (source_type);

CREATE TABLE IF NOT EXISTS inventory_upload_histories (
	id SERIAL NOT NULL, 
	source_type VARCHAR(20) NOT NULL, 
	work_date DATE NOT NULL, 
	file_name VARCHAR(255) NOT NULL, 
	uploaded_by VARCHAR(120) NOT NULL, 
	upload_mode VARCHAR(20) NOT NULL, 
	total_rows INTEGER NOT NULL, 
	matched_count INTEGER NOT NULL, 
	failed_count INTEGER NOT NULL, 
	duplicate_count INTEGER NOT NULL, 
	zeroed_count INTEGER NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_inventory_upload_histories_id ON inventory_upload_histories (id);
CREATE INDEX IF NOT EXISTS ix_inventory_upload_histories_source_type ON inventory_upload_histories (source_type);
CREATE INDEX IF NOT EXISTS ix_inventory_upload_histories_work_date ON inventory_upload_histories (work_date);

CREATE TABLE IF NOT EXISTS inventory_upload_snapshots (
	id SERIAL NOT NULL, 
	upload_history_id INTEGER NOT NULL, 
	source_type VARCHAR(20) NOT NULL, 
	work_date DATE NOT NULL, 
	product_code VARCHAR(120) NOT NULL, 
	barcode VARCHAR(120) NOT NULL, 
	product_name VARCHAR(255) NOT NULL, 
	previous_stock INTEGER NOT NULL, 
	new_stock INTEGER NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_inventory_upload_snapshots_id ON inventory_upload_snapshots (id);
CREATE INDEX IF NOT EXISTS ix_inventory_upload_snapshots_source_type ON inventory_upload_snapshots (source_type);
CREATE INDEX IF NOT EXISTS ix_inventory_upload_snapshots_upload_history_id ON inventory_upload_snapshots (upload_history_id);
CREATE INDEX IF NOT EXISTS ix_inventory_upload_snapshots_work_date ON inventory_upload_snapshots (work_date);

CREATE TABLE IF NOT EXISTS material_inventory_items (
	id SERIAL NOT NULL, 
	category VARCHAR(120) NOT NULL, 
	item_type VARCHAR(40) NOT NULL, 
	related_product VARCHAR(255) NOT NULL, 
	item_code VARCHAR(120) NOT NULL, 
	item_name VARCHAR(255) NOT NULL, 
	spec VARCHAR(255) NOT NULL, 
	unit VARCHAR(40) NOT NULL, 
	current_stock INTEGER NOT NULL, 
	safe_stock INTEGER NOT NULL, 
	location VARCHAR(160) NOT NULL, 
	supplier VARCHAR(160) NOT NULL, 
	lead_time_days INTEGER NOT NULL, 
	memo VARCHAR(500) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_material_inventory_item_identity UNIQUE (item_code, item_name, related_product), 
	CONSTRAINT ck_material_inventory_item_type CHECK (item_type IN ('자재', '반제품'))
);

CREATE INDEX IF NOT EXISTS ix_material_inventory_items_category ON material_inventory_items (category);
CREATE INDEX IF NOT EXISTS ix_material_inventory_items_id ON material_inventory_items (id);
CREATE INDEX IF NOT EXISTS ix_material_inventory_items_item_code ON material_inventory_items (item_code);
CREATE INDEX IF NOT EXISTS ix_material_inventory_items_item_name ON material_inventory_items (item_name);
CREATE INDEX IF NOT EXISTS ix_material_inventory_items_item_type ON material_inventory_items (item_type);
CREATE INDEX IF NOT EXISTS ix_material_inventory_items_related_product ON material_inventory_items (related_product);

CREATE TABLE IF NOT EXISTS offline_product_master (
	id SERIAL NOT NULL, 
	sku VARCHAR(120) NOT NULL, 
	barcode VARCHAR(120) NOT NULL, 
	product_name VARCHAR(255) NOT NULL, 
	large_category VARCHAR(120) NOT NULL, 
	medium_category VARCHAR(120) NOT NULL, 
	small_category VARCHAR(120) NOT NULL, 
	brand VARCHAR(120) NOT NULL, 
	supplier VARCHAR(160) NOT NULL, 
	pack_qty INTEGER NOT NULL, 
	box_qty INTEGER NOT NULL, 
	default_lead_time INTEGER NOT NULL, 
	min_stock INTEGER NOT NULL, 
	sort_order INTEGER NOT NULL, 
	location_registered BOOLEAN DEFAULT false NOT NULL,
	is_active VARCHAR(20) NOT NULL, 
	memo VARCHAR(500) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_offline_product_master_sku UNIQUE (sku), 
	CONSTRAINT uq_offline_product_master_product_name UNIQUE (product_name), 
	CONSTRAINT ck_offline_product_master_is_active CHECK (is_active IN ('사용', '미사용'))
);

CREATE INDEX IF NOT EXISTS ix_offline_product_master_barcode ON offline_product_master (barcode);
CREATE INDEX IF NOT EXISTS ix_offline_product_master_id ON offline_product_master (id);
CREATE INDEX IF NOT EXISTS ix_offline_product_master_product_name ON offline_product_master (product_name);
CREATE INDEX IF NOT EXISTS ix_offline_product_master_sku ON offline_product_master (sku);

CREATE TABLE IF NOT EXISTS production_plans (
	id SERIAL NOT NULL, 
	plan_number VARCHAR(40) NOT NULL, 
	product_name VARCHAR(255) NOT NULL, 
	plan_qty INTEGER NOT NULL, 
	due_date DATE NOT NULL, 
	status VARCHAR(40) NOT NULL, 
	memo VARCHAR(500) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_production_plans_plan_number UNIQUE (plan_number)
);

CREATE INDEX IF NOT EXISTS ix_production_plans_due_date ON production_plans (due_date);
CREATE INDEX IF NOT EXISTS ix_production_plans_id ON production_plans (id);
CREATE INDEX IF NOT EXISTS ix_production_plans_plan_number ON production_plans (plan_number);
CREATE INDEX IF NOT EXISTS ix_production_plans_product_name ON production_plans (product_name);

CREATE TABLE IF NOT EXISTS purchase_documents (
	id SERIAL NOT NULL, 
	document_type VARCHAR(80) NOT NULL, 
	document_number VARCHAR(60) NOT NULL, 
	version INTEGER NOT NULL, 
	creator VARCHAR(120) NOT NULL, 
	pr_number VARCHAR(40) NOT NULL, 
	quote_number VARCHAR(40) NOT NULL, 
	po_number VARCHAR(40) NOT NULL, 
	supplier_name VARCHAR(160) NOT NULL, 
	file_name VARCHAR(255) NOT NULL, 
	file_mime VARCHAR(120) NOT NULL, 
	file_bytes BYTEA NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_purchase_documents_created_at ON purchase_documents (created_at);
CREATE INDEX IF NOT EXISTS ix_purchase_documents_document_number ON purchase_documents (document_number);
CREATE INDEX IF NOT EXISTS ix_purchase_documents_document_type ON purchase_documents (document_type);
CREATE INDEX IF NOT EXISTS ix_purchase_documents_id ON purchase_documents (id);
CREATE INDEX IF NOT EXISTS ix_purchase_documents_po_number ON purchase_documents (po_number);
CREATE INDEX IF NOT EXISTS ix_purchase_documents_pr_number ON purchase_documents (pr_number);
CREATE INDEX IF NOT EXISTS ix_purchase_documents_quote_number ON purchase_documents (quote_number);
CREATE INDEX IF NOT EXISTS ix_purchase_documents_supplier_name ON purchase_documents (supplier_name);

CREATE TABLE IF NOT EXISTS purchase_orders (
	id SERIAL NOT NULL, 
	po_number VARCHAR(40) NOT NULL, 
	pr_number VARCHAR(40) NOT NULL, 
	supplier_name VARCHAR(160) NOT NULL, 
	item_name VARCHAR(255) NOT NULL, 
	spec VARCHAR(160) NOT NULL, 
	quantity INTEGER NOT NULL, 
	unit_price FLOAT NOT NULL, 
	currency VARCHAR(10) NOT NULL, 
	shipping_fee INTEGER NOT NULL, 
	order_date DATE NOT NULL, 
	expected_inbound_date DATE, 
	actual_inbound_date DATE, 
	inbound_status VARCHAR(40) NOT NULL, 
	progress_status VARCHAR(40) NOT NULL, 
	order_amount FLOAT NOT NULL, 
	memo VARCHAR(500) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_purchase_orders_po_number UNIQUE (po_number)
);

CREATE INDEX IF NOT EXISTS ix_purchase_orders_id ON purchase_orders (id);
CREATE INDEX IF NOT EXISTS ix_purchase_orders_item_name ON purchase_orders (item_name);
CREATE INDEX IF NOT EXISTS ix_purchase_orders_order_date ON purchase_orders (order_date);
CREATE INDEX IF NOT EXISTS ix_purchase_orders_po_number ON purchase_orders (po_number);
CREATE INDEX IF NOT EXISTS ix_purchase_orders_pr_number ON purchase_orders (pr_number);
CREATE INDEX IF NOT EXISTS ix_purchase_orders_supplier_name ON purchase_orders (supplier_name);

CREATE TABLE IF NOT EXISTS purchase_requests (
	id SERIAL NOT NULL, 
	pr_number VARCHAR(40) NOT NULL, 
	department VARCHAR(120) NOT NULL, 
	item_code VARCHAR(120) NOT NULL, 
	item_name VARCHAR(255) NOT NULL, 
	spec VARCHAR(160) NOT NULL, 
	quantity INTEGER NOT NULL, 
	unit VARCHAR(40) NOT NULL, 
	request_date DATE NOT NULL, 
	reply_due_date DATE, 
	desired_due_date DATE, 
	delivery_place VARCHAR(160) NOT NULL, 
	request_notes VARCHAR(500) NOT NULL, 
	requester VARCHAR(120) NOT NULL, 
	approver VARCHAR(120) NOT NULL, 
	approval_status VARCHAR(40) NOT NULL, 
	source_type VARCHAR(40) NOT NULL, 
	linked_po_number VARCHAR(40) NOT NULL, 
	memo VARCHAR(500) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_purchase_requests_pr_number UNIQUE (pr_number)
);

CREATE INDEX IF NOT EXISTS ix_purchase_requests_id ON purchase_requests (id);
CREATE INDEX IF NOT EXISTS ix_purchase_requests_item_name ON purchase_requests (item_name);
CREATE INDEX IF NOT EXISTS ix_purchase_requests_pr_number ON purchase_requests (pr_number);
CREATE INDEX IF NOT EXISTS ix_purchase_requests_request_date ON purchase_requests (request_date);

CREATE TABLE IF NOT EXISTS rfq_quotes (
	id SERIAL NOT NULL, 
	pr_number VARCHAR(40) NOT NULL, 
	quote_number VARCHAR(40) NOT NULL, 
	item_name VARCHAR(255) NOT NULL, 
	supplier_name VARCHAR(160) NOT NULL, 
	supplier_manager VARCHAR(120) NOT NULL, 
	supplier_phone VARCHAR(80) NOT NULL, 
	supplier_email VARCHAR(160) NOT NULL, 
	unit_price FLOAT NOT NULL, 
	currency VARCHAR(10) NOT NULL, 
	moq INTEGER NOT NULL, 
	lead_time_days INTEGER NOT NULL, 
	shipping_fee INTEGER NOT NULL, 
	payment_terms VARCHAR(120) NOT NULL, 
	quote_valid_until DATE, 
	memo VARCHAR(500) NOT NULL, 
	is_recommended BOOLEAN NOT NULL, 
	is_selected BOOLEAN NOT NULL, 
	selection_reason VARCHAR(500) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_rfq_quotes_id ON rfq_quotes (id);
CREATE INDEX IF NOT EXISTS ix_rfq_quotes_item_name ON rfq_quotes (item_name);
CREATE INDEX IF NOT EXISTS ix_rfq_quotes_pr_number ON rfq_quotes (pr_number);
CREATE INDEX IF NOT EXISTS ix_rfq_quotes_quote_number ON rfq_quotes (quote_number);
CREATE INDEX IF NOT EXISTS ix_rfq_quotes_supplier_name ON rfq_quotes (supplier_name);

CREATE TABLE IF NOT EXISTS supplier_approval_history (
	id SERIAL NOT NULL, 
	evaluation_id INTEGER NOT NULL, 
	supplier_id INTEGER NOT NULL, 
	action_type VARCHAR(40) NOT NULL, 
	status_from VARCHAR(40) NOT NULL, 
	status_to VARCHAR(40) NOT NULL, 
	reason TEXT NOT NULL, 
	actor VARCHAR(120) NOT NULL, 
	acted_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_supplier_approval_history_acted_at ON supplier_approval_history (acted_at);
CREATE INDEX IF NOT EXISTS ix_supplier_approval_history_action_type ON supplier_approval_history (action_type);
CREATE INDEX IF NOT EXISTS ix_supplier_approval_history_evaluation_id ON supplier_approval_history (evaluation_id);
CREATE INDEX IF NOT EXISTS ix_supplier_approval_history_id ON supplier_approval_history (id);
CREATE INDEX IF NOT EXISTS ix_supplier_approval_history_supplier_id ON supplier_approval_history (supplier_id);

CREATE TABLE IF NOT EXISTS supplier_evaluation_categories (
	id SERIAL NOT NULL, 
	criteria_version VARCHAR(40) NOT NULL, 
	category_order INTEGER NOT NULL, 
	category_name VARCHAR(120) NOT NULL, 
	category_weight FLOAT NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_categories_category_name ON supplier_evaluation_categories (category_name);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_categories_criteria_version ON supplier_evaluation_categories (criteria_version);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_categories_id ON supplier_evaluation_categories (id);

CREATE TABLE IF NOT EXISTS supplier_evaluation_criteria (
	id SERIAL NOT NULL, 
	criteria_version VARCHAR(40) NOT NULL, 
	category_order INTEGER NOT NULL, 
	category_name VARCHAR(120) NOT NULL, 
	category_weight FLOAT NOT NULL, 
	item_order INTEGER NOT NULL, 
	item_name VARCHAR(160) NOT NULL, 
	item_weight FLOAT NOT NULL, 
	item_description VARCHAR(500) NOT NULL, 
	is_required BOOLEAN NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_criteria_category_name ON supplier_evaluation_criteria (category_name);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_criteria_criteria_version ON supplier_evaluation_criteria (criteria_version);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_criteria_id ON supplier_evaluation_criteria (id);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_criteria_item_name ON supplier_evaluation_criteria (item_name);

CREATE TABLE IF NOT EXISTS supplier_evaluation_criteria_versions (
	id SERIAL NOT NULL, 
	version_code VARCHAR(40) NOT NULL, 
	version_name VARCHAR(120) NOT NULL, 
	status VARCHAR(40) NOT NULL, 
	note VARCHAR(500) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_by VARCHAR(120) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_criteria_versions_id ON supplier_evaluation_criteria_versions (id);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_criteria_versions_status ON supplier_evaluation_criteria_versions (status);
CREATE UNIQUE INDEX IF NOT EXISTS ix_supplier_evaluation_criteria_versions_version_code ON supplier_evaluation_criteria_versions (version_code);

CREATE TABLE IF NOT EXISTS supplier_evaluation_history (
	id SERIAL NOT NULL, 
	evaluation_id INTEGER NOT NULL, 
	supplier_id INTEGER NOT NULL, 
	action_type VARCHAR(40) NOT NULL, 
	before_data TEXT NOT NULL, 
	after_data TEXT NOT NULL, 
	changed_by VARCHAR(120) NOT NULL, 
	change_reason VARCHAR(500) NOT NULL, 
	changed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_history_action_type ON supplier_evaluation_history (action_type);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_history_changed_at ON supplier_evaluation_history (changed_at);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_history_evaluation_id ON supplier_evaluation_history (evaluation_id);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_history_id ON supplier_evaluation_history (id);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_history_supplier_id ON supplier_evaluation_history (supplier_id);

CREATE TABLE IF NOT EXISTS supplier_evaluation_items (
	id SERIAL NOT NULL, 
	evaluation_id INTEGER NOT NULL, 
	category_id INTEGER NOT NULL, 
	item_id INTEGER NOT NULL, 
	category_name VARCHAR(120) NOT NULL, 
	item_name VARCHAR(160) NOT NULL, 
	selected_rating VARCHAR(40) NOT NULL, 
	item_score FLOAT NOT NULL, 
	item_weight FLOAT NOT NULL, 
	not_applicable BOOLEAN NOT NULL, 
	comment VARCHAR(500) NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_items_evaluation_id ON supplier_evaluation_items (evaluation_id);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluation_items_id ON supplier_evaluation_items (id);

CREATE TABLE IF NOT EXISTS supplier_evaluations (
	id SERIAL NOT NULL, 
	supplier_id INTEGER NOT NULL, 
	evaluation_year INTEGER NOT NULL, 
	evaluation_quarter VARCHAR(10) NOT NULL, 
	period_start DATE, 
	period_end DATE, 
	evaluation_date DATE NOT NULL, 
	evaluator VARCHAR(120) NOT NULL, 
	next_evaluation_date DATE, 
	status VARCHAR(40) NOT NULL, 
	quality_score FLOAT NOT NULL, 
	delivery_score FLOAT NOT NULL, 
	price_score FLOAT NOT NULL, 
	service_score FLOAT NOT NULL, 
	stability_score FLOAT NOT NULL, 
	applicable_weight FLOAT NOT NULL, 
	earned_score FLOAT NOT NULL, 
	total_score FLOAT NOT NULL, 
	base_grade VARCHAR(20) NOT NULL, 
	final_grade VARCHAR(20) NOT NULL, 
	grade_limit_reason VARCHAR(500) NOT NULL, 
	previous_grade VARCHAR(20) NOT NULL, 
	special_flags TEXT NOT NULL, 
	special_reasons TEXT NOT NULL, 
	special_warning BOOLEAN NOT NULL, 
	overall_comment TEXT NOT NULL, 
	excellent_points TEXT NOT NULL, 
	problem_points TEXT NOT NULL, 
	improvement_request TEXT NOT NULL, 
	improvement_owner VARCHAR(120) NOT NULL, 
	improvement_due_date DATE, 
	improvement_status VARCHAR(40) NOT NULL, 
	attachment_ref VARCHAR(500) NOT NULL, 
	internal_memo TEXT NOT NULL, 
	rejection_reason TEXT NOT NULL, 
	criteria_version VARCHAR(40) NOT NULL, 
	is_deleted BOOLEAN NOT NULL, 
	inactive_reason TEXT NOT NULL, 
	inactive_at TIMESTAMP WITHOUT TIME ZONE, 
	created_by VARCHAR(120) NOT NULL, 
	updated_by VARCHAR(120) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_supplier_evaluations_base_grade ON supplier_evaluations (base_grade);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluations_evaluation_date ON supplier_evaluations (evaluation_date);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluations_evaluation_quarter ON supplier_evaluations (evaluation_quarter);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluations_evaluation_year ON supplier_evaluations (evaluation_year);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluations_evaluator ON supplier_evaluations (evaluator);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluations_final_grade ON supplier_evaluations (final_grade);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluations_id ON supplier_evaluations (id);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluations_improvement_status ON supplier_evaluations (improvement_status);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluations_status ON supplier_evaluations (status);
CREATE INDEX IF NOT EXISTS ix_supplier_evaluations_supplier_id ON supplier_evaluations (supplier_id);

CREATE TABLE IF NOT EXISTS supplier_grade_rules (
	id SERIAL NOT NULL, 
	grade VARCHAR(20) NOT NULL, 
	minimum_score FLOAT NOT NULL, 
	maximum_score FLOAT NOT NULL, 
	label VARCHAR(80) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	auto_downgrade_enabled BOOLEAN NOT NULL, 
	major_quality_max_grade VARCHAR(20) NOT NULL, 
	contract_violation_max_grade VARCHAR(20) NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_supplier_grade_rules_grade ON supplier_grade_rules (grade);
CREATE INDEX IF NOT EXISTS ix_supplier_grade_rules_id ON supplier_grade_rules (id);

CREATE TABLE IF NOT EXISTS supplier_special_rules (
	id SERIAL NOT NULL, 
	flag_name VARCHAR(120) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	show_warning BOOLEAN NOT NULL, 
	reason_required BOOLEAN NOT NULL, 
	grade_limit_enabled BOOLEAN NOT NULL, 
	max_grade VARCHAR(20) NOT NULL, 
	reflect_to_supplier BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_supplier_special_rules_flag_name ON supplier_special_rules (flag_name);
CREATE INDEX IF NOT EXISTS ix_supplier_special_rules_id ON supplier_special_rules (id);

CREATE TABLE IF NOT EXISTS suppliers (
	id SERIAL NOT NULL, 
	supplier_code VARCHAR(40) NOT NULL, 
	supplier_name VARCHAR(160) NOT NULL, 
	business_number VARCHAR(80) NOT NULL, 
	handled_items VARCHAR(500) NOT NULL, 
	moq_terms VARCHAR(500) NOT NULL, 
	manager VARCHAR(120) NOT NULL, 
	phone VARCHAR(80) NOT NULL, 
	email VARCHAR(160) NOT NULL, 
	transaction_status VARCHAR(40) NOT NULL, 
	current_grade VARCHAR(20) NOT NULL, 
	latest_score FLOAT NOT NULL, 
	latest_evaluation_date DATE, 
	next_evaluation_date DATE, 
	special_management BOOLEAN NOT NULL, 
	special_reason VARCHAR(500) NOT NULL, 
	avg_lead_time_days INTEGER NOT NULL, 
	avg_unit_price FLOAT NOT NULL, 
	avg_unit_price_currency VARCHAR(10) NOT NULL, 
	payment_terms VARCHAR(120) NOT NULL, 
	rating VARCHAR(40) NOT NULL, 
	memo VARCHAR(500) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_suppliers_supplier_name UNIQUE (supplier_name)
);

CREATE INDEX IF NOT EXISTS ix_suppliers_current_grade ON suppliers (current_grade);
CREATE INDEX IF NOT EXISTS ix_suppliers_id ON suppliers (id);
CREATE INDEX IF NOT EXISTS ix_suppliers_supplier_code ON suppliers (supplier_code);
CREATE INDEX IF NOT EXISTS ix_suppliers_supplier_name ON suppliers (supplier_name);
CREATE INDEX IF NOT EXISTS ix_suppliers_transaction_status ON suppliers (transaction_status);

CREATE TABLE IF NOT EXISTS thirdparty_product_master (
	id SERIAL NOT NULL, 
	sku VARCHAR(120) NOT NULL, 
	barcode VARCHAR(120) NOT NULL, 
	product_name VARCHAR(255) NOT NULL, 
	large_category VARCHAR(120) NOT NULL, 
	medium_category VARCHAR(120) NOT NULL, 
	small_category VARCHAR(120) NOT NULL, 
	brand VARCHAR(120) NOT NULL, 
	supplier VARCHAR(160) NOT NULL, 
	pack_qty INTEGER NOT NULL, 
	box_qty INTEGER NOT NULL, 
	default_lead_time INTEGER NOT NULL, 
	min_stock INTEGER NOT NULL, 
	sort_order INTEGER NOT NULL, 
	location_registered BOOLEAN DEFAULT false NOT NULL,
	is_active VARCHAR(20) NOT NULL, 
	memo VARCHAR(500) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_thirdparty_product_master_sku UNIQUE (sku), 
	CONSTRAINT uq_thirdparty_product_master_product_name UNIQUE (product_name), 
	CONSTRAINT ck_thirdparty_product_master_is_active CHECK (is_active IN ('사용', '미사용'))
);

CREATE INDEX IF NOT EXISTS ix_thirdparty_product_master_barcode ON thirdparty_product_master (barcode);
CREATE INDEX IF NOT EXISTS ix_thirdparty_product_master_id ON thirdparty_product_master (id);
CREATE INDEX IF NOT EXISTS ix_thirdparty_product_master_product_name ON thirdparty_product_master (product_name);
CREATE INDEX IF NOT EXISTS ix_thirdparty_product_master_sku ON thirdparty_product_master (sku);

CREATE TABLE IF NOT EXISTS warehouse_layouts (
	id VARCHAR(36) DEFAULT gen_random_uuid()::text NOT NULL, 
	building VARCHAR(120) NOT NULL, 
	floor VARCHAR(40) NOT NULL, 
	layout_data JSON NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_warehouse_layouts_building_floor UNIQUE (building, floor)
);

CREATE INDEX IF NOT EXISTS ix_warehouse_layouts_building ON warehouse_layouts (building);
CREATE INDEX IF NOT EXISTS ix_warehouse_layouts_floor ON warehouse_layouts (floor);
CREATE INDEX IF NOT EXISTS ix_warehouse_layouts_id ON warehouse_layouts (id);
CREATE INDEX IF NOT EXISTS ix_warehouse_layouts_is_active ON warehouse_layouts (is_active);

CREATE TABLE IF NOT EXISTS warehouse_product_master (
	id SERIAL NOT NULL, 
	sku VARCHAR(120) NOT NULL, 
	barcode VARCHAR(120) NOT NULL, 
	product_name VARCHAR(255) NOT NULL, 
	large_category VARCHAR(120) NOT NULL, 
	medium_category VARCHAR(120) NOT NULL, 
	small_category VARCHAR(120) NOT NULL, 
	brand VARCHAR(120) NOT NULL, 
	supplier VARCHAR(160) NOT NULL, 
	pack_qty INTEGER NOT NULL, 
	box_qty INTEGER NOT NULL, 
	default_lead_time INTEGER NOT NULL, 
	min_stock INTEGER NOT NULL, 
	sort_order INTEGER NOT NULL, 
	location_registered BOOLEAN DEFAULT false NOT NULL,
	is_active VARCHAR(20) NOT NULL, 
	memo VARCHAR(500) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_warehouse_product_master_sku UNIQUE (sku), 
	CONSTRAINT uq_warehouse_product_master_product_name UNIQUE (product_name), 
	CONSTRAINT ck_warehouse_product_master_is_active CHECK (is_active IN ('사용', '미사용'))
);

CREATE INDEX IF NOT EXISTS ix_warehouse_product_master_barcode ON warehouse_product_master (barcode);
CREATE INDEX IF NOT EXISTS ix_warehouse_product_master_id ON warehouse_product_master (id);
CREATE INDEX IF NOT EXISTS ix_warehouse_product_master_product_name ON warehouse_product_master (product_name);
CREATE INDEX IF NOT EXISTS ix_warehouse_product_master_sku ON warehouse_product_master (sku);

CREATE TABLE IF NOT EXISTS schedule_weeks (
	id SERIAL NOT NULL,
	week_start VARCHAR(20) NOT NULL,
	title TEXT NOT NULL,
	owner TEXT NOT NULL,
	comment TEXT NOT NULL,
	created_at VARCHAR(40) NOT NULL,
	updated_at VARCHAR(40) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_schedule_weeks_week_start UNIQUE (week_start)
);

CREATE INDEX IF NOT EXISTS ix_schedule_weeks_id ON schedule_weeks (id);
CREATE INDEX IF NOT EXISTS ix_schedule_weeks_week_start ON schedule_weeks (week_start);

CREATE TABLE IF NOT EXISTS schedule_highlights (
	id SERIAL NOT NULL,
	week_id INTEGER NOT NULL,
	sort_order INTEGER NOT NULL,
	title TEXT NOT NULL,
	checked INTEGER NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_schedule_highlights_id ON schedule_highlights (id);
CREATE INDEX IF NOT EXISTS ix_schedule_highlights_week_id ON schedule_highlights (week_id);

CREATE TABLE IF NOT EXISTS schedule_slots (
	id SERIAL NOT NULL,
	week_id INTEGER NOT NULL,
	sort_order INTEGER NOT NULL,
	time_label TEXT NOT NULL,
	mon TEXT NOT NULL,
	tue TEXT NOT NULL,
	wed TEXT NOT NULL,
	thu TEXT NOT NULL,
	fri TEXT NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_schedule_slots_id ON schedule_slots (id);
CREATE INDEX IF NOT EXISTS ix_schedule_slots_week_id ON schedule_slots (week_id);

CREATE TABLE IF NOT EXISTS meeting_reports (
	id SERIAL NOT NULL,
	meeting_date VARCHAR(20) NOT NULL,
	author TEXT NOT NULL,
	event_detail TEXT NOT NULL,
	issue_delay TEXT NOT NULL,
	issue_inventory TEXT NOT NULL,
	issue_special TEXT NOT NULL,
	created_at VARCHAR(40) NOT NULL,
	updated_at VARCHAR(40) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_meeting_reports_meeting_date UNIQUE (meeting_date)
);

CREATE INDEX IF NOT EXISTS ix_meeting_reports_id ON meeting_reports (id);
CREATE INDEX IF NOT EXISTS ix_meeting_reports_meeting_date ON meeting_reports (meeting_date);

CREATE TABLE IF NOT EXISTS meeting_meta (
	key VARCHAR(120) NOT NULL,
	value TEXT NOT NULL,
	PRIMARY KEY (key)
);

CREATE TABLE IF NOT EXISTS meeting_production_requests (
	id SERIAL NOT NULL,
	report_id INTEGER NOT NULL,
	sort_order INTEGER NOT NULL,
	production_code TEXT NOT NULL,
	product_name TEXT NOT NULL,
	current_qty INTEGER NOT NULL,
	request_qty INTEGER NOT NULL,
	due_date TEXT NOT NULL,
	status TEXT NOT NULL,
	memo TEXT NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_meeting_production_requests_id ON meeting_production_requests (id);
CREATE INDEX IF NOT EXISTS ix_meeting_production_requests_report_id ON meeting_production_requests (report_id);

CREATE TABLE IF NOT EXISTS meeting_events (
	id SERIAL NOT NULL,
	report_id INTEGER NOT NULL,
	sort_order INTEGER NOT NULL,
	event_name TEXT NOT NULL,
	period TEXT NOT NULL,
	affected_products TEXT NOT NULL,
	request_qty INTEGER NOT NULL,
	summary TEXT NOT NULL,
	owner TEXT NOT NULL,
	memo TEXT NOT NULL,
	event_month VARCHAR(20) NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_meeting_events_event_month ON meeting_events (event_month);
CREATE INDEX IF NOT EXISTS ix_meeting_events_id ON meeting_events (id);
CREATE INDEX IF NOT EXISTS ix_meeting_events_report_id ON meeting_events (report_id);
CREATE INDEX IF NOT EXISTS idx_meeting_events_event_month ON meeting_events(event_month, sort_order, id);

CREATE TABLE IF NOT EXISTS meeting_action_items (
	id SERIAL NOT NULL,
	report_id INTEGER NOT NULL,
	sort_order INTEGER NOT NULL,
	owner TEXT NOT NULL,
	content TEXT NOT NULL,
	quantity INTEGER NOT NULL,
	due_date TEXT NOT NULL,
	delivery_date TEXT NOT NULL,
	status TEXT NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_meeting_action_items_id ON meeting_action_items (id);
CREATE INDEX IF NOT EXISTS ix_meeting_action_items_report_id ON meeting_action_items (report_id);

CREATE TABLE IF NOT EXISTS cases (
	id SERIAL NOT NULL,
	case_id TEXT,
	category TEXT,
	barcode TEXT,
	product TEXT,
	cause TEXT,
	action TEXT,
	repair_method TEXT,
	prevention TEXT,
	product_image BYTEA,
	case_image BYTEA,
	case_image_original BYTEA,
	repair_image BYTEA,
	repair_image_original BYTEA,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_cases_barcode ON cases (barcode);
CREATE INDEX IF NOT EXISTS ix_cases_case_id ON cases (case_id);
CREATE INDEX IF NOT EXISTS ix_cases_category ON cases (category);
CREATE INDEX IF NOT EXISTS ix_cases_id ON cases (id);
CREATE INDEX IF NOT EXISTS ix_cases_product ON cases (product);

CREATE TABLE IF NOT EXISTS purchase_budget_stores (
	id SERIAL NOT NULL,
	store_key VARCHAR(80) NOT NULL,
	payload JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (store_key)
);

CREATE INDEX IF NOT EXISTS ix_purchase_budget_stores_id ON purchase_budget_stores (id);
CREATE INDEX IF NOT EXISTS ix_purchase_budget_stores_store_key ON purchase_budget_stores (store_key);

COMMIT;
