import sqlite3

import pytest

from compx.database import connect, init_database


def test_connections_enforce_foreign_keys(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    init_database(path)

    with connect(path) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO device_families (manufacturer_id, device_type, model) "
                "VALUES (?, ?, ?)",
                (999, "laptop", "Synthetic Model"),
            )


def test_configuration_key_prevents_duplicate_exact_configurations(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    init_database(path)

    with connect(path) as connection:
        connection.execute("INSERT INTO manufacturers (name) VALUES (?)", ("Synthetic Test Co",))
        connection.execute(
            "INSERT INTO device_families (manufacturer_id, device_type, model) "
            "VALUES (?, ?, ?)",
            (1, "desktop", "Synthetic Model"),
        )
        connection.execute(
            "INSERT INTO configurations (family_id, configuration_key) VALUES (?, ?)",
            (1, "test-variant"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO configurations (family_id, configuration_key) VALUES (?, ?)",
                (1, "TEST-VARIANT"),
            )


def test_phase_one_schema_upgrade_preserves_existing_rows(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE manufacturers (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE
            );
            CREATE TABLE device_families (
                id INTEGER PRIMARY KEY,
                manufacturer_id INTEGER NOT NULL REFERENCES manufacturers(id),
                device_type TEXT NOT NULL,
                model TEXT NOT NULL COLLATE NOCASE,
                UNIQUE (manufacturer_id, device_type, model)
            );
            CREATE TABLE configurations (
                id INTEGER PRIMARY KEY,
                family_id INTEGER NOT NULL REFERENCES device_families(id),
                configuration_key TEXT NOT NULL COLLATE NOCASE,
                sku TEXT, part_number TEXT, region TEXT, cpu TEXT, gpu TEXT,
                ram_gb INTEGER, storage_gb INTEGER, display_size_inches REAL,
                battery_wh REAL, weight_g INTEGER, form_factor TEXT,
                power_supply_w INTEGER,
                UNIQUE (family_id, configuration_key)
            );
            CREATE TABLE sources (
                id INTEGER PRIMARY KEY,
                configuration_id INTEGER NOT NULL REFERENCES configurations(id),
                source_url TEXT NOT NULL, checked_on TEXT NOT NULL, source_name TEXT,
                UNIQUE (configuration_id, source_url, checked_on)
            );
            INSERT INTO manufacturers VALUES (7, 'Synthetic Legacy Co');
            INSERT INTO device_families VALUES (8, 7, 'laptop', 'Legacy Test Model');
            INSERT INTO configurations (id, family_id, configuration_key, sku)
                VALUES (9, 8, 'LEGACY-001', 'LEGACY-001');
            INSERT INTO sources (id, configuration_id, source_url, checked_on)
                VALUES (10, 9, 'https://example.test/legacy', '2026-09-27');
        """)

    init_database(path)
    init_database(path)

    with connect(path) as connection:
        assert connection.execute(
            "SELECT id, product_family, model FROM device_families"
        ).fetchone() == (8, None, "Legacy Test Model")
        assert connection.execute(
            "SELECT id, family_id, sku, model_year FROM configurations"
        ).fetchone() == (9, 8, "LEGACY-001", None)
        assert connection.execute(
            "SELECT id, configuration_id FROM sources"
        ).fetchone() == (10, 9)
        assert connection.execute(
            "SELECT configuration_id, change_type FROM revisions"
        ).fetchall() == [(9, "initial")]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
