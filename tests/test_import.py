"""CSV examples in these tests are synthetic, not real device specifications."""

import csv
import sqlite3

import pytest
from typer.testing import CliRunner

from compx.cli import app
from compx.csv_import import ALL_COLUMNS
from compx.database import init_database


runner = CliRunner()
HEADERS = [
    "manufacturer", "product_family", "model_name", "device_type", "sku",
    "source_url", "checked_on", "region", "model_year", "cpu", "gpu",
    "ram_gb", "storage_gb", "display_size_in", "battery_wh", "weight_g",
    "form_factor", "psu_watts",
]


def synthetic_row(**overrides):
    row = {
        "manufacturer": "Synthetic Test Co",
        "product_family": "Test Family",
        "model_name": "Test Model",
        "device_type": "laptop",
        "sku": "TEST-001",
        "source_url": "https://example.test/specs/one",
        "checked_on": "2026-09-27",
        "ram_gb": "16",
    }
    row.update(overrides)
    return row


def write_csv(path, rows, headers=HEADERS):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def invoke_import(csv_path, database_path, *options):
    return runner.invoke(app, ["import", str(csv_path), "--database", str(database_path), *options])


def table_count(path, table):
    with sqlite3.connect(path) as connection:
        return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_dry_run_validates_without_creating_database(tmp_path):
    csv_path = write_csv(tmp_path / "synthetic.csv", [synthetic_row()])
    database_path = tmp_path / "catalog.sqlite3"

    result = invoke_import(csv_path, database_path, "--dry-run")

    assert result.exit_code == 0
    assert "1/1 rows valid" in result.output
    assert "import would succeed" in result.output
    assert not database_path.exists()


