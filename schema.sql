CREATE TABLE IF NOT EXISTS tracked_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    target_price REAL,
    big_drop_percent REAL,
    notify_on_target INTEGER NOT NULL DEFAULT 1,
    notify_on_big_drop INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tracked_stocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    ticker TEXT NOT NULL,
    target_price REAL,
    daily_drop_percent REAL,
    daily_rise_percent REAL,
    notify_on_target INTEGER NOT NULL DEFAULT 1,
    notify_on_big_drop INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT,
    item_id INTEGER,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    alert_status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT,
    delivery_status TEXT NOT NULL DEFAULT 'unsent',
    delivery_error TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS price_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_name TEXT NOT NULL,
    source_url TEXT,
    current_price REAL,
    previous_price REAL,
    target_price REAL,
    status TEXT NOT NULL,
    message TEXT,
    FOREIGN KEY (product_id) REFERENCES tracked_products (id)
);

CREATE TABLE IF NOT EXISTS stock_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER NOT NULL,
    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticker TEXT NOT NULL,
    current_price REAL,
    previous_close REAL,
    target_price REAL,
    percent_change REAL,
    status TEXT NOT NULL,
    message TEXT,
    FOREIGN KEY (stock_id) REFERENCES tracked_stocks (id)
);
