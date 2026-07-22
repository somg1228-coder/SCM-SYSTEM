CREATE TABLE IF NOT EXISTS category_bom_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_name VARCHAR(160) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    item_name VARCHAR(255) NOT NULL,
    item_type VARCHAR(40) NOT NULL DEFAULT '부품',
    manager VARCHAR(120) NOT NULL DEFAULT '',
    vendor VARCHAR(160) NOT NULL DEFAULT '',
    required_stock INTEGER NOT NULL DEFAULT 1,
    barcode_spec VARCHAR(160) NOT NULL DEFAULT '',
    memo VARCHAR(500) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_category_bom_items_category_name
    ON category_bom_items (category_name);

CREATE INDEX IF NOT EXISTS ix_category_bom_items_item_name
    ON category_bom_items (item_name);