def test_shipped_template_matches_importer_and_example_validates(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    with (root / "data" / "devices_template.csv").open(newline="", encoding="utf-8") as file:
        assert set(next(csv.reader(file))) == ALL_COLUMNS
    database_path = tmp_path / "catalog.sqlite3"

    result = invoke_import(root / "data" / "synthetic_example.csv", database_path, "--dry-run")

    assert result.exit_code == 0
    assert not database_path.exists()


def test_dry_run_does_not_change_existing_catalog(tmp_path):
    database_path = tmp_path / "catalog.sqlite3"
    init_database(database_path)
    csv_path = write_csv(tmp_path / "synthetic.csv", [synthetic_row()])

    result = invoke_import(csv_path, database_path, "--dry-run")

    assert result.exit_code == 0
    for table in ("manufacturers", "device_families", "configurations", "sources"):
        assert table_count(database_path, table) == 0


def test_import_saves_all_configurations_and_sources(tmp_path):
    rows = [synthetic_row(), synthetic_row(sku="TEST-002", source_url="https://example.test/two")]
    csv_path = write_csv(tmp_path / "synthetic.csv", rows)
    database_path = tmp_path / "catalog.sqlite3"

    result = invoke_import(csv_path, database_path)

    assert result.exit_code == 0
    assert "Imported 2 configurations and 2 sources" in result.output
    assert table_count(database_path, "manufacturers") == 1
    assert table_count(database_path, "device_families") == 1
    assert table_count(database_path, "configurations") == 2
    assert table_count(database_path, "sources") == 2


def test_same_model_name_can_belong_to_different_product_families(tmp_path):
    csv_path = write_csv(tmp_path / "synthetic.csv", [
        synthetic_row(),
        synthetic_row(product_family="Another Test Family", sku="TEST-002"),
    ])
    database_path = tmp_path / "catalog.sqlite3"

    result = invoke_import(csv_path, database_path)

    assert result.exit_code == 0
    assert table_count(database_path, "device_families") == 2


def test_unicode_case_variants_reuse_manufacturer_and_family(tmp_path):
    csv_path = write_csv(tmp_path / "synthetic.csv", [
        synthetic_row(
            manufacturer="Synthetic Ålpha", product_family="Mödel Line",
            model_name="Démo", sku="TEST-001",
        ),
        synthetic_row(
            manufacturer="SYNTHETIC ÅLPHA", product_family="MÖDEL LINE",
            model_name="DÉMO", sku="TEST-002",
            source_url="https://example.test/specs/two",
        ),
    ])
    database_path = tmp_path / "catalog.sqlite3"

    result = invoke_import(csv_path, database_path)

    assert result.exit_code == 0
    assert table_count(database_path, "manufacturers") == 1
    assert table_count(database_path, "device_families") == 1
    assert table_count(database_path, "configurations") == 2


def test_optional_numeric_fields_use_documented_units_and_columns(tmp_path):
    csv_path = write_csv(tmp_path / "synthetic.csv", [
        synthetic_row(model_year="2026", display_size_in="15.6", battery_wh="60.5", weight_g="1200"),
        synthetic_row(
            sku="TEST-002", device_type="desktop", product_family="Desktop Test Family",
            model_name="Desktop Test Model", storage_gb="1000", form_factor="tower",
            psu_watts="500", source_url="https://example.test/specs/two",
        ),
    ])
    database_path = tmp_path / "catalog.sqlite3"

    result = invoke_import(csv_path, database_path)

    assert result.exit_code == 0
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("""
            SELECT sku, model_year, display_size_inches, battery_wh, weight_g,
                   storage_gb, form_factor, power_supply_w
            FROM configurations ORDER BY sku
        """).fetchall()
    assert rows == [
        ("TEST-001", 2026, 15.6, 60.5, 1200, None, None, None),
        ("TEST-002", None, None, None, None, 1000, "tower", 500),
    ]


@pytest.mark.parametrize("overrides, expected", [
    ({"device_type": "tablet"}, "device_type must be laptop or desktop"),
    ({"ram_gb": "0"}, "ram_gb must be a positive integer"),
    ({"storage_gb": "1.5"}, "storage_gb must be a positive integer"),
    ({"battery_wh": "nan"}, "battery_wh must be a positive number"),
    ({"checked_on": "2026-02-30"}, "checked_on must be a real date"),
    ({"source_url": "ftp://example.test/specs"}, "source_url must be a valid HTTP(S) URL"),
    ({"manufacturer": "  "}, "manufacturer is required"),
])
def test_invalid_values_report_row_numbers(tmp_path, overrides, expected):
    csv_path = write_csv(tmp_path / "synthetic.csv", [synthetic_row(**overrides)])
    database_path = tmp_path / "catalog.sqlite3"

    result = invoke_import(csv_path, database_path, "--dry-run")

    assert result.exit_code == 1
    assert f"row 2: {expected}" in result.output
    assert "import blocked" in result.output
    assert not database_path.exists()


def test_missing_and_unexpected_headers_are_reported(tmp_path):
    headers = [name for name in HEADERS if name != "sku"] + ["ram_gbs"]
    csv_path = write_csv(tmp_path / "synthetic.csv", [synthetic_row()], headers)

    result = invoke_import(csv_path, tmp_path / "catalog.sqlite3", "--dry-run")

    assert result.exit_code == 1
    assert "row 1: missing required columns: sku" in result.output
    assert "row 1: unexpected columns: 'ram_gbs'" in result.output


def test_blank_optional_cells_become_null_and_whitespace_is_trimmed(tmp_path):
    csv_path = write_csv(tmp_path / "synthetic.csv", [synthetic_row(
        manufacturer=" Synthetic Test Co ", sku=" TEST-001 ",
        region="  ", cpu=" ", ram_gb=" ",
    )])
    database_path = tmp_path / "catalog.sqlite3"

    result = invoke_import(csv_path, database_path)

    assert result.exit_code == 0
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT name FROM manufacturers").fetchone()[0] == "Synthetic Test Co"
        assert connection.execute(
            "SELECT sku, region, cpu, ram_gb FROM configurations"
        ).fetchone() == ("TEST-001", None, None, None)


def test_duplicate_rows_in_file_block_entire_import(tmp_path):
    csv_path = write_csv(tmp_path / "synthetic.csv", [
        synthetic_row(), synthetic_row(sku="test-001", model_name="Another Test Model"),
    ])
    database_path = tmp_path / "catalog.sqlite3"

    result = invoke_import(csv_path, database_path)

    assert result.exit_code == 1
    assert "row 3: duplicate manufacturer + SKU (first at row 2)" in result.output
    assert not database_path.exists()


def test_existing_configuration_is_reported_and_not_overwritten(tmp_path):
    database_path = tmp_path / "catalog.sqlite3"
    original = write_csv(tmp_path / "original.csv", [synthetic_row()])
    assert invoke_import(original, database_path).exit_code == 0
    changed = write_csv(tmp_path / "changed.csv", [synthetic_row(
        ram_gb="32", source_url="https://example.test/changed",
    )])

    result = invoke_import(changed, database_path)

    assert result.exit_code == 1
    assert "row 2: Synthetic Test Co SKU TEST-001 already exists" in result.output
    assert table_count(database_path, "configurations") == 1
    assert table_count(database_path, "sources") == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT ram_gb FROM configurations").fetchone()[0] == 16


def test_invalid_row_prevents_all_writes(tmp_path):
    csv_path = write_csv(tmp_path / "synthetic.csv", [
        synthetic_row(), synthetic_row(sku="TEST-002", checked_on="bad"),
    ])
    database_path = tmp_path / "catalog.sqlite3"
    init_database(database_path)

    result = invoke_import(csv_path, database_path)

    assert result.exit_code == 1
    assert "row 3: checked_on" in result.output
    assert table_count(database_path, "manufacturers") == 0
    assert table_count(database_path, "configurations") == 0


def test_database_failure_rolls_back_all_rows(tmp_path):
    database_path = tmp_path / "catalog.sqlite3"
    init_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("""
            CREATE TRIGGER reject_synthetic_source BEFORE INSERT ON sources
            WHEN NEW.source_url = 'https://example.test/fail'
            BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END
        """)
    csv_path = write_csv(tmp_path / "synthetic.csv", [
        synthetic_row(),
        synthetic_row(sku="TEST-002", source_url="https://example.test/fail"),
    ])

    result = invoke_import(csv_path, database_path)

    assert result.exit_code == 1
    assert "Import failed; no configurations saved" in result.output
    assert "synthetic write failure" in result.output
    for table in ("manufacturers", "device_families", "configurations", "sources"):
        assert table_count(database_path, table) == 0


def test_reimport_does_not_create_duplicates(tmp_path):
    csv_path = write_csv(tmp_path / "synthetic.csv", [synthetic_row()])
    database_path = tmp_path / "catalog.sqlite3"
    assert invoke_import(csv_path, database_path).exit_code == 0

    result = invoke_import(csv_path, database_path)

    assert result.exit_code == 1
    assert "already exists" in result.output
    assert table_count(database_path, "configurations") == 1
    assert table_count(database_path, "sources") == 1
