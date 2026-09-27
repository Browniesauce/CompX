CREATE TABLE IF NOT EXISTS manufacturers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (length(trim(name)) > 0)
);

CREATE TABLE IF NOT EXISTS device_families (
    id INTEGER PRIMARY KEY,
    manufacturer_id INTEGER NOT NULL REFERENCES manufacturers(id),
    device_type TEXT NOT NULL CHECK (device_type IN ('laptop', 'desktop')),
    product_family TEXT COLLATE NOCASE CHECK (product_family IS NULL OR length(trim(product_family)) > 0),
    model TEXT NOT NULL COLLATE NOCASE CHECK (length(trim(model)) > 0),
    UNIQUE (manufacturer_id, device_type, product_family, model)
);

CREATE TABLE IF NOT EXISTS configurations (
    id INTEGER PRIMARY KEY,
    family_id INTEGER NOT NULL REFERENCES device_families(id),
    configuration_key TEXT NOT NULL COLLATE NOCASE CHECK (length(trim(configuration_key)) > 0),
    sku TEXT,
    part_number TEXT,
    region TEXT,
    model_year INTEGER CHECK (model_year > 0),
    cpu TEXT,
    gpu TEXT,
    ram_gb INTEGER CHECK (ram_gb > 0),
    storage_gb INTEGER CHECK (storage_gb > 0),
    display_size_inches REAL CHECK (display_size_inches > 0),
    battery_wh REAL CHECK (battery_wh > 0),
    weight_g INTEGER CHECK (weight_g > 0),
    form_factor TEXT,
    power_supply_w INTEGER CHECK (power_supply_w > 0),
    UNIQUE (family_id, configuration_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS configurations_family_sku_unique
    ON configurations (family_id, sku COLLATE NOCASE)
    WHERE sku IS NOT NULL;

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    configuration_id INTEGER NOT NULL REFERENCES configurations(id),
    source_url TEXT NOT NULL CHECK (length(trim(source_url)) > 0),
    checked_on TEXT NOT NULL CHECK (checked_on GLOB '????-??-??'),
    source_name TEXT,
    UNIQUE (configuration_id, source_url, checked_on)
);

CREATE TABLE IF NOT EXISTS revisions (
    id INTEGER PRIMARY KEY,
    configuration_id INTEGER NOT NULL REFERENCES configurations(id),
    recorded_at TEXT NOT NULL,
    change_type TEXT NOT NULL CHECK (
        change_type IN ('initial', 'hardware', 'data_update', 'source_only')
    ),
    before_json TEXT,
    after_json TEXT NOT NULL,
    source_url TEXT,
    checked_on TEXT
);

CREATE INDEX IF NOT EXISTS revisions_configuration_order
    ON revisions (configuration_id, id);
